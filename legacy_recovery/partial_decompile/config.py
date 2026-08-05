# Source Generated with Decompyle++
# File: config.pyc (Python 3.13)

__doc__ = '\nLidarMapper — carregamento e persistência de configuração.\n\nLê `config.yaml` (se existir) e devolve um objeto tipado. Tudo que não\nestiver no YAML cai no default definido aqui.\n\nA função `save(cfg)` reescreve o YAML preservando comentários e ordem\nquando ruamel.yaml está disponível (instalado pelo control panel). Fallback\npra PyYAML quando não tem.\n'
from __future__ import annotations
import dataclasses
import os
from dataclasses import dataclass, field
from typing import Optional
import yaml
# WARNING: Decompyle incomplete
