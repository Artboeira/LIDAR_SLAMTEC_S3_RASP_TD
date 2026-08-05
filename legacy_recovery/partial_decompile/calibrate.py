# Source Generated with Decompyle++
# File: calibrate.pyc (Python 3.13)

__doc__ = '\nLidarMapper — Etapa 3: calibração visual no painel/projetor.\n\nAbre em FULLSCREEN no display configurado em `config.yaml > screen.display_index`\n(default 1 = segundo monitor). Mostra os 4 cantos da tela como alvos grandes\n(cruz + círculos), com o canto ATIVO em amarelo e os outros apagados.\n\nFluxo:\n  1. BASELINE  — captura o fundo estático por ~2s (mantenha a área livre).\n  2. CAPTURE   — para cada canto: posicione objeto/mão sobre o alvo aceso,\n                 confira o "detectando: N pts" em verde e aperte ESPAÇO.\n                 Tela mostra "CAPTURANDO..." e bloqueia ~1.2s acumulando pontos\n                 foreground; o centróide do maior cluster vira o ponto desse canto.\n  3. DONE      — modo teste: cursor verde sobre a tela onde sua mão estiver.\n                 R = refaz a calibração, ESC = sai.\n\nTeclas:\n  SPACE     confirma o canto atual (em CAPTURE)\n  B         re-captura o fundo (se algo entrou na cena no baseline)\n  R         refaz a calibração (em DONE)\n  ESC / Q   sai\n\nSobre a tela alvo:\n  As coordenadas dos alvos ficam dentro de uma margem (default 6% da borda),\n  porque você precisa conseguir alcançá-los fisicamente. Esses pontos são\n  mapeados para o espaço normalizado 0..1 do TouchDesigner.\n'
from __future__ import annotations
import argparse
import math
import os
import sys
import time
import numpy as np
import pygame
import config as cfg_mod
import paths
from homography import Calibration, apply_h, compute_homography, save_calibration
from lidar_reader import LidarReader
from processing import BackgroundSubtractor, Point2D, cluster_greedy, project_batch, split_by_roi
CORNER_NAMES = ('SUPERIOR ESQUERDO', 'SUPERIOR DIREITO', 'INFERIOR DIREITO', 'INFERIOR ESQUERDO')
CORNERS_NORM = ((0, 0), (1, 0), (1, 1), (0, 1))
TARGET_MARGIN = 0.06
C_BG = (0, 25, 60)
C_BG_BUSY = (0, 30, 70)
C_WHITE = (240, 240, 240)
C_YELLOW = (240, 200, 60)
C_GREEN = (80, 220, 100)
C_RED = (230, 80, 80)
C_GREY = (110, 110, 120)
C_DIM = (180, 180, 200)
# WARNING: Decompyle incomplete
