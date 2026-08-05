# Source Generated with Decompyle++
# File: test_viz.pyc (Python 3.13)

__doc__ = '\nLidarMapper — teste da Etapa 2: visualização 2D dos pontos filtrados.\n\nJanela pygame mostrando, ao vivo:\n  - sensor no centro\n  - eixos cinza + grade a cada 1 m\n  - ROI configurada (config.yaml) desenhada em amarelo\n  - pontos DENTRO da ROI em verde, pontos FORA em cinza\n  - HUD com FPS de leitura, FPS de viz, scans/s, contagem de pontos,\n    porta e status\n\nTeclas:\n  ESC, Q : sair\n  + / -  : zoom (altera scale_mm_per_px em runtime)\n  R      : reset zoom (volta ao valor do config)\n  TAB    : alterna mostrar/ocultar pontos fora da ROI\n\nConvenção visual: +x do sensor aponta pra DIREITA, +y aponta pra CIMA\n(coordenadas matemáticas). O pygame tem y crescendo pra baixo, então\nfazemos `py = h/2 - y/scale`.\n'
from __future__ import annotations
import argparse
import math
import sys
import time
import pygame
import config as cfg_mod
from lidar_reader import LidarReader
from processing import project_batch, split_by_roi
# WARNING: Decompyle incomplete
