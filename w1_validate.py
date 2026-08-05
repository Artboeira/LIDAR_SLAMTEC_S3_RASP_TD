# Validação global da Sessão W1 — roda com: .venv/bin/python w1_validate.py
#
# Sem hardware (não requer o RPLIDAR S3 na USB):
#   1. config.load valida panel_id 1..8 (obrigatório)
#   2. parser vetorizado decodifica um blob RPLIDAR sintético byte a byte
#   3. bench_parse rápido: throughput mínimo aceitável na máquina dev
#   4. test_e2e do node: pipeline com medidas sintéticas + publicação V2
#   5. cross-check com W2: node → server_relay → V1 chega no TD listener
#
# NÃO cobre (só com o S3 na USB):
#   test_lidar.py, meas/s real, scans/s real, resiliência (unplug/replug).

import os
import socket
import subprocess
import sys
import tempfile
import threading
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "node"))

import numpy as np  # noqa: E402
import yaml  # noqa: E402

from shared.protocol import unpack_v1, unpack_v2  # noqa: E402
from server.homography import Calibration, compute_homography, save_calibration  # noqa: E402

PY = sys.executable
ENV = os.environ.copy()
ENV["PYTHONPATH"] = ROOT + os.pathsep + os.path.join(ROOT, "node")

fails: list[str] = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  ({detail})" if detail else ""))
    if not cond:
        fails.append(name)


# ---------------- 1. config valida panel_id ----------------
print("== 1. config.load valida panel_id obrigatório ==")
tmp = tempfile.mkdtemp(prefix="w1_")
cfg_bad = os.path.join(tmp, "bad.yaml")
cfg_ok = os.path.join(tmp, "ok.yaml")
with open(cfg_bad, "w") as f:
    yaml.safe_dump({"udp": {"host": "x", "port": 5555}}, f)  # sem panel_id
with open(cfg_ok, "w") as f:
    yaml.safe_dump({"udp": {"host": "x", "port": 5555, "panel_id": 3}}, f)

import config as node_cfg  # noqa: E402
raised = False
try:
    node_cfg.load(cfg_bad)
except ValueError as exc:
    raised = "panel_id" in str(exc)
check("panel_id ausente → ValueError com mensagem clara", raised)
cfg = node_cfg.load(cfg_ok)
check("panel_id=3 aceito", cfg.udp.panel_id == 3)


# ---------------- 2. parser vetorizado ----------------
print("== 2. parser numpy (bit-exact) ==")
from lidar_reader import _parse_legacy_scan  # noqa: E402

def enc(angle_deg, dist_mm, quality, new_scan):
    aq = int(angle_deg * 64) & 0x7FFF
    dq = int(dist_mm * 4) & 0xFFFF
    s = 1 if new_scan else 0
    b0 = ((quality & 0x3F) << 2) | (s & 1) | ((1 - s) << 1)
    b1 = ((aq & 0x7F) << 1) | 1
    b2 = (aq >> 7) & 0xFF
    return bytes([b0, b1, b2, dq & 0xFF, (dq >> 8) & 0xFF])

buf = b"".join(enc(i * 3.6, 1000.0 + i, 25, i == 0) for i in range(200))
a, d, q, ns, v = _parse_legacy_scan(buf)
check("todas as 200 amostras válidas", bool(v.all()))
check("distâncias exatas (bits inteiros)",
      float(np.abs(d - (1000.0 + np.arange(200))).max()) == 0.0)
check("quality preservado", bool((q == 25).all()))
check("new_scan só no primeiro sample",
      bool(ns[0]) and bool((~ns[1:]).all()))
# corrompe um bit de check → invalida sample isolado
bad = bytearray(buf)
bad[1] &= 0xFE  # zero no check bit do sample 0
_, _, _, _, v2 = _parse_legacy_scan(bytes(bad))
check("sample corrompido detectado (check bit)",
      not bool(v2[0]) and bool(v2[1:].all()))


# ---------------- 3. bench rápido ----------------
print("== 3. bench_parse (dev sanity) ==")
r = subprocess.run([PY, "node/bench_parse.py", "--duration", "1.2"],
                   env=ENV, capture_output=True, text=True)
print(r.stdout.rstrip())
check("bench_parse exit 0", r.returncode == 0)
# extrai throughput da linha "throughput=X samples/s"
line = next((ln for ln in r.stdout.splitlines() if "throughput" in ln), "")
throughput = 0
try:
    throughput = int(line.split("throughput=")[1].split()[0].replace(",", ""))
except (IndexError, ValueError):
    pass
check(f"throughput dev >= 100k samples/s ({throughput:,})", throughput >= 100_000)


# ---------------- 4. test_e2e do node ----------------
print("== 4. node/test_e2e.py (pipeline sem hardware) ==")
r = subprocess.run([PY, "node/test_e2e.py", "--duration", "1.5"],
                   env=ENV, capture_output=True, text=True, timeout=60)
sys.stdout.write(r.stdout)
if r.returncode:
    sys.stdout.write(r.stderr)
check("test_e2e exit 0", r.returncode == 0)


# ---------------- 5. cross-check com W2 ----------------
print("== 5. cross-check: (sim node V2 → server_relay → TD listener V1) ==")
# aqui NÃO uso o node/main.py (precisaria do S3); uso o simulador do W2
# como stand-in do node, e mostro que o relay decodifica e reempaqueta.
tmp2 = tempfile.mkdtemp(prefix="w1_cross_")
cfg_srv = os.path.join(tmp2, "server.yaml")
calib1 = os.path.join(tmp2, "calib_p1.json")

# calib idêntica ao world do test_e2e: (500,-562)..(2500,562) → 0..1
W, H = 2000, 1125
cx, cy = 1500, 0
corners = [(cx - W / 2, cy - H / 2), (cx + W / 2, cy - H / 2),
           (cx + W / 2, cy + H / 2), (cx - W / 2, cy + H / 2)]
save_calibration(calib1, Calibration(
    H=compute_homography(corners, [(0, 0), (1, 0), (1, 1), (0, 1)]),
    corners_lidar_mm=corners,
    corners_screen_norm=[(0, 0), (1, 0), (1, 1), (0, 1)],
    screen_width_px=1920, screen_height_px=1080))

with open(cfg_srv, "w") as f:
    yaml.safe_dump({
        "listen_port": 25655,
        "panels": {1: {"out_port": 26001, "display_index": 1, "calib_file": calib1}},
        "osc": {"host": "127.0.0.1", "port": 27500, "timeout_s": 0.18},
        "td": {"host": "127.0.0.1", "clip_out_of_range": True},
        "calibration": {"collect_s": 2.0, "window_mm": 250,
                        "min_pts": 30, "target_insets": [0.06, 0.94]},
    }, f)

v1_frames: list = []
stop_evt = threading.Event()

def sniff_v1():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("127.0.0.1", 26001))
    s.settimeout(0.2)
    while not stop_evt.is_set():
        try:
            data, _ = s.recvfrom(8192)
        except socket.timeout:
            continue
        try:
            v1_frames.append(unpack_v1(data))
        except Exception:
            pass
    s.close()

t = threading.Thread(target=sniff_v1, daemon=True)
t.start()
time.sleep(0.1)

relay = subprocess.Popen(
    [PY, "server/server_relay.py", "--config", cfg_srv, "--log-level", "warning"],
    env=ENV, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(0.4)
sim = subprocess.Popen(
    [PY, "server/test_node_sim.py", "--panels", "1", "--port", "25655",
     "--quiet", "--duration", "1.0"],
    env=ENV, stdout=subprocess.DEVNULL)
sim.wait()
time.sleep(0.3)
relay.terminate()
try:
    relay.wait(timeout=2)
except subprocess.TimeoutExpired:
    relay.kill()
stop_evt.set()
t.join(timeout=1)

check("W1→W2 (via simulador): pelo menos 25 pacotes V1", len(v1_frames) >= 25,
      f"veio {len(v1_frames)}")
if v1_frames:
    last = v1_frames[-1]
    all_in = all(0 <= p.x <= 1 and 0 <= p.y <= 1 for p in last.points)
    check("W1→W2: último frame V1 em [0..1]", all_in)

print()
print("W1 VALIDATION:", "OK" if not fails else f"FALHOU: {fails}")
sys.exit(0 if not fails else 1)
