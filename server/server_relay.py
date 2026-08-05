"""
LidarMapper — middleware de servidor (§5.2 item 7 do guia).

Loop por pacote:
  socket V2 :listen_port
    → valida version/tamanho (shared.protocol.unpack_v2)
    → demux por panel_id
    → aplica H de calib_pN.json
    → descarta pontos fora de [0..1] (§2, segunda linha de defesa)
    → detecta down/up por diff de ids (§3.2)
    → envia OSC /touch/<panel_id> ao Max (python-osc)
    → reempacota V1 e envia a 127.0.0.1:<out_port>

Hot-reload dos JSONs de calibração por mtime: cada pacote checa o mtime do
arquivo do seu painel; se mudou, recarrega. Sem custo perceptível
(stat() por pacote, algo como 100..1000/s, é ruído).

Sem calibração de um painel → não repassa, loga uma vez.

Alvo: Windows (paths, sockets, sem systemd). NSSM/Task Scheduler cuidam
do processo em produção; aqui só o loop.

Uso:
    python server/server_relay.py
    python server/server_relay.py --config outro.yaml
    python server/server_relay.py --no-osc     # dev sem Max
"""
from __future__ import annotations

import argparse
import logging
import os
import signal
import socket
import sys
import time
from dataclasses import dataclass, field
from typing import Iterable

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.protocol import (  # noqa: E402
    V1Point, V2Frame, ProtocolError, pack_v1, unpack_v2,
)
from server import config_server as cfg_mod  # noqa: E402
from server.homography import Calibration, apply_h_batch, load_calibration  # noqa: E402

log = logging.getLogger("relay")

HERE = os.path.dirname(os.path.abspath(__file__))


@dataclass
class PanelState:
    cfg: cfg_mod.PanelCfg
    calib_path: str
    calib: Calibration | None = None
    calib_mtime: float = 0.0
    warned_no_calib: bool = False
    # id → wall_time do último frame em que a id apareceu
    active_ids: dict[int, float] = field(default_factory=dict)
    n_in: int = 0
    n_out: int = 0
    n_clipped: int = 0
    n_down: int = 0
    last_pkt_mono: float = 0.0


def _resolve_calib_path(rel: str) -> str:
    return rel if os.path.isabs(rel) else os.path.join(HERE, rel)


def _reload_calib_if_needed(state: PanelState) -> None:
    """Stat + reload se o mtime mudou. Sem lock: o relay é single-threaded."""
    try:
        mtime = os.path.getmtime(state.calib_path)
    except OSError:
        # arquivo sumiu (ou nunca existiu): mantém o que tinha
        if state.calib is not None:
            log.warning("[p%d] calib %s sumiu — mantendo H antiga",
                        state.cfg.panel_id, state.calib_path)
        elif not state.warned_no_calib:
            log.warning("[p%d] sem calibração em %s — pacotes ignorados",
                        state.cfg.panel_id, state.calib_path)
            state.warned_no_calib = True
        return
    if mtime == state.calib_mtime:
        return
    calib = load_calibration(state.calib_path)
    if calib is None:
        log.warning("[p%d] calib %s inválida — mantendo H antiga",
                    state.cfg.panel_id, state.calib_path)
        return
    state.calib = calib
    state.calib_mtime = mtime
    state.warned_no_calib = False
    log.info("[p%d] calib carregada: err reprojeção check nos cantos %s",
             state.cfg.panel_id, os.path.basename(state.calib_path))


class OscSender:
    """Envelope fino sobre python-osc. Se `enabled=False` (--no-osc),
    vira no-op e não importa o pacote."""

    def __init__(self, host: str, port: int, enabled: bool = True):
        self.enabled = enabled
        self.host = host
        self.port = port
        self._client = None
        if enabled:
            from pythonosc.udp_client import SimpleUDPClient
            self._client = SimpleUDPClient(host, port)

    def send_touch(self, panel_id: int) -> None:
        if not self.enabled:
            return
        self._client.send_message(f"/touch/{panel_id}", [])


class Relay:
    def __init__(self, cfg: cfg_mod.ServerCfg, enable_osc: bool = True):
        self.cfg = cfg
        self.panels: dict[int, PanelState] = {}
        for pid, pcfg in cfg.panels.items():
            st = PanelState(cfg=pcfg, calib_path=_resolve_calib_path(pcfg.calib_file))
            _reload_calib_if_needed(st)
            self.panels[pid] = st
        # socket de entrada (V2)
        self.sock_in = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock_in.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock_in.bind(("0.0.0.0", cfg.listen_port))
        self.sock_in.settimeout(0.25)
        # socket de saída (V1 para localhost)
        self.sock_out = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        # OSC
        self.osc = OscSender(cfg.osc.host, cfg.osc.port, enabled=enable_osc)
        self._stop = False
        self._last_status = time.monotonic()
        self._unknown_panels_warned: set[int] = set()

    def stop(self) -> None:
        self._stop = True

    def _on_v2(self, frm: V2Frame) -> None:
        state = self.panels.get(frm.panel_id)
        if state is None:
            if frm.panel_id not in self._unknown_panels_warned:
                log.warning("panel_id=%d fora do config_server.yaml — pacotes ignorados",
                            frm.panel_id)
                self._unknown_panels_warned.add(frm.panel_id)
            return
        state.n_in += 1
        state.last_pkt_mono = time.monotonic()
        _reload_calib_if_needed(state)
        if state.calib is None:
            return

        # aplica H em lote
        if frm.points:
            xy = np.array([(p.x_mm, p.y_mm) for p in frm.points], dtype=float)
            uv = apply_h_batch(state.calib.H, xy)
        else:
            uv = np.zeros((0, 2))

        # clip fora de [0..1] (§2: segunda linha de defesa contra o LIDAR
        # ver o painel vizinho)
        v1_pts: list[V1Point] = []
        seen_ids: set[int] = set()
        clip = self.cfg.td.clip_out_of_range
        for src, (u, v) in zip(frm.points, uv):
            if clip and not (0.0 <= u <= 1.0 and 0.0 <= v <= 1.0):
                state.n_clipped += 1
                continue
            v1_pts.append(V1Point(id=src.id, x=float(u), y=float(v),
                                  confidence=src.confidence))
            seen_ids.add(src.id)

        # down/up (§3.2)
        now_wall = time.time()
        for pid in seen_ids:
            if pid not in state.active_ids:
                state.n_down += 1
                self.osc.send_touch(state.cfg.panel_id)
            state.active_ids[pid] = now_wall
        # expiração por timeout_s (up reservado; só limpa o estado)
        cutoff = now_wall - self.cfg.osc.timeout_s
        state.active_ids = {pid: t for pid, t in state.active_ids.items()
                            if t >= cutoff or pid in seen_ids}

        # V1 para o TD (mantém o cap de 32 como o resto do sistema)
        body = pack_v1(frm.frame, frm.timestamp, v1_pts, max_points=32)
        try:
            self.sock_out.sendto(body, (self.cfg.td.host, state.cfg.out_port))
            state.n_out += 1
        except OSError as exc:
            log.warning("[p%d] sendto V1 falhou: %s", state.cfg.panel_id, exc)

    def _status_tick(self) -> None:
        now = time.monotonic()
        if now - self._last_status < 1.0:
            return
        parts = []
        for pid, st in sorted(self.panels.items()):
            age = now - st.last_pkt_mono if st.last_pkt_mono else float("inf")
            age_s = f"{age:4.1f}s" if age != float("inf") else "  ∞ "
            calib_flag = "C" if st.calib else "-"
            parts.append(f"p{pid}[{calib_flag}] in={st.n_in} out={st.n_out} "
                         f"drop={st.n_clipped} down={st.n_down} age={age_s}")
        log.info("  ".join(parts))
        self._last_status = now

    def run(self) -> int:
        log.info("relay escutando 0.0.0.0:%d (panels=%s, OSC %s:%d %s)",
                 self.cfg.listen_port, sorted(self.panels.keys()),
                 self.cfg.osc.host, self.cfg.osc.port,
                 "on" if self.osc.enabled else "off")

        def _sig(*_):
            self.stop()

        signal.signal(signal.SIGINT, _sig)
        try:
            signal.signal(signal.SIGTERM, _sig)
        except (AttributeError, ValueError):
            pass

        while not self._stop:
            try:
                data, _peer = self.sock_in.recvfrom(8192)
            except socket.timeout:
                self._status_tick()
                continue
            try:
                frm = unpack_v2(data)
            except ProtocolError as exc:
                log.warning("V2 inválido de %s: %s", _peer, exc)
                continue
            self._on_v2(frm)
            self._status_tick()

        log.info("relay encerrando.")
        self.sock_in.close()
        self.sock_out.close()
        return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="LidarMapper server relay (§5.2)")
    ap.add_argument("--config", default=None, help="path do config_server.yaml")
    ap.add_argument("--no-osc", action="store_true",
                    help="desativa envio OSC (dev sem Max)")
    ap.add_argument("--log-level", default="info",
                    help="debug|info|warning|error")
    args = ap.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="[%(asctime)s] %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S")

    cfg = cfg_mod.load(args.config)
    relay = Relay(cfg, enable_osc=not args.no_osc)
    return relay.run()


if __name__ == "__main__":
    raise SystemExit(main())
