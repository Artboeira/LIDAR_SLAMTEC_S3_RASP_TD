# Validação global da Sessão W0 — roda com: .venv/bin/python w0_validate.py
# Verifica os critérios do prompt W0 sem hardware:
#   1. import de todos os 16 módulos reconstruídos
#   2. config.load() com o config.yaml REAL do kit (todos os campos)
#   3. homography: load do calibration.json REAL, round-trip sem perda,
#      e compute_homography(corners) reproduz a matriz H salva
#   4. pack_frame V1 byte-idêntico ao formato do TOUCHDESIGNER.md
#   5. test_e2e.py roda e passa (subprocess)
import json
import os
import struct
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
LEGACY = os.path.join(ROOT, "legacy")
KIT = os.path.join(ROOT, "legacy_recovery")
sys.path.insert(0, LEGACY)

fails = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  ({detail})" if detail else ""))
    if not cond:
        fails.append(name)


print("== 1. imports ==")
mods = ["paths", "config", "homography", "processing", "tracker", "publisher",
        "lidar_reader", "main", "calibrate", "test_udp_receiver", "test_e2e",
        "test_lidar", "test_viz", "test_tracker", "test_calib", "ui"]
import importlib
for m in mods:
    try:
        importlib.import_module(m)
        check(f"import {m}", True)
    except Exception as exc:
        check(f"import {m}", False, repr(exc))

print("== 2. config.yaml real do kit ==")
import dataclasses
import config as cfg_mod
cfg = cfg_mod.load(os.path.join(KIT, "config.yaml"))
d = dataclasses.asdict(cfg)
check("config.load() sem erro", True)
import yaml as _yaml
with open(os.path.join(KIT, "config.yaml"), encoding="utf-8") as f:
    raw = _yaml.safe_load(f)
missing = []
for sec, vals in raw.items():
    if not isinstance(vals, dict):
        continue
    for k, v in vals.items():
        if sec not in d or k not in d[sec]:
            missing.append(f"{sec}.{k}")
        elif d[sec][k] != v:
            missing.append(f"{sec}.{k}={d[sec][k]!r}!={v!r}")
check("todos os campos do YAML presentes e iguais no Config", not missing, str(missing))

print("== 3. calibration.json real do kit ==")
import numpy as np
from homography import load_calibration, save_calibration, compute_homography
calib_path = os.path.join(KIT, "calibration.json")
calib = load_calibration(calib_path)
check("load_calibration devolve Calibration", calib is not None)
with open(calib_path, encoding="utf-8") as f:
    orig = json.load(f)
tmp = os.path.join(ROOT, ".w0_calib_roundtrip.json")
save_calibration(tmp, calib)
with open(tmp, encoding="utf-8") as f:
    rt = json.load(f)
os.remove(tmp)
check("round-trip save/load sem perda", rt == orig,
      "" if rt == orig else "diferenças nas chaves: " +
      str([k for k in orig if rt.get(k) != orig[k]]))
H2 = compute_homography(calib.corners_lidar_mm, calib.corners_screen_norm)
same = np.allclose(H2, calib.H, atol=1e-9) or np.allclose(-H2, calib.H, atol=1e-9)
check("compute_homography reproduz H salva (tolerância float)", bool(same),
      f"max|dH|={np.abs(H2 - calib.H).max():.2e}")

print("== 4. datagrama V1 byte-idêntico ao TOUCHDESIGNER.md ==")
from tracker import Track
from publisher import pack_frame
tracks = [
    Track(id=7, x_mm=0, y_mm=0, u=0.25, v=0.75, confidence=0.5),
    Track(id=4294967295 + 8, x_mm=0, y_mm=0, u=1.0, v=0.0, confidence=1.0),
]
got = pack_frame(1234, 1722902400.5, tracks, 32)
# referência montada direto do TOUCHDESIGNER.md: header "<IdH", ponto "<Ifff"
ref = struct.pack("<IdH", 1234, 1722902400.5, 2)
ref += struct.pack("<Ifff", 7, 0.25, 0.75, 0.5)
ref += struct.pack("<Ifff", (4294967295 + 8) & 0xFFFFFFFF, 1.0, 0.0, 1.0)
check("pack_frame == referência byte a byte", got == ref,
      f"{len(got)}B vs {len(ref)}B")
check("tamanho 14 + 16*N", len(got) == 14 + 16 * 2)

print("== 5. test_e2e.py (sem hardware) ==")
r = subprocess.run([sys.executable, "test_e2e.py", "--duration", "3"],
                   cwd=LEGACY, capture_output=True, text=True, timeout=120)
sys.stdout.write(r.stdout)
if r.returncode != 0:
    sys.stdout.write(r.stderr)
check("test_e2e exit 0", r.returncode == 0, f"rc={r.returncode}")

print()
print("W0 VALIDATION:", "OK" if not fails else f"FALHOU: {fails}")
sys.exit(0 if not fails else 1)
