"""
LidarMapper — protocolos LIDAR_MAPPER V1 e V2 (pack/unpack).

ÚNICO lugar do repositório onde format strings de struct podem existir.
node/, server/ e testes importam daqui — é o que garante que simulador,
Pi real e relay nunca divirjam em bytes.

--- V2 (nós Pi → relay, UDP :5555) — §3 do guia ---
  Header 16 B  "<BBIdH"
    uint8    version        (= 2)
    uint8    panel_id       (1..8, definido no config.yaml do nó)
    uint32   frame
    float64  timestamp      (time.time() do Pi — NTP obrigatório)
    uint16   num_points     (N)

  Ponto  16 B  "<Ifff"
    uint32   id             (único por nó, não global)
    float32  x_mm           (referencial do sensor)
    float32  y_mm
    float32  confidence     (0..1)

--- V1 (relay → TouchDesigner, localhost:600N) — §3.1 do guia ---
  Header 14 B  "<IdH"
    uint32   frame
    float64  timestamp
    uint16   num_points     (N)

  Ponto  16 B  "<Ifff"
    uint32   id
    float32  x              (0..1, coordenada normalizada da tela)
    float32  y              (0..1)
    float32  confidence
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field

# fmt: off
_V1_HEADER = struct.Struct("<IdH")     # 14 B
_V1_POINT  = struct.Struct("<Ifff")    # 16 B
_V2_HEADER = struct.Struct("<BBIdH")   # 16 B
_V2_POINT  = struct.Struct("<Ifff")    # 16 B
# fmt: on

V2_VERSION = 2

V1_HEADER_SIZE = _V1_HEADER.size
V1_POINT_SIZE = _V1_POINT.size
V2_HEADER_SIZE = _V2_HEADER.size
V2_POINT_SIZE = _V2_POINT.size


class ProtocolError(ValueError):
    """Erro ao decodificar: tamanho, versão, ou payload inconsistente."""


@dataclass
class V1Point:
    id: int
    x: float
    y: float
    confidence: float


@dataclass
class V1Frame:
    frame: int
    timestamp: float
    points: list[V1Point] = field(default_factory=list)


@dataclass
class V2Point:
    id: int
    x_mm: float
    y_mm: float
    confidence: float


@dataclass
class V2Frame:
    panel_id: int
    frame: int
    timestamp: float
    points: list[V2Point] = field(default_factory=list)
    version: int = V2_VERSION


def pack_v1(frame: int, timestamp: float, points, max_points: int = 32) -> bytes:
    """Empacota um frame V1. `points` é iterável de V1Point (ou objeto com
    .id, .x, .y, .confidence). Trunca em `max_points`."""
    pts = list(points)[:max_points]
    out = bytearray(_V1_HEADER.pack(int(frame), float(timestamp), len(pts)))
    for p in pts:
        out += _V1_POINT.pack(int(p.id) & 0xFFFFFFFF,
                              float(p.x), float(p.y), float(p.confidence))
    return bytes(out)


def unpack_v1(buf: bytes) -> V1Frame:
    """Inverso de pack_v1. Levanta ProtocolError em datagrama inválido."""
    if len(buf) < _V1_HEADER.size:
        raise ProtocolError(f"V1: datagrama curto ({len(buf)} < {_V1_HEADER.size})")
    frame, ts, n = _V1_HEADER.unpack_from(buf, 0)
    expected = _V1_HEADER.size + n * _V1_POINT.size
    if len(buf) != expected:
        raise ProtocolError(f"V1: tamanho inconsistente: header diz {n} pts → "
                            f"{expected} B, recebido {len(buf)} B")
    points = [
        V1Point(*_V1_POINT.unpack_from(buf, _V1_HEADER.size + i * _V1_POINT.size))
        for i in range(n)
    ]
    return V1Frame(frame=int(frame), timestamp=float(ts), points=points)


def pack_v2(panel_id: int, frame: int, timestamp: float, points,
            max_points: int = 32, version: int = V2_VERSION) -> bytes:
    """Empacota um frame V2. `points` é iterável de V2Point (ou objeto com
    .id, .x_mm, .y_mm, .confidence). Trunca em `max_points`.

    struct.error se panel_id/version não caberem em uint8 (0..255)."""
    pts = list(points)[:max_points]
    out = bytearray(_V2_HEADER.pack(int(version), int(panel_id),
                                    int(frame), float(timestamp), len(pts)))
    for p in pts:
        out += _V2_POINT.pack(int(p.id) & 0xFFFFFFFF,
                              float(p.x_mm), float(p.y_mm),
                              float(p.confidence))
    return bytes(out)


def unpack_v2(buf: bytes, *, strict_version: bool = True) -> V2Frame:
    """Inverso de pack_v2. Com strict_version=True (default) levanta
    ProtocolError se version != V2_VERSION — útil pra rejeitar pacotes
    alheios na porta durante a transição V1↔V2."""
    if len(buf) < _V2_HEADER.size:
        raise ProtocolError(f"V2: datagrama curto ({len(buf)} < {_V2_HEADER.size})")
    version, panel_id, frame, ts, n = _V2_HEADER.unpack_from(buf, 0)
    if strict_version and version != V2_VERSION:
        raise ProtocolError(f"V2: version esperado {V2_VERSION}, recebido {version}")
    expected = _V2_HEADER.size + n * _V2_POINT.size
    if len(buf) != expected:
        raise ProtocolError(f"V2: tamanho inconsistente: header diz {n} pts → "
                            f"{expected} B, recebido {len(buf)} B")
    points = [
        V2Point(*_V2_POINT.unpack_from(buf, _V2_HEADER.size + i * _V2_POINT.size))
        for i in range(n)
    ]
    return V2Frame(panel_id=int(panel_id), frame=int(frame),
                   timestamp=float(ts), points=points, version=int(version))
