# Source Generated with Decompyle++
# File: main.pyc (Python 3.13)

__doc__ = '\nLidarMapper — entry point principal.\n\nPipeline completo: RPLIDAR S3 (thread separado) → filtro + ROI → background\nsubtraction → DBSCAN → tracker com IDs persistentes → UDP binário.\n\nA leitura do LIDAR roda no thread interno de `LidarReader`. Processamento\ne envio UDP rodam no thread principal — pra cada frame de tempo (limitado\npor `udp.publish_rate_hz`), drena medidas acumuladas, atualiza tracker,\nempacota com struct.pack e envia.\n\nUso típico:\n    python main.py\n    python main.py --port COM12\n    python main.py --log-level debug\n\nEncerra com Ctrl+C.\n'
from __future__ import annotations
import argparse
import logging
import os
import signal
import sys
import time
import numpy as np
import config as cfg_mod
import paths
from homography import load_calibration
from lidar_reader import LidarReader
from processing import BackgroundSubtractor, project_batch, split_by_roi
from publisher import RateLimiter, UdpPublisher
from tracker import Tracker
log = None('lidarmapper')
# WARNING: Decompyle incomplete
