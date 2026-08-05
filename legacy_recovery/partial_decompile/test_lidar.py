# Source Generated with Decompyle++
# File: test_lidar.pyc (Python 3.13)

__doc__ = '\nLidarMapper — teste da Etapa 1.\n\nConecta o RPLIDAR S3, lê em loop e imprime:\n  - status da conexão\n  - medidas/s (throughput do sensor) e scans/s (≈ rotações por segundo)\n  - amostra rotativa das últimas medidas (ângulo°, distância mm, qualidade)\n  - contador de reconexões / desyncs (estabilidade)\n\nUso:\n    python test_lidar.py                       # auto-detecta a porta\n    python test_lidar.py --port COM12          # força a porta\n    python test_lidar.py --port COM12 --raw    # imprime CADA medida\n                                                # (verboso; bom pra sanity check)\n    python test_lidar.py --duration 10         # roda 10s e sai (CI/smoke)\n\nCritério de validação (humano):\n  - status "conectado @ COMx"\n  - scans/s ≈ 8–15 Hz (S3 típico)\n  - medidas/s na faixa de milhares (S3 a 1 Mbps faz ~32 kHz em teoria,\n    a leitura via pyserial geralmente fica em alguns milhares)\n  - reconnects = 0, desyncs baixos ou zero por 30s+ sem mexer\n'
from __future__ import annotations
import argparse
import sys
import time
from lidar_reader import LidarReader
# WARNING: Decompyle incomplete
