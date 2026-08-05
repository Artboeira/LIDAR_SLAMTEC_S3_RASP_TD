"""
LidarMapper — teste da Etapa 5: receptor UDP binário de validação.

Recebe datagramas do publisher, faz struct.unpack do formato LIDAR_MAPPER_V1
e imprime FPS, latência (timestamp do pacote vs time.time() local), e
opcionalmente o conteúdo decodificado.

Uso:
    python test_udp_receiver.py
    python test_udp_receiver.py --host 0.0.0.0 --port 5555
    python test_udp_receiver.py --raw                # imprime cada pacote
    python test_udp_receiver.py --duration 10        # roda 10s e sai

Para latência fazer sentido, o relógio do publisher e do receiver têm que
estar sincronizados — em localhost é trivial; entre máquinas, NTP ajuda.

Bind:
  Por default, escuta em 0.0.0.0 (todas as interfaces) na porta `--port`.
  Use --host 127.0.0.1 pra restringir ao localhost.
"""
from __future__ import annotations

import argparse
import socket
import sys
import time

import config as cfg_mod
from publisher import unpack_frame, _HEADER, _POINT


def main() -> int:
    cfg = cfg_mod.load()
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="0.0.0.0",
                    help="interface pra escutar (0.0.0.0=todas)")
    ap.add_argument("--port", type=int, default=cfg.udp.port)
    ap.add_argument("--raw", action="store_true",
                    help="imprime cada frame decodificado")
    ap.add_argument("--duration", type=float, default=0.0)
    args = ap.parse_args()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((args.host, args.port))
    sock.settimeout(0.2)
    print(f"[udp-sub] escutando em {args.host}:{args.port}")
    print(f"[udp-sub] formato: header {_HEADER.size}B + {_POINT.size}B/ponto")
    print("[udp-sub] aguardando datagramas... Ctrl+C pra sair.")

    t0 = time.monotonic()
    last_status = t0
    n_total = 0
    n_window = 0
    n_bad = 0
    sum_lat = 0.0
    max_lat = 0.0
    last_pkt = None
    first_seen = False

    try:
        while True:
            try:
                data, peer = sock.recvfrom(8192)
            except socket.timeout:
                data = None
            now_mono = time.monotonic()
            now_wall = time.time()

            if data:
                try:
                    pkt = unpack_frame(data)
                except ValueError as exc:
                    n_bad += 1
                    if n_bad <= 5:
                        print(f"[udp-sub] pacote inválido ({exc})", file=sys.stderr)
                    continue
                last_pkt = pkt
                n_total += 1
                n_window += 1
                lat = now_wall - pkt["timestamp"]
                sum_lat += lat
                if lat > max_lat:
                    max_lat = lat
                if not first_seen:
                    print(f"[udp-sub] primeiro pacote de {peer[0]}:{peer[1]}"
                          f" ok: frame={pkt['frame']} points={len(pkt['points'])}"
                          f"  bytes={len(data)}")
                    first_seen = True
                if args.raw:
                    pts_s = "  ".join(
                        f"#{p['id']}({p['x']:.2f},{p['y']:.2f},c={p['confidence']:.2f})"
                        for p in pkt["points"]) or "(vazio)"
                    print(f"frame={pkt['frame']:5d}  ts={pkt['timestamp']:.3f}"
                          f"  N={len(pkt['points'])}  bytes={len(data)}  {pts_s}")

            if now_mono - last_status >= 1:
                elapsed = now_mono - last_status
                fps = n_window / elapsed
                avg_lat_ms = sum_lat / max(n_total, 1) * 1000
                max_lat_ms = max_lat * 1000
                pts = len(last_pkt["points"]) if last_pkt else 0
                print(f"[udp-sub] fps={fps:5.1f}  total={n_total:6d}"
                      f"  last_frame={last_pkt['frame'] if last_pkt else '-'}"
                      f"  N={pts}  lat avg={avg_lat_ms:5.1f}ms max={max_lat_ms:5.1f}ms"
                      f"  bad={n_bad}")
                n_window = 0
                last_status = now_mono

            if args.duration > 0 and now_mono - t0 >= args.duration:
                print(f"[udp-sub] duração atingida ({args.duration:.1f}s); saindo.")
                break
    except KeyboardInterrupt:
        print("\n[udp-sub] Ctrl+C — saindo.")
    finally:
        sock.close()
    elapsed = time.monotonic() - t0
    print(f"[udp-sub] total: {n_total} pacotes em {elapsed:.1f}s"
          f" ({n_total / max(elapsed, 1e-06):.1f} fps média, {n_bad} inválidos)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
