# Validação global da Sessão W2 — roda com: .venv/bin/python w2_validate.py
#
# Cobre o que dá pra automatizar sem Pi, sem TD, sem Max:
#   1. testes unitários de round-trip do shared/protocol.py
#   2. simulador V2 → recebe e demux por panel_id
#   3. relay ponta a ponta: sim → relay → listener V1 + OSC
#      (inclui hot-reload do calib por mtime durante o run)
#   4. coletor do calibrate.py (mediana + filtro por panel_id)
#
# NÃO cobre (só com hardware / infra externa): pygame fullscreen no
# display_index de cada painel, TouchDesigner consumindo V1, Max
# recebendo /touch/N, latência real com NTP entre máquinas.

import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from shared.protocol import unpack_v1, unpack_v2  # noqa: E402
from server.homography import Calibration, compute_homography, save_calibration, load_calibration  # noqa: E402
from server.calibrate import collect_corner  # noqa: E402
import numpy as np  # noqa: E402
import yaml  # noqa: E402

PY = sys.executable
ENV = os.environ.copy()
ENV["PYTHONPATH"] = ROOT

fails = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  ({detail})" if detail else ""))
    if not cond:
        fails.append(name)


def sniff_bg(port, decoder, stop, bucket):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("127.0.0.1", port))
    s.settimeout(0.2)
    while not stop.is_set():
        try:
            data, peer = s.recvfrom(8192)
        except socket.timeout:
            continue
        try:
            bucket.append(decoder(data))
        except Exception:
            pass
    s.close()


def sniff_osc(port, stop, hits):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("127.0.0.1", port))
    s.settimeout(0.2)
    while not stop.is_set():
        try:
            data, _ = s.recvfrom(4096)
        except socket.timeout:
            continue
        addr = data.split(b"\x00", 1)[0].decode(errors="replace")
        if addr.startswith("/touch/"):
            try:
                pid = int(addr.rsplit("/", 1)[1])
                hits[pid] = hits.get(pid, 0) + 1
            except ValueError:
                pass
    s.close()


def make_calib(path, cx=1500):
    W, H = 2000, 1125
    cy = 0
    corners_mm = [(cx - W / 2, cy - H / 2), (cx + W / 2, cy - H / 2),
                  (cx + W / 2, cy + H / 2), (cx - W / 2, cy + H / 2)]
    corners_norm = [(0, 0), (1, 0), (1, 1), (0, 1)]
    H_mat = compute_homography(corners_mm, corners_norm)
    save_calibration(path, Calibration(
        H=H_mat, corners_lidar_mm=corners_mm,
        corners_screen_norm=corners_norm,
        screen_width_px=1920, screen_height_px=1080))


# ---------------- 1. protocol ----------------
print("== 1. shared/protocol.py — testes unitários ==")
r = subprocess.run([PY, "shared/test_protocol.py"], capture_output=True, text=True)
check("shared/test_protocol.py exit 0", r.returncode == 0,
      f"rc={r.returncode}\n{r.stdout[-500:] if r.returncode else ''}")

# ---------------- 2. simulador ----------------
print("== 2. simulador V2 ==")
port = 25601
stop = threading.Event()
bucket = []
t = threading.Thread(target=sniff_bg, args=(port, unpack_v2, stop, bucket), daemon=True)
t.start()
time.sleep(0.1)
subprocess.run([PY, "server/test_node_sim.py", "--panels", "1,2,3,4",
                "--port", str(port), "--quiet", "--duration", "1.0"],
               env=ENV, check=True, stdout=subprocess.DEVNULL)
time.sleep(0.2)
stop.set()
t.join(timeout=1)
check("simulador: >=100 pkts em 1s a 30 Hz x 4 painéis", len(bucket) >= 100,
      f"veio {len(bucket)}")
check("simulador: 4 panel_ids distintos",
      {f.panel_id for f in bucket} == {1, 2, 3, 4},
      str(sorted({f.panel_id for f in bucket})))

# ---------------- 3. relay ponta a ponta ----------------
print("== 3. relay ponta a ponta + hot-reload ==")
tmp = tempfile.mkdtemp(prefix="w2_")
cfg_path = os.path.join(tmp, "config_server.yaml")
calib1 = os.path.join(tmp, "calib_p1.json")
calib2 = os.path.join(tmp, "calib_p2.json")
make_calib(calib1, cx=1500)
make_calib(calib2, cx=1500)
with open(cfg_path, "w") as f:
    yaml.safe_dump({
        "listen_port": 25655,
        "panels": {
            1: {"out_port": 26001, "display_index": 1, "calib_file": calib1},
            2: {"out_port": 26002, "display_index": 2, "calib_file": calib2},
        },
        "osc": {"host": "127.0.0.1", "port": 27500, "timeout_s": 0.18},
        "td": {"host": "127.0.0.1", "clip_out_of_range": True},
        "calibration": {"collect_s": 2.0, "window_mm": 250,
                        "min_pts": 30, "target_insets": [0.06, 0.94]},
    }, f)

stop = threading.Event()
v1_p1, v1_p2 = [], []
osc_hits = {}
threads = [
    threading.Thread(target=sniff_bg, args=(26001, unpack_v1, stop, v1_p1), daemon=True),
    threading.Thread(target=sniff_bg, args=(26002, unpack_v1, stop, v1_p2), daemon=True),
    threading.Thread(target=sniff_osc, args=(27500, stop, osc_hits), daemon=True),
]
for th in threads:
    th.start()
time.sleep(0.15)

relay = subprocess.Popen([PY, "server/server_relay.py",
                          "--config", cfg_path, "--log-level", "warning"],
                         env=ENV, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
time.sleep(0.4)
sim = subprocess.Popen([PY, "server/test_node_sim.py",
                        "--panels", "1,2", "--port", "25655",
                        "--quiet", "--duration", "1.0"],
                       env=ENV, stdout=subprocess.DEVNULL)
sim.wait()

# hot-reload: mudar o calib_p1 (mesmo H, mtime diferente) enquanto o relay roda
time.sleep(0.2)
before_p1 = len(v1_p1)
make_calib(calib1, cx=1500)  # regrava (mtime muda)
sim = subprocess.Popen([PY, "server/test_node_sim.py",
                        "--panels", "1,2", "--port", "25655",
                        "--quiet", "--duration", "0.5"],
                       env=ENV, stdout=subprocess.DEVNULL)
sim.wait()
after_p1 = len(v1_p1)

time.sleep(0.3)
relay.terminate()
try:
    relay.wait(timeout=2)
except subprocess.TimeoutExpired:
    relay.kill()
stop.set()
for th in threads:
    th.join(timeout=1)

check("V1 painel 1 (>=25 pkts na 1a rodada)", before_p1 >= 25, f"veio {before_p1}")
check("V1 painel 2 (>=25 pkts)", len(v1_p2) >= 25, f"veio {len(v1_p2)}")
check("V1 painel 1 acumulou mais pkts após o hot-reload",
      after_p1 > before_p1, f"{before_p1} -> {after_p1}")

for pid, samples in ((1, v1_p1), (2, v1_p2)):
    if samples:
        last = samples[-1]
        all_in = all(0 <= p.x <= 1 and 0 <= p.y <= 1 for p in last.points)
        check(f"V1 painel {pid}: último frame em [0..1]", all_in,
              str([(round(p.x, 3), round(p.y, 3)) for p in last.points]))
        check(f"OSC /touch/{pid} disparou (down)", osc_hits.get(pid, 0) >= 1,
              f"hits={osc_hits.get(pid, 0)}")
        check(f"debounce painel {pid}: /touch <= 4 hits em circle",
              osc_hits.get(pid, 0) <= 4, f"hits={osc_hits.get(pid, 0)}")

# ---------------- 4. coletor do calibrate ----------------
print("== 4. coletor do calibrate.py ==")
port = 25701
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
sock.bind(("127.0.0.1", port))
sim = subprocess.Popen([PY, "server/test_node_sim.py",
                        "--panels", "3,5", "--port", str(port), "--pattern", "static",
                        "--quiet", "--duration", "0.8"],
                       env=ENV, stdout=subprocess.DEVNULL)
time.sleep(0.15)
mx, my, n = collect_corner(sock, panel_id=5, collect_s=0.6, min_pts=5)
sim.wait()
sock.close()
check("coletor: mediana correta do painel 5 (ignora painel 3)",
      mx == 1500.0 and my == 0.0 and n >= 5, f"({mx}, {my}) n={n}")

print()
print("W2 VALIDATION:", "OK" if not fails else f"FALHOU: {fails}")
sys.exit(0 if not fails else 1)
