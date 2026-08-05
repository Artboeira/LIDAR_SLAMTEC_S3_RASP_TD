"""
LidarMapper node — teste do RPLIDAR S3 (só com sensor real).

Conecta, roda o parser vetorizado e imprime:
  - status da conexão
  - meas/s (throughput real do sensor, após parse + validação)
  - scans/s (~ rotações/s do S3)
  - amostra rotativa das últimas medidas (angle°, distance mm, quality)
  - reconnects / desyncs (estabilidade)

Diferente do legacy: não roda em máquinas sem o S3 na USB (o legacy tampouco).
Para smoke sem hardware, use `node/test_e2e.py`.

Uso:
    python node/test_lidar.py
    python node/test_lidar.py --port /dev/rplidar
    python node/test_lidar.py --duration 30

Critério de validação (humano, no bring-up de cada painel):
  - status "conectado @ ..."
  - scans/s ≈ 8-15 Hz (S3 típico)
  - meas/s ≥ ~alguns milhares (com parser vetorizado, alto)
  - reconnects=0 e desyncs baixo/estável por 30 s+ sem mexer no cabo
"""
from __future__ import annotations

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from lidar_reader import LidarReader  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default=None,
                    help="porta serial (ex.: /dev/rplidar). Default: auto-detecta")
    ap.add_argument("--baud", type=int, default=1000000)
    ap.add_argument("--duration", type=float, default=0.0,
                    help="se >0, roda esse tempo e sai. Default: até Ctrl+C")
    args = ap.parse_args()

    rdr = LidarReader(port=args.port, baud=args.baud)
    if not rdr.start():
        print(f"[lidar] não iniciou: {rdr.status}", file=sys.stderr)
        return 1
    print(f"[lidar] {rdr.status}")
    print("[lidar] lendo... Ctrl+C pra sair.")

    t0 = time.monotonic()
    last_print = 0.0
    n_total = 0
    sample_row = None
    try:
        while True:
            arr = rdr.drain()
            if arr.size:
                n_total += len(arr)
                sample_row = arr[-1]
            now = time.monotonic()
            if now - last_print >= 0.5:
                if sample_row is None:
                    line = f"\r[lidar] {rdr.status}  | aguardando primeira medida..."
                else:
                    a, d, q = sample_row
                    line = (f"\r[lidar] port={rdr.port}  "
                            f"meas/s={rdr.meas_per_sec:7.0f}  "
                            f"scans/s={rdr.scans_per_sec:5.1f}  "
                            f"last={a:6.1f}° {d:7.1f}mm q={int(q):3d}  "
                            f"desync={rdr.desyncs} recon={rdr.reconnects}  "
                            f"total={n_total}")
                sys.stdout.write(line + "        ")
                sys.stdout.flush()
                last_print = now
            if args.duration > 0 and now - t0 >= args.duration:
                print()
                print(f"[lidar] duração {args.duration:.1f}s atingida.")
                break
            time.sleep(0.01)
    except KeyboardInterrupt:
        print()
        print("[lidar] Ctrl+C — parando.")
    finally:
        rdr.stop()
        print(f"[lidar] {rdr.status}. total medidas: {n_total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
