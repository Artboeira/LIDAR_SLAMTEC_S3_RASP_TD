# Source Generated with Decompyle++
# File: test_e2e.pyc (Python 3.13)

__doc__ = '\nLidarMapper — teste E2E final (sem hardware).\n\nRoda o pipeline completo em memória, sem conectar ao sensor: gera medidas\nsintéticas (2 "objetos" se movendo em círculos), envia via UDP no mesmo\nprocesso, e um receptor local valida que recebeu pacotes coerentes.\n\nCritérios de sucesso:\n  - pelo menos N pacotes recebidos\n  - último pacote tem 2 tracks ativos\n  - IDs estáveis (frames consecutivos compartilham pelo menos um id)\n  - coords (x, y) ∈ [0, 1]\n  - tamanho do datagrama bate com o esperado pelo header\n\nUso:\n    python test_e2e.py\n    python test_e2e.py --duration 3\n'
from __future__ import annotations
import argparse
import math
import socket
import threading
import time
import numpy as np
import config as cfg_mod
from config import TrackerCfg
from homography import compute_homography
from lidar_reader import Measurement
from processing import BackgroundSubtractor, project_batch, split_by_roi
from publisher import RateLimiter, UdpPublisher, unpack_frame, _HEADER, _POINT
from tracker import Tracker
# WARNING: Decompyle incomplete
