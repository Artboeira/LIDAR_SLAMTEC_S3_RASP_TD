"""
LidarMapper — carregamento e persistência de configuração.

Lê `config.yaml` (se existir) e devolve um objeto tipado. Tudo que não
estiver no YAML cai no default definido aqui.

A função `save(cfg)` reescreve o YAML preservando comentários e ordem
quando ruamel.yaml está disponível (instalado pelo control panel). Fallback
pra PyYAML quando não tem.
"""
from __future__ import annotations

import dataclasses
import os
from dataclasses import dataclass, field
from typing import Optional

import yaml

try:
    from ruamel.yaml import YAML as _RuamelYAML
except ImportError:
    _RuamelYAML = None

import paths

_DEFAULT_PATH = paths.CONFIG_PATH


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
class VizCfg:
    width: int = 900
    height: int = 900
    scale_mm_per_px: float = 10
    trail_window_s: float = 0.2


@dataclass
class ScreenCfg:
    width_px: int = 1920
    height_px: int = 1080
    display_index: int = 1
    fullscreen: bool = True


@dataclass
class UdpCfg:
    host: str = "127.0.0.1"
    port: int = 5555
    publish_rate_hz: float = 30
    max_points: int = 32


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
class Config:
    logging: LoggingCfg = field(default_factory=LoggingCfg)
    sensor: SensorCfg = field(default_factory=SensorCfg)
    processing: ProcessingCfg = field(default_factory=ProcessingCfg)
    roi: ROICfg = field(default_factory=ROICfg)
    viz: VizCfg = field(default_factory=VizCfg)
    screen: ScreenCfg = field(default_factory=ScreenCfg)
    tracker: TrackerCfg = field(default_factory=TrackerCfg)
    udp: UdpCfg = field(default_factory=UdpCfg)


def _merge(target_cls, data: dict | None):
    """Cria a dataclass `target_cls` aplicando apenas os campos presentes em `data`.
    Campos ausentes ficam com o default. Campos com valor `null` no YAML viram
    None (útil pra desligar limites da ROI ou auto-detectar porta)."""
    if not data:
        return target_cls()
    valid = {f for f in target_cls.__dataclass_fields__}
    kwargs = {k: v for k, v in data.items() if k in valid}
    return target_cls(**kwargs)


def load(path: str | None = None) -> Config:
    """Lê o YAML. Se o arquivo não existir, devolve defaults."""
    p = path or _DEFAULT_PATH
    if not os.path.isfile(p):
        return Config()
    with open(p, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    return Config(
        logging=_merge(LoggingCfg, raw.get("logging")),
        sensor=_merge(SensorCfg, raw.get("sensor")),
        processing=_merge(ProcessingCfg, raw.get("processing")),
        roi=_merge(ROICfg, raw.get("roi")),
        viz=_merge(VizCfg, raw.get("viz")),
        screen=_merge(ScreenCfg, raw.get("screen")),
        tracker=_merge(TrackerCfg, raw.get("tracker")),
        udp=_merge(UdpCfg, raw.get("udp")),
    )


def _update_inplace(target, src):
    """Atualiza `target` (CommentedMap ou dict) com valores de `src` recursivamente.
    Preserva chaves de target que não estão em src e mantém comentários do ruamel."""
    for k, v in src.items():
        if isinstance(v, dict) and k in target and isinstance(target[k], dict):
            _update_inplace(target[k], v)
        else:
            target[k] = v


def save(cfg: "Config", path: str | None = None) -> None:
    """Salva cfg no YAML. Preserva comentários se ruamel.yaml disponível
    e o arquivo já existir."""
    target = path or _DEFAULT_PATH
    data_new = dataclasses.asdict(cfg)
    if _RuamelYAML is not None and os.path.isfile(target):
        y = _RuamelYAML()
        y.preserve_quotes = True
        with open(target, "r", encoding="utf-8") as f:
            existing = y.load(f) or {}
        _update_inplace(existing, data_new)
        with open(target, "w", encoding="utf-8") as f:
            y.dump(existing, f)
        return
    with open(target, "w", encoding="utf-8") as f:
        yaml.safe_dump(data_new, f, sort_keys=False, allow_unicode=True)


if __name__ == "__main__":
    cfg = load()
    import json
    print(json.dumps(dataclasses.asdict(cfg), indent=2, default=str))
