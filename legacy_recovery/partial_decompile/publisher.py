# Source Generated with Decompyle++
# File: publisher.pyc (Python 3.13)

__doc__ = '\nLidarMapper — Etapa 5: publisher UDP binário.\n\nEmpacota os tracks num datagrama binário com `struct.pack` (little-endian,\nsem padding) e envia via UDP. Formato pensado pra ser consumido por um\nUDP In DAT no TouchDesigner com um callback que faz `struct.unpack`.\n\nFormato (LIDAR_MAPPER_V1):\n\n  Header (14 bytes):\n    uint32   frame\n    float64  timestamp           (segundos desde epoch, time.time())\n    uint16   num_points          (N)\n\n  Por ponto (16 bytes, repetido N vezes):\n    uint32   id\n    float32  x                   (0..1, espaço normalizado da tela)\n    float32  y                   (0..1)\n    float32  confidence          (0..1)\n\n  Tamanho do datagrama = 14 + 16 * N bytes.\n  Com max_tracks=10  → 174 B, bem abaixo do MTU típico (1500 B), sem fragmentação.\n  O cap `max_points` no config é uma cinta extra (trunca se passar disso).\n\nAtenção: UDP é "best-effort" (sem retransmissão, ordem não garantida).\nPra esse caso (estado completo a cada frame), perder um pacote significa\nficar uma "frame" atrasado e o próximo já carrega o estado novo. Aceitável.\n'
from __future__ import annotations
import socket
import struct
import time
from tracker import Track
_HEADER = None('<IdH')
_POINT = None('<Ifff')
# WARNING: Decompyle incomplete
