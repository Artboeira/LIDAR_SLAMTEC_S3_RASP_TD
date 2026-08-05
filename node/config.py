"""
LidarMapper node — carregamento de config.yaml (Pi headless).

Enxuto vs. legacy:
  - REMOVIDO: viz, screen (o node é headless, sem tela)
  - REMOVIDO: calibração (calibration.json vive só no servidor, §2 do guia)
  - ADICIONADO: udp.panel_id (1..8, obrigatório, sem default silencioso)
  - ADICIONADO: baseline (config do BackgroundSubtractor, antes hardcoded)

Chama `load()` sem args pra ler o config.yaml ao lado deste arquivo.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional

import yaml


HERE = os.path.dirname(os.path.abspath(__file__))
_DEFAULT_PATH = os.path.join(HERE, "config.yaml")


@dataclass
class LoggingCfg:
    level: str = "info"
    format: str = "[%(asctime)s] %(levelname)-7s %(name)s: %(message)s"


@dataclass
class SensorCfg:
    port: Optional[str] = None
    baud: int = 1000000


@dataclass
class ProcessingCfg:
    min_dist_mm: float = 80
    max_dist_mm: float = 6000
    min_quality: int = 5
    angle_offset_deg: float = 0
    mirror: bool = False


@dataclass
class ROICfg:
    """ROI em coordenadas cartesianas (mm) no referencial do sensor.
    None em qualquer lado = sem filtro nesse limite."""
    x_min: Optional[float] = -3000
    x_max: Optional[float] = 3000
    y_min: Optional[float] = -3000
    y_max: Optional[float] = 3000


@dataclass
class TrackerCfg:
    dbscan_eps_mm: float = 150
    dbscan_min_samples: int = 4
    match_dist_mm: float = 220
    timeout_s: float = 0.18
    max_tracks: int = 10
    confidence_frames: int = 5
    smoothing: float = 0.35


@dataclass
class UdpCfg:
    host: str = "10.10.0.10"
    port: int = 5555
    panel_id: int = 0
    publish_rate_hz: float = 30
    max_points: int = 32


@dataclass
class BaselineCfg:
    duration_s: float = 2.0
    margin_mm: float = 120
    bins: int = 720


@dataclass
class Config:
    logging: LoggingCfg = field(default_factory=LoggingCfg)
    sensor: SensorCfg = field(default_factory=SensorCfg)
    processing: ProcessingCfg = field(default_factory=ProcessingCfg)
    roi: ROICfg = field(default_factory=ROICfg)
    tracker: TrackerCfg = field(default_factory=TrackerCfg)
    udp: UdpCfg = field(default_factory=UdpCfg)
    baseline: BaselineCfg = field(default_factory=BaselineCfg)


def _merge(target_cls, data: dict | None):
    if not data:
        return target_cls()
    valid = set(target_cls.__dataclass_fields__)
    kwargs = {k: v for k, v in data.items() if k in valid}
    return target_cls(**kwargs)


def load(path: str | None = None) -> Config:
    """Carrega config.yaml. Levanta ValueError se udp.panel_id não estiver
    em 1..8 (obrigatório, sem default silencioso — o único campo realmente
    distinto entre os nós, conforme §9 e §14 do guia)."""
    p = path or _DEFAULT_PATH
    if not os.path.isfile(p):
        raise FileNotFoundError(f"config não encontrado: {p}")
    with open(p, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    cfg = Config(
        logging=_merge(LoggingCfg, raw.get("logging")),
        sensor=_merge(SensorCfg, raw.get("sensor")),
        processing=_merge(ProcessingCfg, raw.get("processing")),
        roi=_merge(ROICfg, raw.get("roi")),
        tracker=_merge(TrackerCfg, raw.get("tracker")),
        udp=_merge(UdpCfg, raw.get("udp")),
        baseline=_merge(BaselineCfg, raw.get("baseline")),
    )
    if not (1 <= cfg.udp.panel_id <= 8):
        raise ValueError(
            f"udp.panel_id obrigatório em 1..8 no {os.path.basename(p)} "
            f"(veio {cfg.udp.panel_id}). Cada nó do repo tem panel_id único.")
    return cfg
