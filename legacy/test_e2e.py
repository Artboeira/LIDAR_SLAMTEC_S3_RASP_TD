"""
LidarMapper — teste E2E final (sem hardware).

Roda o pipeline completo em memória, sem conectar ao sensor: gera medidas
sintéticas (2 "objetos" se movendo em círculos), envia via UDP no mesmo
processo, e um receptor local valida que recebeu pacotes coerentes.

Critérios de sucesso:
  - pelo menos N pacotes recebidos
  - último pacote tem 2 tracks ativos
  - IDs estáveis (frames consecutivos compartilham pelo menos um id)
  - coords (x, y) ∈ [0, 1]
  - tamanho do datagrama bate com o esperado pelo header

Uso:
    python test_e2e.py
    python test_e2e.py --duration 3
"""
from __future__ import annotations

import argparse
import math
import socket
import threading
import time

import numpy as np

import config as cfg_mod
from config import TrackerCfg
from homography import compute_homography
from lidar_reader import Measurement
from processing import BackgroundSubtractor, project_batch, split_by_roi
from publisher import RateLimiter, UdpPublisher, unpack_frame, _HEADER, _POINT
from tracker import Tracker


def make_world() -> tuple[list[tuple[float, float]], np.ndarray]:
    """Tela 'virtual' 2m x 1.13m centrada em (1500, 0) mm no plano do sensor."""
    W, H_ = (2000, 1125)
    cx, cy = (1500, 0)

    def t(u, v):
        return (cx + (u - 0.5) * W, cy + (v - 0.5) * H_)

    corners = [t(0, 0), t(1, 0), t(1, 1), t(0, 1)]
    H = compute_homography(corners, [(0, 0), (1, 0), (1, 1), (0, 1)])
    return (corners, H)


def cartesian_to_polar(x: float, y: float) -> tuple[float, float]:
    a = math.degrees(math.atan2(y, x)) % 360
    d = math.hypot(x, y)
    return (a, d)


def synth_baseline_measurements(now: float) -> list[Measurement]:
    """Fundo: 'paredes' a 6m em volta do sensor."""
    out = []
    for a in range(0, 360, 1):
        ax = 6000 * math.cos(math.radians(a))
        ay = 6000 * math.sin(math.radians(a))
        ang, dist = cartesian_to_polar(ax, ay)
        out.append(Measurement(angle=ang, distance=dist, quality=30,
                               new_scan=a == 0, timestamp=now))
    return out


def synth_cursor_blob(center_xy, n, now, jitter_mm=30,
                      rng: np.random.Generator | None = None):
    if rng is None:
        rng = np.random.default_rng()
    out = []
    for _ in range(n):
        dx, dy = rng.normal(0, jitter_mm, 2)
        x = center_xy[0] + float(dx)
        y = center_xy[1] + float(dy)
        ang, dist = cartesian_to_polar(x, y)
        out.append(Measurement(angle=ang, distance=dist, quality=30,
                               new_scan=False, timestamp=now))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--duration", type=float, default=2)
    ap.add_argument("--port", type=int, default=5599,
                    help="porta UDP pro teste (não conflita com main 5555)")
    ap.add_argument("--baseline-s", type=float, default=0.5)
    args = ap.parse_args()

    print("[e2e] montando mundo sintético...")
    corners, H = make_world()
    print(f"[e2e] cantos (mm): {[(round(p[0]), round(p[1])) for p in corners]}")

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", args.port))
    sock.settimeout(0.1)

    received = []
    received_lock = threading.Lock()
    sub_done = threading.Event()

    def recv_loop():
        while not sub_done.is_set():
            try:
                data, _peer = sock.recvfrom(8192)
            except socket.timeout:
                continue
            try:
                pkt = unpack_frame(data)
                with received_lock:
                    received.append(pkt)
            except ValueError:
                pass
        sock.close()

    t_sub = threading.Thread(target=recv_loop, daemon=True)
    t_sub.start()

    pub = UdpPublisher(host="127.0.0.1", port=args.port, max_points=32)
    print(f"[e2e] publisher em {pub.endpoint}")

    bg = BackgroundSubtractor(bins=720, margin_mm=120)
    bg.begin_baseline()
    bg.configure_time(args.baseline_s)

    trk_cfg = TrackerCfg(dbscan_eps_mm=160, dbscan_min_samples=4,
                         match_dist_mm=300, smoothing=0.2)
    trk = Tracker(trk_cfg)
    cfg = cfg_mod.Config()
    rng = np.random.default_rng(42)

    print("[e2e] alimentando baseline...")
    while not bg.ready:
        bg.feed(synth_baseline_measurements(time.monotonic()))
        time.sleep(0.02)
    print(f"[e2e] baseline pronto ({bg.learned_bins}/720 bins)")

    print(f"[e2e] rodando {args.duration:.1f}s com 2 cursores sintéticos...")
    t_start = time.monotonic()
    limiter = RateLimiter(60)
    while time.monotonic() - t_start < args.duration:
        now = time.monotonic()
        t = now - t_start
        ax = 1200 + 400 * math.cos(t * 2)
        ay = -200 + 400 * math.sin(t * 2)
        bx = 1800 + 250 * math.cos(t * 1.3 + math.pi / 3)
        by = 200 + 250 * math.sin(t * 1.3 + math.pi / 3)
        meas = synth_baseline_measurements(now)
        meas += synth_cursor_blob((ax, ay), 20, now, rng=rng)
        meas += synth_cursor_blob((bx, by), 20, now, rng=rng)
        points = project_batch(meas, cfg.processing)
        fg = bg.foreground_points(points)
        fg_in, _ = split_by_roi(fg, cfg.roi)
        if fg_in:
            xy = np.array([(p.x, p.y) for p in fg_in], dtype=float)
        else:
            xy = np.zeros((0, 2))
        tracks = trk.update(xy, H)
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

    def expect(cond: bool, msg: str):
        nonlocal fails
        if cond:
            print(f"  PASS  {msg}")
        else:
            print(f"  FAIL  {msg}")
            fails += 1

    expect(len(msgs) >= 30, f"recebeu pelo menos 30 pacotes (foi {len(msgs)})")
    if msgs:
        last = msgs[-1]
        expect("frame" in last and "timestamp" in last and "points" in last,
               "payload tem schema {frame, timestamp, points}")
        n_last = len(last["points"])
        expect(n_last == 2, f"último frame tem 2 tracks (teve {n_last}: {last['points']})")
        all_in = all(0 <= p["x"] <= 1 and 0 <= p["y"] <= 1 for p in last["points"])
        expect(all_in, "coords (x,y) do último frame em [0,1]")
        ids_seq = [set(p["id"] for p in m["points"]) for m in msgs[-10:] if m["points"]]
        if len(ids_seq) >= 2:
            common = set.intersection(*ids_seq)
            expect(len(common) >= 1,
                   f"ao menos um ID persistiu nos últimos {len(ids_seq)} frames ({common})")
        confs = [p["confidence"] for p in last["points"]]
        expect(all(0 <= c <= 1 for c in confs),
               f"confidence em [0,1] no último frame (foi {confs})")
        expected_bytes_last = _HEADER.size + len(last["points"]) * _POINT.size
        expect(expected_bytes_last == _HEADER.size + 2 * _POINT.size,
               f"tamanho previsto do datagrama com 2 pts = {expected_bytes_last} B")
    print(f"[e2e] {'OK' if fails == 0 else 'FAIL'}  ({fails} asserts falharam)")
    return 0 if fails == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
