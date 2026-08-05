"""
LidarMapper — Etapa 1: leitura do RPLIDAR S3.

Conecta o sensor via serial USB e roda a varredura num thread separado.
Expõe medidas ao consumidor (drain), estatísticas (medidas/s, scans/s) e
um campo de status legível pra debug.

A interface foi pensada pra ser estável nas etapas seguintes — o que
muda à frente é o que se faz com as medidas (filtragem, homografia,
clustering, publicação ZMQ), não como elas são lidas.

Quirks do S3 já tratados aqui:
  - Sessão anterior mal encerrada deixa lixo no buffer → stop()+clean_input()
    antes de get_info(); se ainda assim get_info() falhar, reset() + sleep + retry.
  - O logger interno do `rplidar` fala muito a 1 Mbps ("Too many bytes") e
    rouba throughput do hot loop. Silenciamos pra ERROR.
  - O thread de leitura NUNCA morre: soft recover nas primeiras desyncs e
    hard reconnect (recria RPLidar) se persistir. Só sai com stop().
"""
from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass


@dataclass
class Measurement:
    """Uma medida polar do sensor."""
    angle: float
    distance: float
    quality: int
    new_scan: bool
    timestamp: float


def find_lidar_port() -> str | None:
    """Acha a porta do S3 pelo chip CP210x (VID 0x10C4 / PID 0xEA60)."""
    try:
        import serial.tools.list_ports as list_ports
    except ImportError:
        return None
    for p in list_ports.comports():
        if (p.vid, p.pid) == (0x10C4, 0xEA60):
            return p.device
    return None


class LidarReader:
    """Lê o RPLIDAR S3 em thread separada e entrega medidas ao consumidor.

    Uso:
        rdr = LidarReader()      # port=None → auto-detecta
        if not rdr.start():
            raise SystemExit(rdr.status)
        try:
            while True:
                for m in rdr.drain():
                    ...
                time.sleep(0.01)
        finally:
            rdr.stop()
    """

    def __init__(self, port: str | None = None, baud: int = 1000000,
                 max_buf_meas: int = 8000, queue_size: int = 16000) -> None:
        self._port_hint = port
        self._baud = baud
        self._max_buf_meas = max_buf_meas
        self._lidar = None
        self._port = None
        self._thread = None
        self._running = False
        self._lock = threading.Lock()
        self._queue = deque(maxlen=queue_size)
        self._meas_count = 0
        self._scan_count = 0
        self._stats_t0 = time.monotonic()
        self._meas_rate = 0.0
        self._scan_rate = 0.0
        self._last_meas_t = 0.0
        self._reconnects = 0
        self._desyncs = 0
        self._status = "parado"

    def start(self) -> bool:
        """Conecta o sensor e dispara o thread. Retorna True em sucesso."""
        port = self._port_hint or find_lidar_port()
        if not port:
            self._status = "porta não encontrada (driver CP210x instalado? S3 deve aparecer como 'Silicon Labs CP210x ... (COMx)')"
            return False
        self._port = port
        try:
            from rplidar import RPLidar
        except ImportError:
            self._status = "rplidar-roboticia não instalado (pip install rplidar-roboticia)"
            return False
        try:
            self._lidar = RPLidar(port, baudrate=self._baud, timeout=3)
            self._lidar.logger.setLevel(logging.ERROR)
            info, health = self._connect_clean()
            self._status = f"conectado @ {port} | info={info} health={health}"
        except Exception as exc:
            self._status = f"falha ao conectar em {port}: {exc}"
            self._safe_disconnect()
            return False
        self._running = True
        self._stats_t0 = time.monotonic()
        self._thread = threading.Thread(target=self._reader_loop, daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None
        self._safe_disconnect()
        self._status = "parado"

    def _connect_clean(self):
        """stop()+clean_input() antes de get_info(); se o info vier corrompido
        (lixo de scan anterior), reset() + sleep + retry."""
        try:
            self._lidar.stop()
            self._lidar.clean_input()
        except Exception:
            pass
        try:
            info = self._lidar.get_info()
        except Exception:
            self._lidar.reset()
            time.sleep(2)
            self._lidar.clean_input()
            info = self._lidar.get_info()
        health = self._lidar.get_health()
        return (info, health)

    def _safe_disconnect(self) -> None:
        if self._lidar is None:
            return
        try:
            self._lidar.stop()
            self._lidar.stop_motor()
            self._lidar.disconnect()
        except Exception:
            pass
        self._lidar = None

    def _reader_loop(self) -> None:
        """Loop resiliente: soft recover nas primeiras desyncs, hard reconnect
        se persistir. Só sai quando stop() é chamado."""
        fails = 0
        while self._running:
            try:
                lidar = self._lidar
                if lidar is None:
                    self._hard_reconnect()
                    continue
                for meas in lidar.iter_measures(max_buf_meas=self._max_buf_meas):
                    if not self._running:
                        break
                    fails = 0
                    self._on_measure(meas)
            except Exception as exc:
                if not self._running:
                    return
                fails += 1
                self._desyncs += 1
                if fails <= 3:
                    self._status = f"desync; recuperando ({fails})"
                    self._soft_recover()
                    time.sleep(0.05)
                else:
                    self._reconnects += 1
                    self._status = f"desync persistente ({exc}); reconectando (#{self._reconnects})"
                    self._hard_reconnect()
                    fails = 0

    def _soft_recover(self) -> None:
        try:
            self._lidar.stop()
            self._lidar.clean_input()
        except Exception:
            pass

    def _hard_reconnect(self) -> None:
        self._safe_disconnect()
        time.sleep(0.5)
        if not self._running or not self._port:
            return
        try:
            from rplidar import RPLidar
            self._lidar = RPLidar(self._port, baudrate=self._baud, timeout=3)
            self._lidar.logger.setLevel(logging.ERROR)
            self._connect_clean()
            self._status = f"reconectado @ {self._port} (#{self._reconnects})"
        except Exception as exc:
            self._status = f"falha ao reconectar: {exc}"
            time.sleep(1)

    def _on_measure(self, meas: tuple) -> None:
        new_scan, quality, angle, distance = meas
        now = time.monotonic()
        self._last_meas_t = now
        self._meas_count += 1
        if new_scan:
            self._scan_count += 1
        with self._lock:
            self._queue.append(Measurement(angle=float(angle),
                                           distance=float(distance),
                                           quality=int(quality),
                                           new_scan=bool(new_scan),
                                           timestamp=now))
        elapsed = now - self._stats_t0
        if elapsed >= 0.5:
            self._meas_rate = self._meas_count / elapsed
            self._scan_rate = self._scan_count / elapsed
            self._meas_count = 0
            self._scan_count = 0
            self._stats_t0 = now

    def drain(self) -> list[Measurement]:
        """Devolve todas as medidas acumuladas e esvazia a fila."""
        with self._lock:
            out = list(self._queue)
            self._queue.clear()
        return out

    @property
    def meas_per_sec(self) -> float:
        return self._meas_rate

    @property
    def scans_per_sec(self) -> float:
        return self._scan_rate

    @property
    def status(self) -> str:
        return self._status

    @property
    def reconnects(self) -> int:
        return self._reconnects

    @property
    def desyncs(self) -> int:
        return self._desyncs

    @property
    def port(self) -> str | None:
        return self._port

    @property
    def connected(self) -> bool:
        return self._lidar is not None and self._running
