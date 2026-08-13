"""
LidarMapper node — leitura vetorizada do RPLIDAR S3 (§5.0 do guia).

Diferente do legacy (que usava `RPLidar.iter_measures()` — parsing byte a
byte em Python puro, gargalo do 3B+), aqui:

  - conecta o sensor via `rplidar-roboticia` só para setup
    (get_info, get_health, motor)
  - envia `START_SCAN` (\\xa5\\x20) direto pelo serial cru
  - lê a serial em blocos grandes (`serial.read(chunk_bytes)`)
  - parseia com `numpy.frombuffer` + máscaras vetorizadas de bits
  - devolve arrays (N, 3) [angle_deg, distance_mm, quality] pelo `drain()`

Legacy Scan Mode do RPLIDAR (5 bytes/amostra):
  byte 0:  S | !S | quality[5..0]        (S=new scan, !S=complemento)
  byte 1:  angle_q6[6..0] | C            (C=check bit, deve ser 1)
  byte 2:  angle_q6[13..7]
  byte 3:  distance_q2 low byte
  byte 4:  distance_q2 high byte
  angle°   = ((byte2 << 7) | (byte1 >> 1)) / 64.0
  dist_mm  = (byte3 | (byte4 << 8)) / 4.0
  quality  = byte0 >> 2
  valid    = (S XOR !S == 1) & (C == 1)

Resync: se a fração de amostras válidas cai abaixo de 0.5 num chunk, a
gente tenta shifts de 1..4 bytes; se nenhum bater 0.9, dropa o chunk
todo e resincroniza no próximo.
"""
from __future__ import annotations

import logging
import threading
import time
from collections import deque

import numpy as np


_CMD_STOP = b"\xa5\x25"
_CMD_START_SCAN = b"\xa5\x20"
_CMD_RESET = b"\xa5\x40"


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


def _parse_legacy_scan(buf: bytes):
    """`buf` deve ser múltiplo de 5. Devolve
    (angle_deg, distance_mm, quality, new_scan, valid) — 5 arrays de
    tamanho N = len(buf)//5. Não valida nem filtra — só decodifica."""
    n = len(buf) // 5
    if n == 0:
        z = np.empty(0, dtype=np.float32)
        return (z, z, np.empty(0, dtype=np.int32),
                np.empty(0, dtype=bool), np.empty(0, dtype=bool))
    arr = np.frombuffer(buf[:n * 5], dtype=np.uint8).reshape(n, 5)
    b0 = arr[:, 0]
    b1 = arr[:, 1]
    b2 = arr[:, 2]
    b3 = arr[:, 3]
    b4 = arr[:, 4]
    s_bit = (b0 & 0x01).astype(bool)
    s_not = ((b0 >> 1) & 0x01).astype(bool)
    v0 = s_bit ^ s_not
    v1 = (b1 & 0x01) == 1
    valid = v0 & v1
    quality = (b0 >> 2).astype(np.int32)
    angle_q6 = (b2.astype(np.uint32) << 7) | (b1.astype(np.uint32) >> 1)
    angle_deg = (angle_q6.astype(np.float32) / 64.0)
    distance_q2 = b3.astype(np.uint32) | (b4.astype(np.uint32) << 8)
    distance_mm = (distance_q2.astype(np.float32) / 4.0)
    return angle_deg, distance_mm, quality, s_bit, valid


def _lidar_serial(lidar) -> object:
    """rplidar-roboticia expõe o `serial.Serial` interno em
    `_serial_port`; algumas versões usam `_serial`. Tenta os dois."""
    for name in ("_serial_port", "_serial"):
        ser = getattr(lidar, name, None)
        if ser is not None:
            return ser
    raise RuntimeError("rplidar não expõe o serial interno "
                       "(_serial_port / _serial)")


class LidarReader:
    """Leitor vetorizado. Roda o parser em thread separada; consumidor
    chama `drain()` a partir do thread principal.

    Uso:
        rdr = LidarReader(port=None)   # auto-detecta CP210x
        if not rdr.start():
            raise SystemExit(rdr.status)
        try:
            while True:
                arr = rdr.drain()      # (N, 3) [angle, dist, quality]
                ...
                time.sleep(0.005)
        finally:
            rdr.stop()
    """

    def __init__(self, port: str | None = None, baud: int = 1000000,
                 chunk_bytes: int = 4096, max_backlog_samples: int = 65536):
        self._port_hint = port
        self._baud = baud
        self._chunk = chunk_bytes
        self._max_backlog = max_backlog_samples
        self._lidar = None
        self._serial = None
        self._port: str | None = None
        self._thread: threading.Thread | None = None
        self._running = False
        self._lock = threading.Lock()
        # arrays acumulados entre drains — deque de np.ndarray
        self._parts_a: deque = deque()
        self._parts_d: deque = deque()
        self._parts_q: deque = deque()
        self._carry: bytes = b""
        # estatísticas
        self._meas_count = 0
        self._scan_count = 0
        self._meas_rate = 0.0
        self._scan_rate = 0.0
        self._stats_t0 = 0.0
        self._reconnects = 0
        self._desyncs = 0
        self._status = "parado"

    # ---------------- lifecycle ----------------

    def start(self) -> bool:
        port = self._port_hint or find_lidar_port()
        if not port:
            self._status = ("porta não encontrada (driver CP210x instalado? "
                            "S3 deve aparecer como 'Silicon Labs CP210x' ou "
                            "no udev /dev/rplidar)")
            return False
        try:
            from rplidar import RPLidar
        except ImportError:
            self._status = "rplidar-roboticia não instalado (pip install rplidar-roboticia)"
            return False
        try:
            self._lidar = RPLidar(port, baudrate=self._baud, timeout=1.0)
            try:
                self._lidar.logger.setLevel(logging.ERROR)
            except AttributeError:
                pass
            self._connect_clean()
            self._serial = _lidar_serial(self._lidar)
            try:
                self._lidar.start_motor()
            except Exception:
                pass
            # espera o motor estabilizar
            time.sleep(0.5)
            # S3 pode acordar TRAVADO após corte de energia (visto em campo
            # nos painéis 4 e 6: START_SCAN responde vazio para sempre, e o
            # serviço entrava em crash-loop até um RESET manual). Com
            # desligamento diário do evento isso precisa se auto-curar:
            # até 3 tentativas, com STOP+RESET (A5 40) entre elas.
            hdr = b""
            for _attempt in range(3):
                self._serial.reset_input_buffer()
                self._serial.write(_CMD_START_SCAN)
                self._serial.flush()
                hdr = self._serial.read(7)
                if len(hdr) == 7 and hdr[0] == 0xA5 and hdr[1] == 0x5A:
                    break
                self._serial.write(_CMD_STOP)
                self._serial.flush()
                time.sleep(0.1)
                self._serial.write(_CMD_RESET)
                self._serial.flush()
                time.sleep(2.5)          # reboot interno do sensor
            else:
                self._status = (f"resposta START_SCAN inválida após 3 "
                                f"tentativas com RESET: {hdr.hex()}")
                self._safe_disconnect()
                return False
            self._port = port
            self._status = f"conectado @ {port} (parser vetorizado)"
        except Exception as exc:
            self._status = f"falha na conexão: {exc}"
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
        try:
            if self._serial is not None:
                self._serial.write(_CMD_STOP)
                self._serial.flush()
                time.sleep(0.01)
        except Exception:
            pass
        self._safe_disconnect()
        self._status = "parado"

    def _connect_clean(self) -> None:
        try:
            self._lidar.stop()
            self._lidar.clean_input()
        except Exception:
            pass
        try:
            self._lidar.get_info()
        except Exception:
            try:
                self._lidar.reset()
            except Exception:
                pass
            time.sleep(2)
            try:
                self._lidar.clean_input()
                self._lidar.get_info()
            except Exception:
                pass
        try:
            self._lidar.get_health()
        except Exception:
            pass

    def _safe_disconnect(self) -> None:
        try:
            if self._lidar is not None:
                self._lidar.stop_motor()
                self._lidar.disconnect()
        except Exception:
            pass
        self._lidar = None
        self._serial = None

    # ---------------- hot path ----------------

    def _reader_loop(self) -> None:
        """Único loop de leitura. Puxa blocos grandes do serial, parseia
        vetorizado, empurra pra fila do drain."""
        while self._running:
            try:
                data = self._serial.read(self._chunk)
            except Exception as exc:
                if not self._running:
                    return
                self._desyncs += 1
                self._status = f"read fail: {exc}"
                time.sleep(0.05)
                continue
            if not data:
                # timeout curto do pyserial (1s no start): normal se está
                # entre scans; só olha stats e continua
                self._maybe_update_stats(0)
                continue
            self._carry += data
            n = len(self._carry) // 5
            if n == 0:
                continue
            usable = self._carry[:n * 5]
            self._carry = self._carry[n * 5:]
            angle, dist, quality, new_scan, valid = _parse_legacy_scan(usable)

            # resync se muitos bits inválidos
            if n > 20 and float(valid.mean()) < 0.5:
                self._desyncs += 1
                shifted = False
                for shift in (1, 2, 3, 4):
                    tail = self._carry
                    trial = usable[shift:] + tail
                    m = len(trial) // 5
                    if m == 0:
                        continue
                    _, _, _, _, v = _parse_legacy_scan(trial[:m * 5])
                    if float(v.mean()) > 0.9:
                        self._carry = trial[m * 5:]
                        angle, dist, quality, new_scan, valid = _parse_legacy_scan(
                            trial[:m * 5])
                        shifted = True
                        break
                if not shifted:
                    self._carry = b""
                    continue

            # O S-bit de nova rotação dispara num rumo fixo da torre; se essa
            # direção não tem eco (dist=0), filtrar antes de contar zeraria o
            # scans/s toda volta. Conta sobre as VÁLIDAS, com ou sem eco.
            self._scan_count += int(new_scan[valid].sum())

            keep = valid & (dist > 0)
            n_kept = int(keep.sum())
            if n_kept == 0:
                self._maybe_update_stats(0)
                continue
            a_ = angle[keep]
            d_ = dist[keep]
            q_ = quality[keep]

            with self._lock:
                self._parts_a.append(a_)
                self._parts_d.append(d_)
                self._parts_q.append(q_)
                total = sum(a.size for a in self._parts_a)
                while total > self._max_backlog and self._parts_a:
                    dropped = self._parts_a.popleft().size
                    self._parts_d.popleft()
                    self._parts_q.popleft()
                    total -= dropped

            self._meas_count += n_kept
            self._maybe_update_stats(n_kept)

    def _maybe_update_stats(self, _n: int) -> None:
        now = time.monotonic()
        elapsed = now - self._stats_t0
        if elapsed >= 0.5:
            self._meas_rate = self._meas_count / elapsed
            self._scan_rate = self._scan_count / elapsed
            self._meas_count = 0
            self._scan_count = 0
            self._stats_t0 = now

    # ---------------- consumer API ----------------

    def drain(self) -> np.ndarray:
        """Retorna array (N, 3) [angle_deg, distance_mm, quality] com
        todas as amostras acumuladas desde o último drain. Esvazia."""
        with self._lock:
            if not self._parts_a:
                return np.zeros((0, 3), dtype=np.float32)
            a = np.concatenate(list(self._parts_a))
            d = np.concatenate(list(self._parts_d))
            q = np.concatenate(list(self._parts_q))
            self._parts_a.clear()
            self._parts_d.clear()
            self._parts_q.clear()
        out = np.empty((a.size, 3), dtype=np.float32)
        out[:, 0] = a
        out[:, 1] = d
        out[:, 2] = q
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
    def desyncs(self) -> int:
        return self._desyncs

    @property
    def reconnects(self) -> int:
        return self._reconnects

    @property
    def port(self) -> str | None:
        return self._port

    @property
    def connected(self) -> bool:
        return self._serial is not None and self._running
