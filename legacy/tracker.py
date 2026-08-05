"""
LidarMapper — Etapa 4: tracking de cursores com IDs persistentes.

Pipeline por frame:
  pontos foreground (mm) → DBSCAN → centróides → associação nearest-neighbor
  com gating → tracks atualizados/novos → expira não-vistos por timeout
  → aplica homografia em cada track → (u, v) em [0,1].

Cada Track tem ID estável (incrementado globalmente). A confidence sobe
gradualmente até 1.0 nos primeiros N frames de vida e cai pra zero quando
o track não é visto há muito tempo. Suavização exponencial opcional no (x,y)
do plano do sensor pra reduzir jitter visual.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import time
from typing import Iterable

import numpy as np

from config import TrackerCfg
from homography import apply_h_batch
from processing import dbscan_centroids


@dataclass
class Track:
    id: int
    x_mm: float
    y_mm: float
    u: float = 0.0
    v: float = 0.0
    last_seen: float = 0.0
    first_seen: float = 0.0
    age_frames: int = 0
    confidence: float = 0.0


class Tracker:
    """Mantém uma lista de Track com IDs persistentes."""

    def __init__(self, cfg: TrackerCfg):
        self.cfg = cfg
        self._tracks = []
        self._next_id = 1

    @property
    def tracks(self) -> list[Track]:
        """Lista mutável dos tracks ativos atuais (não copia — leitura)."""
        return self._tracks

    def update(self, fg_points_xy: np.ndarray, H: np.ndarray | None,
               now: float | None = None) -> list[Track]:
        """Atualiza tracks dado o conjunto de pontos foreground deste frame.

        Args:
            fg_points_xy: array (N, 2) de pontos foreground em mm no plano
                          do sensor. Pode estar vazio.
            H: matriz 3x3 da homografia LIDAR→(u,v). Se None, (u,v) ficam 0.
            now: time.monotonic() opcional (pra testes determinísticos).
        """
        if now is None:
            now = time.monotonic()
        cfg = self.cfg
        centroids = dbscan_centroids(fg_points_xy, cfg.dbscan_eps_mm,
                                     cfg.dbscan_min_samples)
        pairs = []
        for ci, ((cx, cy), n) in enumerate(centroids):
            for ti, tr in enumerate(self._tracks):
                d2 = (cx - tr.x_mm) ** 2 + (cy - tr.y_mm) ** 2
                if d2 <= cfg.match_dist_mm ** 2:
                    pairs.append((d2, ti, ci))
        pairs.sort()
        used_t = [False] * len(self._tracks)
        used_c = [False] * len(centroids)
        a = cfg.smoothing
        for _d2, ti, ci in pairs:
            if used_t[ti] or used_c[ci]:
                continue
            used_t[ti] = used_c[ci] = True
            (cx, cy), _n = centroids[ci]
            tr = self._tracks[ti]
            tr.x_mm = a * tr.x_mm + (1 - a) * cx
            tr.y_mm = a * tr.y_mm + (1 - a) * cy
            tr.last_seen = now
            tr.age_frames += 1
            tr.confidence = min(1.0, tr.age_frames / max(cfg.confidence_frames, 1))
        for ci, ((cx, cy), n) in enumerate(centroids):
            if used_c[ci]:
                continue
            if len(self._tracks) >= cfg.max_tracks:
                break
            t = Track(id=self._next_id, x_mm=cx, y_mm=cy, last_seen=now,
                      first_seen=now, age_frames=1,
                      confidence=1.0 / max(cfg.confidence_frames, 1))
            self._tracks.append(t)
            self._next_id += 1
        self._tracks = [t for t in self._tracks if now - t.last_seen <= cfg.timeout_s]
        if self._tracks and H is not None:
            xy = np.array([(t.x_mm, t.y_mm) for t in self._tracks], dtype=float)
            uv = apply_h_batch(H, xy)
            for tr, (u, v) in zip(self._tracks, uv):
                tr.u = float(u)
                tr.v = float(v)
        return self._tracks

    def reset(self) -> None:
        self._tracks.clear()
        self._next_id = 1
