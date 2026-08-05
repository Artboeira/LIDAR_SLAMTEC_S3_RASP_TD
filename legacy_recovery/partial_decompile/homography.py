# Source Generated with Decompyle++
# File: homography.pyc (Python 3.13)

__doc__ = '\nLidarMapper — Etapa 3: homografia 4 pontos + persistência.\n\nA homografia H é uma matriz 3×3 que mapeia (x_lidar_mm, y_lidar_mm) para\n(u, v) em coordenadas normalizadas da tela (0..1). 4 correspondências\ndeterminam H exatamente (DLT por SVD, numpy puro — sem dependência de cv2).\n\nPersistência:\n  calibration.json\n  {\n    "version": 1,\n    "screen_width_px": 1920,\n    "screen_height_px": 1080,\n    "corners_lidar_mm":  [[x_TL,y_TL], [x_TR,y_TR], [x_BR,y_BR], [x_BL,y_BL]],\n    "corners_screen_norm": [[0,0], [1,0], [1,1], [0,1]],\n    "H": [[...],[...],[...]]\n  }\n'
from __future__ import annotations
import json
import os
from dataclasses import dataclass
import numpy as np
Point = tuple[(float, float)]
CORNER_NAMES = ('TOP-LEFT', 'TOP-RIGHT', 'BOTTOM-RIGHT', 'BOTTOM-LEFT')
CORNERS_NORM: 'tuple[Point, Point, Point, Point]' = ((0, 0), (1, 0), (1, 1), (0, 1))
# WARNING: Decompyle incomplete
