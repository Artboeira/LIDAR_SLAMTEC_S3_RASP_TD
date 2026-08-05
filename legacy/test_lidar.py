"""
LidarMapper — teste da Etapa 1.

Conecta o RPLIDAR S3, lê em loop e imprime:
  - status da conexão
  - medidas/s (throughput do sensor) e scans/s (≈ rotações por segundo)
  - amostra rotativa das últimas medidas (ângulo°, distância mm, qualidade)
  - contador de reconexões / desyncs (estabilidade)

Uso:
    python test_lidar.py                       # auto-detecta a porta
    python test_lidar.py --port COM12          # força a porta
    python test_lidar.py --port COM12 --raw    # imprime CADA medida
                                                # (verboso; bom pra sanity check)
    python test_lidar.py --duration 10         # roda 10s e sai (CI/smoke)

Critério de validação (humano):
  - status "conectado @ COMx"
  - scans/s ≈ 8–15 Hz (S3 típico)
  - medidas/s na faixa de milhares (S3 a 1 Mbps faz ~32 kHz em teoria,
    a leitura via pyserial geralmente fica em alguns milhares)
  - reconnects = 0, desyncs baixos ou zero por 30s+ sem mexer
"""
from __future__ import annotations

import argparse
import sys
import time

from lidar_reader import LidarReader


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default=None,
                    help="porta serial (ex.: COM12). Default: auto-detecta CP210x")
    ap.add_argument("--baud", type=int, default=1000000,
                    help="baud rate (S3 = 1000000)")
    ap.add_argument("--raw", action="store_true",
                    help="imprime CADA medida (muito verboso)")
    ap.add_argument("--duration", type=float, default=0.0,
                    help="se >0, roda esse tempo (s) e sai. 0 = até Ctrl+C")
    args = ap.parse_args()

    rdr = LidarReader(port=args.port, baud=args.baud)
    if not rdr.start():
        print(f"[lidar] não iniciou: {rdr.status}", file=sys.stderr)
        return 1
    print(f"[lidar] {rdr.status}")
    print("[lidar] lendo... Ctrl+C pra sair.")

    t0 = time.monotonic()
    last_print = 0.0
    sample = None
    n_seen = 0
    try:
        while True:
            measurements = rdr.drain()
            for m in measurements:
                n_seen += 1
                sample = (m.angle, m.distance, m.quality)
                if args.raw:
                    print(f"  {m.angle:7.2f}°  {m.distance:8.1f}mm  q={m.quality:3d}"
                          f"  new_scan={int(m.new_scan)}")
            now = time.monotonic()
            if now - last_print >= 0.5 and not args.raw:
                if sample is None:
                    line = f"\r[lidar] {rdr.status}  | aguardando primeira medida..."
                else:
                    a, d, q = sample
                    line = (f"\r[lidar] port={rdr.port}  meas/s={rdr.meas_per_sec:7.0f}"
                            f"  scans/s={rdr.scans_per_sec:5.1f}  last={a:6.1f}° "
                            f"{d:7.1f}mm q={q:3d}  desync={rdr.desyncs}"
                            f" recon={rdr.reconnects}  total={n_seen}")
                sys.stdout.write(line + "        ")
                sys.stdout.flush()
                last_print = now
            if args.duration > 0 and now - t0 >= args.duration:
                print()
                print(f"[lidar] duração atingida ({args.duration:.1f}s); saindo.")
                break
            time.sleep(0.01)
    except KeyboardInterrupt:
        print()
        print("[lidar] Ctrl+C — parando.")
    finally:
        rdr.stop()
        print(f"[lidar] {rdr.status}. total medidas: {n_seen}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
