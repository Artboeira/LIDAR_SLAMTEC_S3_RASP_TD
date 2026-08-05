# Source Generated with Decompyle++
# File: processing.pyc (Python 3.13)

__doc__ = '\nLidarMapper — processamento (Etapas 2-4).\n\nFunções puras. Não dependem de pygame, ZMQ nem do sensor. Recebem listas\nde Measurement (do lidar_reader.py) e devolvem listas de pontos\ncartesianos prontos pra visualização / tracking / publicação.\n\nInclui:\n  - polar→cartesiano + filtros (Etapa 2)\n  - ROI bbox (Etapa 2)\n  - BackgroundSubtractor por bin angular (Etapa 3, robustiza calibração)\n  - cluster_greedy (Etapa 3, escolher canto entre vários objetos visíveis)\n'
from __future__ import annotations
import math
import time
from dataclasses import dataclass
import numpy as np
from config import ProcessingCfg, ROICfg
from lidar_reader import Measurement
# WARNING: Decompyle incomplete
