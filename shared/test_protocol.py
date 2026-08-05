"""
Testes de round-trip e casos de borda para shared/protocol.py.

Roda como script sem framework: imprime PASS/FAIL e sai 0/1.

    python shared/test_protocol.py
"""
from __future__ import annotations

import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.protocol import (  # noqa: E402
    V1Point, V2Point, V2_VERSION,
    V1_HEADER_SIZE, V1_POINT_SIZE, V2_HEADER_SIZE, V2_POINT_SIZE,
    ProtocolError,
    pack_v1, unpack_v1, pack_v2, unpack_v2,
)

_fails = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global _fails
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  ({detail})" if detail else ""))
    if not cond:
        _fails += 1


def raises(exc, fn, *args, **kwargs) -> bool:
    try:
        fn(*args, **kwargs)
    except exc:
        return True
    except Exception as e:
        print(f"       (levantou {type(e).__name__}: {e})")
        return False
    return False


# ---------------- V1 ----------------

print("== V1 round-trip ==")

pts0 = []
buf = pack_v1(1, 1.5, pts0)
check("V1 0 pts: tamanho = HEADER", len(buf) == V1_HEADER_SIZE,
      f"{len(buf)} vs {V1_HEADER_SIZE}")
frm = unpack_v1(buf)
check("V1 0 pts: unpack devolve frame/ts/lista vazia",
      frm.frame == 1 and frm.timestamp == 1.5 and frm.points == [])

pts1 = [V1Point(id=42, x=0.25, y=0.75, confidence=0.5)]
buf = pack_v1(7, 12345.678, pts1)
frm = unpack_v1(buf)
check("V1 1 pt: id preservado", frm.points[0].id == 42)
check("V1 1 pt: (x,y,conf) preservados",
      frm.points[0].x == 0.25 and frm.points[0].y == 0.75 and frm.points[0].confidence == 0.5)
check("V1 1 pt: tamanho = 14 + 16*N", len(buf) == V1_HEADER_SIZE + V1_POINT_SIZE)

pts_over = [V1Point(id=i, x=0, y=0, confidence=1) for i in range(50)]
buf = pack_v1(1, 0, pts_over, max_points=32)
frm = unpack_v1(buf)
check("V1 max_points=32: trunca em 32", len(frm.points) == 32)
check("V1 max_points=32: tamanho = 14 + 16*32",
      len(buf) == V1_HEADER_SIZE + 32 * V1_POINT_SIZE)

# id > uint32 range → mascarado com & 0xFFFFFFFF
buf = pack_v1(1, 0, [V1Point(id=0x1_0000_0007, x=0, y=0, confidence=1)])
frm = unpack_v1(buf)
check("V1 id > uint32: mascarado", frm.points[0].id == 7)

# byte-check: compat com o callback V1 do TOUCHDESIGNER.md
ref = struct.pack("<IdH", 1234, 1722902400.5, 2)
ref += struct.pack("<Ifff", 7, 0.25, 0.75, 0.5)
ref += struct.pack("<Ifff", 8, 1.0, 0.0, 1.0)
got = pack_v1(1234, 1722902400.5,
              [V1Point(7, 0.25, 0.75, 0.5), V1Point(8, 1.0, 0.0, 1.0)])
check("V1 byte-idêntico ao TOUCHDESIGNER.md", got == ref)

print("== V1 casos de borda ==")

check("V1 buf muito curto (5 B) → ProtocolError",
      raises(ProtocolError, unpack_v1, b"\x00" * 5))

# header diz N=2 mas body só tem 1 pt
bad = pack_v1(1, 0, [V1Point(1, 0, 0, 1), V1Point(2, 0, 0, 1)])[:-V1_POINT_SIZE]
bad = struct.pack("<IdH", 1, 0.0, 2) + bad[V1_HEADER_SIZE:]
check("V1 tamanho inconsistente (N=2, 1 pt no body) → ProtocolError",
      raises(ProtocolError, unpack_v1, bad))


# ---------------- V2 ----------------

print("== V2 round-trip ==")

buf = pack_v2(panel_id=3, frame=1, timestamp=1.5, points=[])
check("V2 0 pts: tamanho = HEADER", len(buf) == V2_HEADER_SIZE,
      f"{len(buf)} vs {V2_HEADER_SIZE}")
frm = unpack_v2(buf)
check("V2 0 pts: panel_id=3 preservado", frm.panel_id == 3)
check("V2 0 pts: version = V2_VERSION", frm.version == V2_VERSION)

pts = [V2Point(id=1, x_mm=-1234.5, y_mm=678.9, confidence=0.8),
       V2Point(id=2, x_mm=0.0, y_mm=0.0, confidence=1.0)]
buf = pack_v2(panel_id=7, frame=999, timestamp=42.0, points=pts)
frm = unpack_v2(buf)
check("V2 2 pts: panel_id=7 preservado", frm.panel_id == 7)
check("V2 2 pts: (x_mm, y_mm) preservados (float32 tolerance)",
      abs(frm.points[0].x_mm - (-1234.5)) < 1e-3 and
      abs(frm.points[0].y_mm - 678.9) < 1e-3)
check("V2 2 pts: tamanho = 16 + 16*N",
      len(buf) == V2_HEADER_SIZE + 2 * V2_POINT_SIZE)

pts_over = [V2Point(id=i, x_mm=0, y_mm=0, confidence=1) for i in range(50)]
buf = pack_v2(1, 1, 0, pts_over, max_points=32)
frm = unpack_v2(buf)
check("V2 max_points=32: trunca em 32", len(frm.points) == 32)

# max_tracks=10 → 176 bytes (§3 do guia)
buf = pack_v2(1, 1, 0, [V2Point(i, 0, 0, 1) for i in range(10)])
check("V2 10 pts: 176 B (max_tracks do guia)",
      len(buf) == 176, f"{len(buf)} vs 176")

print("== V2 casos de borda ==")

check("V2 buf muito curto (10 B) → ProtocolError",
      raises(ProtocolError, unpack_v2, b"\x00" * 10))

# version errado — strict rejeita
buf = pack_v2(1, 1, 0, [], version=99)
check("V2 version=99 strict → ProtocolError",
      raises(ProtocolError, unpack_v2, buf))
frm = unpack_v2(buf, strict_version=False)
check("V2 version=99 non-strict → devolve version=99",
      frm.version == 99)

# tamanho inconsistente
bad = struct.pack("<BBIdH", V2_VERSION, 1, 1, 0.0, 3) + b"\x00" * V2_POINT_SIZE
check("V2 tamanho inconsistente (N=3, 1 pt no body) → ProtocolError",
      raises(ProtocolError, unpack_v2, bad))

# panel_id fora de uint8 → struct.error
check("V2 panel_id=300 → struct.error",
      raises(struct.error, pack_v2, 300, 1, 0, []))

# byte-check do V2: header 16B + ponto 16B com valores conhecidos
ref = struct.pack("<BBIdH", 2, 5, 42, 1.5, 1)
ref += struct.pack("<Ifff", 1, 100.0, 200.0, 0.5)
got = pack_v2(panel_id=5, frame=42, timestamp=1.5,
              points=[V2Point(id=1, x_mm=100.0, y_mm=200.0, confidence=0.5)])
check("V2 byte-idêntico à referência", got == ref)

print()
print("shared/protocol:", "OK" if _fails == 0 else f"FALHOU ({_fails} asserts)")
sys.exit(0 if _fails == 0 else 1)
