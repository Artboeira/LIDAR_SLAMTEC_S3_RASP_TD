"""
LidarMapper — carregamento de config_server.yaml.

Lê o YAML e devolve dataclasses tipadas. Sem persistência (o server_server.yaml
é editado à mão; não replicamos o hot-reload do config_server em runtime).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

import yaml


HERE = os.path.dirname(os.path.abspath(__file__))
_DEFAULT_PATH = os.path.join(HERE, "config_server.yaml")


@dataclass
class PanelCfg:
    panel_id: int
    out_port: int
    display_index: int
    calib_file: str


@dataclass
class OscCfg:
    host: str = "127.0.0.1"
    port: int = 7500
    timeout_s: float = 0.18


@dataclass
class TdCfg:
    host: str = "127.0.0.1"
    clip_out_of_range: bool = True


@dataclass
class CalibrationCfg:
    collect_s: float = 2.0
    window_mm: float = 250.0
    min_pts: int = 30
    target_insets: tuple[float, float] = (0.06, 0.94)


@dataclass
class ServerCfg:
    listen_port: int = 5555
    panels: dict[int, PanelCfg] = field(default_factory=dict)
    osc: OscCfg = field(default_factory=OscCfg)
    td: TdCfg = field(default_factory=TdCfg)
    calibration: CalibrationCfg = field(default_factory=CalibrationCfg)


def load(path: str | None = None) -> ServerCfg:
    p = path or _DEFAULT_PATH
    with open(p, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    panels_raw = raw.get("panels") or {}
    panels = {}
    for pid, vals in panels_raw.items():
        pid_int = int(pid)
        panels[pid_int] = PanelCfg(panel_id=pid_int, **vals)

    osc = OscCfg(**(raw.get("osc") or {}))
    td = TdCfg(**(raw.get("td") or {}))
    calib_raw = dict(raw.get("calibration") or {})
    if "target_insets" in calib_raw:
        calib_raw["target_insets"] = tuple(calib_raw["target_insets"])
    calib = CalibrationCfg(**calib_raw)

    return ServerCfg(
        listen_port=int(raw.get("listen_port", 5555)),
        panels=panels,
        osc=osc,
        td=td,
        calibration=calib,
    )
