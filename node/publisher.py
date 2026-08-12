"""
LidarMapper node — publisher UDP V2 (§5.1 item 1 do guia).

Publica os tracks em mm no plano do sensor (SEM homografia — o relay
aplica a H) via `shared/protocol.pack_v2` com `version=2` e o
`panel_id` do config.yaml.

Mantém o `RateLimiter` do legacy (limita frequência de publicação a
`udp.publish_rate_hz` — 30 Hz por default, §9 do guia).

NENHUM format string de struct aqui: tudo passa por `shared/protocol`.
"""
from __future__ import annotations

import os
import socket
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.protocol import V2Point, pack_v2  # noqa: E402
from tracker import Track  # noqa: E402


class UdpPublisher:
    """Envia datagramas V2 pra host:port (unicast). O panel_id é fixo
    por instância — cada nó carrega um."""

    def __init__(self, host: str, port: int, panel_id: int,
                 max_points: int = 32):
        if not 1 <= panel_id <= 8:
            raise ValueError(f"panel_id deve estar em 1..8, veio {panel_id}")
        self.host = host
        self.port = port
        self.panel_id = panel_id
        self.max_points = max_points
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        except OSError:
            pass
        self.endpoint = f"udp://{host}:{port} (panel_id={panel_id})"
        self._frame = 0
        self._sent = 0
        self._t0 = time.monotonic()
        self._send_rate = 0.0

    def publish(self, tracks: list[Track]) -> int:
        """Empacota V2 e envia. Retorna o frame number publicado."""
        self._frame += 1
        pts = [V2Point(id=t.id, x_mm=t.x_mm, y_mm=t.y_mm,
                       confidence=t.confidence) for t in tracks]
        body = pack_v2(panel_id=self.panel_id, frame=self._frame,
                       timestamp=time.time(), points=pts,
                       max_points=self.max_points)
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
    """Limita chamadas periódicas a `rate_hz`.

    DIVERGÊNCIA DO LEGACY (bugfix 08/2026): o legacy ancorava o próximo
    disparo em `agora + período`, herdando a cada frame o atraso do sleep
    do loop chamador (~3 ms) — 30 Hz pedidos viravam ~27,3 Hz medidos no
    nó e no receiver. Ancorar no deadline anterior elimina a deriva; se o
    chamador atrasar mais que um período inteiro, re-ancora em `agora`
    para não disparar rajada de recuperação."""

    def __init__(self, rate_hz: float):
        self.set_rate(rate_hz)
        self._next = 0.0

    def set_rate(self, rate_hz: float) -> None:
        self.period = 0.0 if rate_hz <= 0 else 1.0 / rate_hz

    def ready(self, now: float | None = None) -> bool:
        if self.period <= 0:
            return True
        t = time.monotonic() if now is None else now
        if t >= self._next:
            self._next += self.period
            if t >= self._next:          # atrasou 1+ período: re-ancora
                self._next = t + self.period
            return True
        return False
