# Source Generated with Decompyle++
# File: test_tracker.pyc (Python 3.13)

__doc__ = '\nLidarMapper — teste da Etapa 4: tracking ao vivo com IDs persistentes.\n\nJanela 1280x720 dividida ao meio:\n  ESQUERDA  — viz do plano do sensor (mm):\n              pontos crus (cinza), foreground (verde fraco),\n              centróides de cluster (amarelo), tracks com ID (azul claro)\n              + quadrilátero da calibração\n\n  DIREITA   — minimapa 0..1 da tela:\n              tracks projetados via homografia, cada um com:\n                - círculo proporcional à confidence\n                - label "id=N  u=0.xx v=0.xx"\n                - rastro dos últimos ~0.5s\n\nTeclas:\n  ESC, Q : sair\n  B      : recaptura o fundo (use se a cena mudou)\n  +/-    : zoom no painel LIDAR\n'
from __future__ import annotations
import argparse
import os
import sys
import time
from collections import deque, defaultdict
import numpy as np
import pygame
import config as cfg_mod
import paths
from homography import load_calibration
from lidar_reader import LidarReader
from processing import BackgroundSubtractor, Point2D, dbscan_centroids, project_batch, split_by_roi
from tracker import Tracker, Track
# WARNING: Decompyle incomplete
