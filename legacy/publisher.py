"""
LidarMapper — Etapa 5: publisher UDP binário.

Empacota os tracks num datagrama binário com `struct.pack` (little-endian,
sem padding) e envia via UDP. Formato pensado pra ser consumido por um
UDP In DAT no TouchDesigner com um callback que faz `struct.unpack`.

Formato (LIDAR_MAPPER_V1):

  Header (14 bytes):
    uint32   frame
    float64  timestamp           (segundos desde epoch, time.time())
    uint16   num_points          (N)

  Por ponto (16 bytes, repetido N vezes):
    uint32   id
    float32  x                   (0..1, espaço normalizado da tela)
    float32  y                   (0..1)
    float32  confidence          (0..1)

  Tamanho do datagrama = 14 + 16 * N bytes.
  Com max_tracks=10  → 174 B, bem abaixo do MTU típico (1500 B), sem fragmentação.
  O cap `max_points` no config é uma cinta extra (trunca se passar disso).

Atenção: UDP é "best-effort" (sem retransmissão, ordem não garantida).
Pra esse caso (estado completo a cada frame), perder um pacote significa
ficar uma "frame" atrasado e o próximo já carrega o estado novo. Aceitável.
"""
from __future__ import annotations

import socket
import struct
import time

from tracker import Track

_HEADER = struct.Struct("<IdH")
_POINT = struct.Struct("<Ifff")


def pack_frame(frame_idx: int, timestamp: float, tracks: list[Track],
               max_points: int = 32) -> bytes:
    """Empacota um frame no formato LIDAR_MAPPER_V1."""
    pts = tracks[:max_points]
    out = bytearray(_HEADER.pack(frame_idx, timestamp, len(pts)))
    for t in pts:
        out += _POINT.pack(int(t.id) & 0xFFFFFFFF, float(t.u), float(t.v),
                           float(t.confidence))
    return bytes(out)


def unpack_frame(buf: bytes) -> dict:
    """Inverso de pack_frame. Útil pra testes e pro receptor de validação."""
    if len(buf) < _HEADER.size:
        raise ValueError(f"datagrama muito curto ({len(buf)} < {_HEADER.size})")
    frame, ts, n = _HEADER.unpack_from(buf, 0)
    expected = _HEADER.size + n * _POINT.size
    if len(buf) != expected:
        raise ValueError(f"tamanho inconsistente: header diz {n} pts → "
                         f"{expected} B, recebido {len(buf)} B")
    points = []
    for i in range(n):
        off = _HEADER.size + i * _POINT.size
        pid, x, y, conf = _POINT.unpack_from(buf, off)
        points.append({"id": pid, "x": x, "y": y, "confidence": conf})
    return {"frame": frame, "timestamp": ts, "points": points}


class UdpPublisher:
    """Envia datagramas UDP binários pra um host:port (unicast)."""

    def __init__(self, host: str = "127.0.0.1", port: int = 5555,
                 max_points: int = 32) -> None:
        self.host = host
        self.port = port
        self.max_points = max_points
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        except OSError:
            pass
        self.endpoint = f"udp://{host}:{port}"
        self._frame = 0
        self._sent = 0
        self._t0 = time.monotonic()
        self._send_rate = 0.0

    def publish(self, tracks: list[Track]) -> int:
        """Empacota e envia. Retorna o frame number publicado."""
        self._frame += 1
        body = pack_frame(self._frame, time.time(), tracks, self.max_points)
        try:
            self.sock.sendto(body, (self.host, self.port))
        except OSError:
            pass
        self._sent += 1
        now = time.monotonic()
        elapsed = now - self._t0
        if elapsed >= 1.0:
            self._send_rate = self._sent / elapsed
            self._sent = 0
            self._t0 = now
        return self._frame

    @property
    def send_rate(self) -> float:
        return self._send_rate

    @property
    def frame(self) -> int:
        return self._frame

    def close(self) -> None:
        try:
            self.sock.close()
        except Exception:
            pass


class RateLimiter:
    """Limita uma chamada periódica a uma taxa máxima (Hz). 0 = sem limite."""

    def __init__(self, rate_hz: float):
        self.set_rate(rate_hz)
        self._next = 0.0

    def set_rate(self, rate_hz: float) -> None:
        if rate_hz <= 0:
            self.period = 0.0
            return
        self.period = 1 / rate_hz

    def ready(self, now: float | None = None) -> bool:
        if self.period <= 0:
            return True
        t = time.monotonic() if now is None else now
        if t >= self._next:
            self._next = t + self.period
            return True
        return False
