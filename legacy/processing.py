"""
LidarMapper — processamento (Etapas 2-4).

Funções puras. Não dependem de pygame, ZMQ nem do sensor. Recebem listas
de Measurement (do lidar_reader.py) e devolvem listas de pontos
cartesianos prontos pra visualização / tracking / publicação.

Inclui:
  - polar→cartesiano + filtros (Etapa 2)
  - ROI bbox (Etapa 2)
  - BackgroundSubtractor por bin angular (Etapa 3, robustiza calibração)
  - cluster_greedy (Etapa 3, escolher canto entre vários objetos visíveis)
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass

import numpy as np

from config import ProcessingCfg, ROICfg
from lidar_reader import Measurement


@dataclass
class Point2D:
    x: float
    y: float
    quality: int
    angle: float
    distance: float


def polar_to_xy(angle_deg: float, distance_mm: float, offset_deg: float = 0,
                mirror: bool = False) -> tuple[float, float]:
    """Converte uma medida polar do LIDAR em (x, y) cartesiano em mm.

    Convenção: angle=0° aponta no sentido +x. `offset_deg` é somado antes
    do seno/cosseno, equivalente a rotacionar o sensor virtualmente. Com
    `mirror=True`, o eixo X é invertido (útil quando a montagem fica em
    espelho relativo ao espaço a ser interagido)."""
    a = math.radians(angle_deg + offset_deg)
    x = distance_mm * math.cos(a)
    y = distance_mm * math.sin(a)
    if mirror:
        x = -x
    return (x, y)


def project_batch(measurements: list[Measurement], cfg: ProcessingCfg) -> list[Point2D]:
    """Filtra (range + qualidade) e projeta para cartesiano em lote.

    Mantém `angle`/`distance` originais em cada Point2D pra facilitar
    debug e operações que ainda preferem polar (ex.: background subtraction
    por bin angular, na Etapa 4)."""
    out = []
    off = cfg.angle_offset_deg
    mir = cfg.mirror
    qmin = cfg.min_quality
    dmin = cfg.min_dist_mm
    dmax = cfg.max_dist_mm
    for m in measurements:
        if m.distance <= 0 or m.distance < dmin or m.distance > dmax:
            continue
        if m.quality < qmin:
            continue
        x, y = polar_to_xy(m.angle, m.distance, off, mir)
        out.append(Point2D(x=x, y=y, quality=m.quality, angle=m.angle,
                           distance=m.distance))
    return out


def in_roi(p: Point2D, roi: ROICfg) -> bool:
    """True se `p` está dentro da ROI. Limites `None` no ROICfg = livres."""
    if roi.x_min is not None and p.x < roi.x_min:
        return False
    if roi.x_max is not None and p.x > roi.x_max:
        return False
    if roi.y_min is not None and p.y < roi.y_min:
        return False
    if roi.y_max is not None and p.y > roi.y_max:
        return False
    return True


def split_by_roi(points: list[Point2D], roi: ROICfg) -> tuple[list[Point2D], list[Point2D]]:
    """Particiona pontos em (dentro, fora) da ROI."""
    inside = []
    outside = []
    for p in points:
        (inside if in_roi(p, roi) else outside).append(p)
    return (inside, outside)


class BackgroundSubtractor:
    """Aprende o fundo estático (distância por bin angular) e filtra pontos
    que estão MAIS PERTO que o fundo — esses são "objeto" / foreground.

    Bins angulares: discretiza 0..360° em N bins (default 720 = 0,5°/bin).
    Pra cada bin, armazena a distância mediana do fundo. Pontos com
    `distance < bg[bin] - margin` viram foreground. Bins não aprendidos
    (NaN) deixam o ponto passar como foreground (sem fundo conhecido).
    """

    def __init__(self, bins: int = 720, baseline_time_s: float = 2, margin_mm: float = 120):
        self.bins = bins
        self.margin = margin_mm
        self._bg = np.full(bins, np.nan)
        self._samples = None
        self._until = 0
        self.ready = False

    def begin_baseline(self) -> None:
        """Inicia a fase de captura do fundo. Mantenha a área livre."""
        self._samples = [[] for _ in range(self.bins)]
        self._bg[:] = np.nan
        self.ready = False
        self._until = time.monotonic() + 2

    def configure_time(self, baseline_time_s: float) -> None:
        self._until = time.monotonic() + baseline_time_s

    def feed(self, measurements: list[Measurement]) -> None:
        """Alimenta o baseline com medidas (durante a fase de captura)."""
        if self._samples is None:
            return
        for m in measurements:
            if m.distance <= 0:
                continue
            b = int(m.angle / 360 * self.bins) % self.bins
            if len(self._samples[b]) < 60:
                self._samples[b].append(m.distance)
        if time.monotonic() >= self._until:
            self._finish()

    def _finish(self) -> None:
        assert self._samples is not None
        for i, s in enumerate(self._samples):
            if s:
                self._bg[i] = float(np.median(s))
        self._samples = None
        self.ready = True

    def is_foreground(self, angle_deg: float, distance_mm: float) -> bool:
        """True se a medida é objeto (mais perto que o fundo nesse bin)."""
        if not self.ready:
            return False
        b = int(angle_deg / 360 * self.bins) % self.bins
        bg = self._bg[b]
        if not math.isfinite(bg):
            return True
        return distance_mm < bg - self.margin

    def foreground_points(self, points: list['Point2D']) -> list['Point2D']:
        """Filtra uma lista de Point2D mantendo só os que são foreground.
        Usa angle/distance preservados em Point2D."""
        return [p for p in points if self.is_foreground(p.angle, p.distance)]

    @property
    def learned_bins(self) -> int:
        return int(np.isfinite(self._bg).sum())


def dbscan(points: np.ndarray, eps: float, min_samples: int) -> np.ndarray:
    """DBSCAN básico. `points` é (N, 2); devolve labels np.ndarray (N,):
      -1 = ruído, 0..K-1 = cluster.

    Para o tamanho típico do nosso problema (foreground filtrado: dezenas
    a poucas centenas de pontos por frame), a matriz de distâncias N×N é
    rápida e simples. Se um dia N passar de uns 2000, trocar por KDTree.
    """
    n = len(points)
    if n == 0:
        return np.zeros(0, dtype=int)
    diff = points[:, None, :] - points[None, :, :]
    d2 = np.einsum('ijk,ijk->ij', diff, diff)
    eps2 = eps * eps
    neighbors = [np.flatnonzero(d2[i] <= eps2) for i in range(n)]
    is_core = np.array([len(neighbors[i]) >= min_samples for i in range(n)])
    labels = np.full(n, -1, dtype=int)
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
    """Roda DBSCAN e devolve [(centróide, count), ...] ordenado por count desc.
    Ignora pontos com label -1 (ruído)."""
    labels = dbscan(points, eps, min_samples)
    out = []
    for lab in sorted(set(labels.tolist()) - {-1}):
        m = labels == lab
        cx, cy = points[m].mean(axis=0)
        out.append(((float(cx), float(cy)), int(m.sum())))
    out.sort(key=lambda kv: kv[1], reverse=True)
    return out


def cluster_greedy(points: list[tuple[float, float]],
                   radius_mm: float) -> list[tuple[tuple[float, float], int]]:
    """Agrupa pontos próximos (greedy) e devolve [(centróide, count), ...]
    ordenado por count decrescente. Cada ponto entra no primeiro cluster
    cujo centróide está dentro de `radius_mm`; se nenhum, vira cluster novo.
    """
    clusters = []
    r2 = radius_mm * radius_mm
    for x, y in points:
        placed = False
        for c in clusters:
            cx = sum(p[0] for p in c) / len(c)
            cy = sum(p[1] for p in c) / len(c)
            if (x - cx) ** 2 + (y - cy) ** 2 <= r2:
                c.append((x, y))
                placed = True
                break
        if placed:
            continue
        clusters.append([(x, y)])
    out = []
    for c in clusters:
        n = len(c)
        cx = sum(p[0] for p in c) / n
        cy = sum(p[1] for p in c) / n
        out.append(((cx, cy), n))
    out.sort(key=lambda kv: kv[1], reverse=True)
    return out


def project_batch_np(measurements: list[Measurement], cfg: ProcessingCfg) -> np.ndarray:
    """Versão numpy. Devolve array (N, 4) = [x, y, quality, angle]."""
    if not measurements:
        return np.zeros((0, 4), dtype=float)
    arr = np.array([(m.angle, m.distance, m.quality) for m in measurements], dtype=float)
    angle, dist, q = arr[:, 0], arr[:, 1], arr[:, 2]
    mask = (dist > 0) & (dist >= cfg.min_dist_mm) & (dist <= cfg.max_dist_mm) & (q >= cfg.min_quality)
    angle, dist, q = angle[mask], dist[mask], q[mask]
    a = np.deg2rad(angle + cfg.angle_offset_deg)
    x = dist * np.cos(a)
    y = dist * np.sin(a)
    if cfg.mirror:
        x = -x
    return np.column_stack([x, y, q, angle])
