"""
LidarMapper node — processamento vetorizado (§5.0 do guia).

O node opera em arrays numpy do início ao fim: o LidarReader entrega
(N, 3) [angle_deg, distance_mm, quality] em cada `drain()`; este módulo
aplica filtros, projeção polar→cartesiano e ROI como máscaras vetorizadas
antes de criar qualquer objeto Python. `BackgroundSubtractor` também opera
sobre arrays (fase de baseline por bin angular, filtro por comparação
elementar depois).

A meta do §10 é parsing+filtros ≤ 30% de um core no 3B+ — o que exige
que TODO o hot-path fique em numpy.
"""
from __future__ import annotations

import time

import numpy as np

from config import ProcessingCfg, ROICfg


def project_and_filter(arr: np.ndarray, cfg: ProcessingCfg) -> np.ndarray:
    """`arr` (N, 3) → (M, 2) [x_mm, y_mm] filtrado por quality+dist e
    projetado polar→cartesiano em uma varredura vetorizada.

    - descarta amostras com distance <= 0
    - descarta amostras fora de [min_dist_mm, max_dist_mm]
    - descarta amostras com quality < min_quality
    - aplica `angle_offset_deg` antes do seno/cosseno
    - inverte x se `mirror`
    """
    if arr.size == 0:
        return np.zeros((0, 2), dtype=np.float32)
    angle = arr[:, 0]
    dist = arr[:, 1]
    q = arr[:, 2]
    mask = ((dist > 0) &
            (dist >= cfg.min_dist_mm) &
            (dist <= cfg.max_dist_mm) &
            (q >= cfg.min_quality))
    if not mask.any():
        return np.zeros((0, 2), dtype=np.float32)
    a = np.deg2rad(angle[mask] + cfg.angle_offset_deg)
    d = dist[mask]
    x = d * np.cos(a)
    y = d * np.sin(a)
    if cfg.mirror:
        x = -x
    return np.column_stack([x, y]).astype(np.float32)


def in_roi_mask(xy: np.ndarray, roi: ROICfg) -> np.ndarray:
    """Máscara bool de shape (N,) para pontos dentro da ROI. Limites
    `None` no ROICfg desativam aquele lado."""
    if xy.size == 0:
        return np.zeros(0, dtype=bool)
    x = xy[:, 0]
    y = xy[:, 1]
    m = np.ones(len(xy), dtype=bool)
    if roi.x_min is not None:
        m &= x >= roi.x_min
    if roi.x_max is not None:
        m &= x <= roi.x_max
    if roi.y_min is not None:
        m &= y >= roi.y_min
    if roi.y_max is not None:
        m &= y <= roi.y_max
    return m


class BackgroundSubtractor:
    """Aprende o fundo estático (distância mediana por bin angular) e
    filtra pontos mais perto que o fundo — esses são "objeto"/foreground.

    Versão vetorizada do legacy/processing.py: `feed` e `foreground_mask`
    aceitam arrays (N, 3). Só o loop de agregação por bin durante o
    baseline (2 s no start) permanece em Python — cap de 60 amostras/bin
    evita crescimento sem limite.
    """

    def __init__(self, bins: int = 720, margin_mm: float = 120):
        self.bins = bins
        self.margin = margin_mm
        self._bg = np.full(bins, np.nan, dtype=np.float32)
        self._samples: list[list[float]] | None = None
        self._until = 0.0
        self.ready = False

    def begin_baseline(self, duration_s: float | None = None) -> None:
        self._samples = [[] for _ in range(self.bins)]
        self._bg[:] = np.nan
        self.ready = False
        self._until = time.monotonic() + (duration_s if duration_s is not None else 2.0)

    def configure_time(self, baseline_time_s: float) -> None:
        self._until = time.monotonic() + baseline_time_s

    def feed(self, arr: np.ndarray) -> None:
        """`arr` (N, 3) [angle_deg, distance_mm, quality]. Só amostras
        com distance > 0 são consideradas."""
        if self._samples is None or arr.size == 0:
            return
        angle = arr[:, 0]
        dist = arr[:, 1]
        good = dist > 0
        if not good.any():
            self._maybe_finish()
            return
        b = ((angle[good] / 360.0 * self.bins).astype(np.int32)) % self.bins
        d = dist[good]
        for bi, di in zip(b, d):
            bucket = self._samples[bi]
            if len(bucket) < 60:
                bucket.append(float(di))
        self._maybe_finish()

    def _maybe_finish(self) -> None:
        if time.monotonic() >= self._until:
            self._finish()

    def _finish(self) -> None:
        assert self._samples is not None
        for i, s in enumerate(self._samples):
            if s:
                self._bg[i] = float(np.median(s))
        self._samples = None
        self.ready = True

    def foreground_mask(self, arr: np.ndarray) -> np.ndarray:
        """Máscara bool de shape (N,). True se a amostra é "foreground"
        (mais perto que o fundo daquele bin). Bins ainda não aprendidos
        (NaN) deixam a amostra passar como foreground."""
        if not self.ready or arr.size == 0:
            return np.zeros(len(arr), dtype=bool)
        angle = arr[:, 0]
        dist = arr[:, 1]
        b = ((angle / 360.0 * self.bins).astype(np.int32)) % self.bins
        bg = self._bg[b]
        finite = np.isfinite(bg)
        return np.where(finite, dist < bg - self.margin, True)

    @property
    def learned_bins(self) -> int:
        return int(np.isfinite(self._bg).sum())


def dbscan(points: np.ndarray, eps: float, min_samples: int) -> np.ndarray:
    """DBSCAN básico. `points` (N, 2). Retorna labels (N,):
    -1=ruído, 0..K-1=cluster. Para N típico do foreground (dezenas a
    poucas centenas), a matriz de distâncias N×N é rápida e simples;
    trocar por KDTree se N > 2000 (§ do legacy)."""
    n = len(points)
    if n == 0:
        return np.zeros(0, dtype=np.int32)
    diff = points[:, None, :] - points[None, :, :]
    d2 = np.einsum("ijk,ijk->ij", diff, diff)
    eps2 = eps * eps
    neighbors = [np.flatnonzero(d2[i] <= eps2) for i in range(n)]
    is_core = np.array([len(neighbors[i]) >= min_samples for i in range(n)])
    labels = np.full(n, -1, dtype=np.int32)
    cluster = 0
    for i in range(n):
        if labels[i] != -1 or not is_core[i]:
            continue
        stack = [i]
        labels[i] = cluster
        while stack:
            j = stack.pop()
            for k in neighbors[j]:
                if labels[k] == -1:
                    labels[k] = cluster
                    if is_core[k]:
                        stack.append(k)
        cluster += 1
    return labels


def dbscan_centroids(points: np.ndarray, eps: float,
                     min_samples: int) -> list[tuple[tuple[float, float], int]]:
    """DBSCAN + centróides. Retorna [((cx, cy), count), ...] ordenado
    por count desc."""
    labels = dbscan(points, eps, min_samples)
    out = []
    for lab in sorted(set(labels.tolist()) - {-1}):
        m = labels == lab
        cx, cy = points[m].mean(axis=0)
        out.append(((float(cx), float(cy)), int(m.sum())))
    out.sort(key=lambda kv: kv[1], reverse=True)
    return out
