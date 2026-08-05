# Source Generated with Decompyle++
# File: lidar_reader.pyc (Python 3.13)

__doc__ = '\nLidarMapper — Etapa 1: leitura do RPLIDAR S3.\n\nConecta o sensor via serial USB e roda a varredura num thread separado.\nExpõe medidas ao consumidor (drain), estatísticas (medidas/s, scans/s) e\num campo de status legível pra debug.\n\nA interface foi pensada pra ser estável nas etapas seguintes — o que\nmuda à frente é o que se faz com as medidas (filtragem, homografia,\nclustering, publicação ZMQ), não como elas são lidas.\n\nQuirks do S3 já tratados aqui:\n  - Sessão anterior mal encerrada deixa lixo no buffer → stop()+clean_input()\n    antes de get_info(); se ainda assim get_info() falhar, reset() + sleep + retry.\n  - O logger interno do `rplidar` fala muito a 1 Mbps ("Too many bytes") e\n    rouba throughput do hot loop. Silenciamos pra ERROR.\n  - O thread de leitura NUNCA morre: soft recover nas primeiras desyncs e\n    hard reconnect (recria RPLidar) se persistir. Só sai com stop().\n'
from __future__ import annotations
import logging
import threading
import time
from collections import deque
from dataclasses import dataclass
# WARNING: Decompyle incomplete
