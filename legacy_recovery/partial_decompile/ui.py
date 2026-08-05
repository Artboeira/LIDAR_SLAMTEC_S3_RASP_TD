# Source Generated with Decompyle++
# File: ui.pyc (Python 3.13)

__doc__ = '\nLidarMapper — Control Panel.\n\nJanela CustomTkinter que gerencia o pipeline (`main.py`) como subprocess.\n\nEtapas implementadas até aqui:\n  A — esqueleto: botões Start/Stop + log viewer + lifecycle do subprocess\n  B — status ao vivo: parsing do stdout do main e indicadores coloridos\n      (LIDAR, baseline, tracks, UDP, calibração)\n\nTudo roda em processo separado do pipeline — o front fala com o backend só\nvia stdin/stdout/sinais. main.py e calibrate.py continuam standalone.\n'
from __future__ import annotations
import os
import queue
import re
import signal
import subprocess
import sys
import threading
import time
import customtkinter as ctk
import config as cfg_mod
import paths
HERE = os.path.dirname(os.path.abspath(__file__))
CALIB_PATH = paths.CALIB_PATH
DISPATCHER_PATH = os.path.join(HERE, 'lidarmapper.py')
TOOLS = [
    ('test_viz', '📷 Test Viz', 'test_viz', False),
    ('test_tracker', '🎯 Test Tracker', 'test_tracker', True),
    ('test_calib', '📐 Test Calib', 'test_calib', True)]
# WARNING: Decompyle incomplete
