# Source Generated with Decompyle++
# File: tracker.pyc (Python 3.13)

__doc__ = '\nLidarMapper — Etapa 4: tracking de cursores com IDs persistentes.\n\nPipeline por frame:\n  pontos foreground (mm) → DBSCAN → centróides → associação nearest-neighbor\n  com gating → tracks atualizados/novos → expira não-vistos por timeout\n  → aplica homografia em cada track → (u, v) em [0,1].\n\nCada Track tem ID estável (incrementado globalmente). A confidence sobe\ngradualmente até 1.0 nos primeiros N frames de vida e cai pra zero quando\no track não é visto há muito tempo. Suavização exponencial opcional no (x,y)\ndo plano do sensor pra reduzir jitter visual.\n'
from __future__ import annotations
from dataclasses import dataclass, field
import time
from typing import Iterable
import numpy as np
from config import TrackerCfg
from homography import apply_h_batch
from processing import dbscan_centroids
# WARNING: Decompyle incomplete
