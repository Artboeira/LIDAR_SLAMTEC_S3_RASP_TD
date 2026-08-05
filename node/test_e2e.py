"""
LidarMapper node — teste E2E sem hardware (§5.1 item 6 do guia).

Roda o pipeline inteiro em memória, sem sensor: gera medidas sintéticas
(2 cursores fake se movendo em círculo no plano do sensor), publica V2
via loopback com panel_id do config, e um receptor local decodifica com
`shared/protocol.unpack_v2` e valida:

  - N pacotes recebidos
  - version == 2, panel_id do config aparece
  - últimos frames têm 2 tracks
  - ids persistentes (ao menos 1 id em comum nos últimos 10 frames)
  - x_mm, y_mm em mm (não normalizados)
  - tamanho do datagrama bate com o header

É o smoke test do ambiente ARM: deve passar sem sensor conectado.

Uso:
    python node/test_e2e.py
    python node/test_e2e.py --duration 3
"""
from __future__ import annotations

import argparse
import math
import os
import socket
import sys
import threading
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import Config, ProcessingCfg, ROICfg, TrackerCfg, UdpCfg, BaselineCfg  # noqa: E402
from processing import BackgroundSubtractor, in_roi_mask, project_and_filter  # noqa: E402
from publisher import RateLimiter, UdpPublisher  # noqa: E402
from tracker import Tracker  # noqa: E402
from shared.protocol import unpack_v2, ProtocolError  # noqa: E402


def _synth_baseline(now: float, n_walls: int = 360) -> np.ndarray:
    """`n_walls` amostras varrendo 0..360°, distância 6000 mm (paredes
    a 6 m). Simula o fundo estático do sensor."""
    angles = np.linspace(0, 360, n_walls, endpoint=False, dtype=np.float32)
    dists = np.full(n_walls, 6000.0, dtype=np.float32)
    qs = np.full(n_walls, 30, dtype=np.float32)
    return np.column_stack([angles, dists, qs])


def _synth_blob(cx: float, cy: float, n: int, jitter_mm: float,
                rng: np.random.Generator) -> np.ndarray:
    """`n` amostras (angle°, dist_mm, quality) para um blob gaussiano
    em (cx, cy) no plano do sensor."""
    xs = cx + rng.normal(0, jitter_mm, n)
    ys = cy + rng.normal(0, jitter_mm, n)
    angles = (np.rad2deg(np.arctan2(ys, xs)) + 360.0) % 360.0
    dists = np.hypot(xs, ys)
    qs = np.full(n, 30, dtype=np.float32)
    return np.column_stack([angles.astype(np.float32),
                            dists.astype(np.float32), qs])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--duration", type=float, default=2.0)
    ap.add_argument("--port", type=int, default=15599,
                    help="porta UDP loopback (não conflita com relay 5555)")
    ap.add_argument("--panel-id", type=int, default=3,
                    help="panel_id sintético do publisher")
    ap.add_argument("--baseline-s", type=float, default=0.5)
    args = ap.parse_args()

    # cfg sintético — sem carregar config.yaml (não precisamos)
    cfg = Config(
        processing=ProcessingCfg(min_quality=5, min_dist_mm=80, max_dist_mm=8000),
        roi=ROICfg(x_min=-3000, x_max=3000, y_min=-3000, y_max=3000),
        tracker=TrackerCfg(dbscan_eps_mm=160, dbscan_min_samples=4,
                           match_dist_mm=300, smoothing=0.2),
        udp=UdpCfg(host="127.0.0.1", port=args.port,
                   panel_id=args.panel_id, publish_rate_hz=60),
        baseline=BaselineCfg(duration_s=args.baseline_s, margin_mm=120),
    )

    # receptor em thread separada
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", args.port))
    sock.settimeout(0.1)
    received: list = []
    received_lock = threading.Lock()
    sub_done = threading.Event()

    def recv_loop():
        while not sub_done.is_set():
            try:
                data, _ = sock.recvfrom(8192)
            except socket.timeout:
                continue
            try:
                pkt = unpack_v2(data)
                with received_lock:
                    received.append(pkt)
            except ProtocolError:
                pass
        sock.close()

    t_sub = threading.Thread(target=recv_loop, daemon=True)
    t_sub.start()

    pub = UdpPublisher(host=cfg.udp.host, port=cfg.udp.port,
                       panel_id=cfg.udp.panel_id, max_points=cfg.udp.max_points)
    print(f"[e2e] publisher em {pub.endpoint}")

    bg = BackgroundSubtractor(bins=cfg.baseline.bins, margin_mm=cfg.baseline.margin_mm)
    bg.begin_baseline(cfg.baseline.duration_s)
    trk = Tracker(cfg.tracker)
    rng = np.random.default_rng(42)

    # alimenta o baseline com "paredes" por baseline_s
    print("[e2e] alimentando baseline...")
    while not bg.ready:
        bg.feed(_synth_baseline(time.monotonic()))
        time.sleep(0.02)
    print(f"[e2e] baseline pronto ({bg.learned_bins}/{cfg.baseline.bins} bins)")

    print(f"[e2e] rodando {args.duration:.1f}s com 2 cursores sintéticos...")
    t_start = time.monotonic()
    limiter = RateLimiter(cfg.udp.publish_rate_hz)
    while time.monotonic() - t_start < args.duration:
        now = time.monotonic()
        t = now - t_start
        # 2 cursores fake em órbita, dentro da ROI e dentro do foreground
        ax = 1200 + 400 * math.cos(t * 2)
        ay = -200 + 400 * math.sin(t * 2)
        bx = 1800 + 250 * math.cos(t * 1.3 + math.pi / 3)
        by = 200 + 250 * math.sin(t * 1.3 + math.pi / 3)
        arr = np.concatenate([
            _synth_baseline(now),
            _synth_blob(ax, ay, 20, 30, rng),
            _synth_blob(bx, by, 20, 30, rng),
        ])
        fg = arr[bg.foreground_mask(arr)]
        xy = project_and_filter(fg, cfg.processing)
        m = in_roi_mask(xy, cfg.roi) if len(xy) else np.zeros(0, dtype=bool)
        tracks = trk.update(xy[m])
        if limiter.ready(now):
            pub.publish(tracks)
        time.sleep(0.005)

    time.sleep(0.2)
    sub_done.set()
    t_sub.join(timeout=1)
    pub.close()

    with received_lock:
        msgs = list(received)
    print(f"[e2e] recebidos: {len(msgs)} pacotes")

    fails = 0

    def expect(cond, msg):
        nonlocal fails
        print(f"  {'PASS' if cond else 'FAIL'}  {msg}")
        if not cond:
            fails += 1

    expect(len(msgs) >= 30, f"recebeu pelo menos 30 pacotes (foi {len(msgs)})")
    if msgs:
        last = msgs[-1]
        expect(last.version == 2, f"version = 2 (foi {last.version})")
        expect(last.panel_id == cfg.udp.panel_id,
               f"panel_id = {cfg.udp.panel_id} (foi {last.panel_id})")
        expect(len(last.points) == 2,
               f"último frame com 2 tracks (foi {len(last.points)})")
        for p in last.points:
            expect(500 < abs(p.x_mm) < 3000 or 500 < abs(p.y_mm) < 3000,
                   f"track em coord mm plausível (foi {p.x_mm:.0f},{p.y_mm:.0f})")
        # persistência de id
        recent = [set(p.id for p in m.points) for m in msgs[-10:] if m.points]
        if len(recent) >= 2:
            common = set.intersection(*recent)
            expect(len(common) >= 1,
                   f"ao menos 1 id persistiu nos últimos {len(recent)} frames ({common})")
        # sem publish_rate_hz muito acima do frame de captura
        frames_seq = [m.frame for m in msgs]
        strictly_inc = all(b > a for a, b in zip(frames_seq, frames_seq[1:]))
        expect(strictly_inc, "frame_idx estritamente crescente")

    print(f"[e2e] {'OK' if fails == 0 else 'FAIL'}  ({fails} asserts falharam)")
    return 0 if fails == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
