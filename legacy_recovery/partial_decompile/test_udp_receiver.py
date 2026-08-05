# Source Generated with Decompyle++
# File: test_udp_receiver.pyc (Python 3.13)

__doc__ = '\nLidarMapper — teste da Etapa 5: receptor UDP binário de validação.\n\nRecebe datagramas do publisher, faz struct.unpack do formato LIDAR_MAPPER_V1\ne imprime FPS, latência (timestamp do pacote vs time.time() local), e\nopcionalmente o conteúdo decodificado.\n\nUso:\n    python test_udp_receiver.py\n    python test_udp_receiver.py --host 0.0.0.0 --port 5555\n    python test_udp_receiver.py --raw                # imprime cada pacote\n    python test_udp_receiver.py --duration 10        # roda 10s e sai\n\nPara latência fazer sentido, o relógio do publisher e do receiver têm que\nestar sincronizados — em localhost é trivial; entre máquinas, NTP ajuda.\n\nBind:\n  Por default, escuta em 0.0.0.0 (todas as interfaces) na porta `--port`.\n  Use --host 127.0.0.1 pra restringir ao localhost.\n'
from __future__ import annotations
import argparse
import socket
import sys
import time
import config as cfg_mod
from publisher import unpack_frame, _HEADER, _POINT
# WARNING: Decompyle incomplete
