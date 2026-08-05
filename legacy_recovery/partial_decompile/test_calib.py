# Source Generated with Decompyle++
# File: test_calib.pyc (Python 3.13)

__doc__ = '\nLidarMapper — teste da Etapa 3: overlay de validação da calibração.\n\nCarrega `calibration.json` e mostra, ao vivo:\n  - LADO ESQUERDO: viz do plano do sensor (mm) com pontos crus +\n    foreground (depois do baseline) e o quadrilátero salvo dos 4 cantos\n  - LADO DIREITO: minimapa 0..1 da tela com cursores projetados via H\n\nTeclas:\n  ESC, Q : sair\n  B      : recaptura o baseline (se a área mudou desde o calibrate)\n  +/-    : zoom do painel esquerdo\n'
from __future__ import annotations
import argparse
import os
import sys
import time
import numpy as np
import pygame
import config as cfg_mod
import paths
from homography import apply_h, load_calibration
from lidar_reader import LidarReader
from processing import BackgroundSubtractor, project_batch, split_by_roi
# WARNING: Decompyle incomplete
