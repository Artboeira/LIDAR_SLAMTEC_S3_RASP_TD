# RECONSTRUÇÃO: módulo inteiro inferido — o paths.py não estava no kit de
# recuperação (ficou fora do PYZ do build PyInstaller). Atributos usados
# pelo restante do código: APP_DIR, CONFIG_PATH, CALIB_PATH. Comportamento
# confirmado pelo README_DIST.txt ("arquivos editáveis ao lado do exe") e
# pelo padrão canônico PyInstaller (sys.frozen).
"""
LidarMapper — resolução de caminhos da aplicação.

APP_DIR é a pasta do executável no build PyInstaller (sys.frozen) ou a
pasta do fonte em desenvolvimento. config.yaml e calibration.json vivem
ao lado do exe.
"""
from __future__ import annotations

import os
import sys

if getattr(sys, "frozen", False):
    APP_DIR = os.path.dirname(sys.executable)
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))

CONFIG_PATH = os.path.join(APP_DIR, "config.yaml")
CALIB_PATH = os.path.join(APP_DIR, "calibration.json")
