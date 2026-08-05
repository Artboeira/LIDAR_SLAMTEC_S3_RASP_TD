"""
LidarMapper — receptor UDP binário de validação (§5.2 item 11).

Evolução do legacy/test_udp_receiver.py: agora com dois modos.

  --v2   decoda o formato dos nós Pi (entrada do relay, porta :5555).
         Imprime panel_id, id, x_mm, y_mm.
         Alerta uma vez por peer se version != 2.

  --v1   decoda o formato do TouchDesigner (saída do relay,
         portas :600N, localhost). Imprime x, y ∈ [0..1].

Uso:
    # validar entrada dos Pis num servidor:
    python server/test_udp_receiver.py --v2

    # validar saída do relay pro TD (painel 1):
    python server/test_udp_receiver.py --v1 --host 127.0.0.1 --port 6001

    # cada frame decodificado:
    python server/test_udp_receiver.py --v1 --port 6001 --raw

Para latência fazer sentido, os relógios do publisher e do receiver têm
que estar sincronizados (localhost é trivial; entre máquinas, NTP ajuda).
"""
from __future__ import annotations

import argparse
import os
import socket
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.protocol import (  # noqa: E402
    V1_HEADER_SIZE, V1_POINT_SIZE,
    V2_HEADER_SIZE, V2_POINT_SIZE, V2_VERSION,
    ProtocolError, unpack_v1, unpack_v2,
)


def _fmt_pts_v1(frm) -> str:
    if not frm.points:
        return "(vazio)"
    return "  ".join(f"#{p.id}({p.x:.2f},{p.y:.2f},c={p.confidence:.2f})"
                     for p in frm.points)


def _fmt_pts_v2(frm) -> str:
    if not frm.points:
        return "(vazio)"
    return "  ".join(f"#{p.id}({p.x_mm:+7.1f},{p.y_mm:+7.1f})mm,c={p.confidence:.2f}"
                     for p in frm.points)


def main() -> int:
    ap = argparse.ArgumentParser(description="Receptor UDP LidarMapper (V1/V2)")
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--v1", action="store_true",
                      help="formato do TD (relay → TD)")
    mode.add_argument("--v2", action="store_true",
                      help="formato dos Pis (Pi → relay)")
    ap.add_argument("--host", default="0.0.0.0",
                    help="interface (0.0.0.0 = todas). Default: 0.0.0.0")
    ap.add_argument("--port", type=int, required=True,
                    help="porta UDP (V2: 5555; V1: 600N)")
    ap.add_argument("--raw", action="store_true",
                    help="imprime cada frame decodificado")
    ap.add_argument("--duration", type=float, default=0.0,
                    help="se >0, roda esse tempo e sai. Default: até Ctrl+C")
    args = ap.parse_args()

    is_v2 = args.v2
    unpack = unpack_v2 if is_v2 else unpack_v1
    hdr_sz = V2_HEADER_SIZE if is_v2 else V1_HEADER_SIZE
    pt_sz = V2_POINT_SIZE if is_v2 else V1_POINT_SIZE

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((args.host, args.port))
    sock.settimeout(0.2)

    label = "V2" if is_v2 else "V1"
    print(f"[udp-sub {label}] escutando em {args.host}:{args.port}")
    print(f"[udp-sub {label}] formato: header {hdr_sz}B + {pt_sz}B/ponto")
    print(f"[udp-sub {label}] aguardando datagramas... Ctrl+C pra sair.")

    t0 = time.monotonic()
    last_status = t0
    n_total = 0
    n_window = 0
    n_bad = 0
    n_bad_reported = 0
    sum_lat = 0.0
    max_lat = 0.0
    last_pkt = None
    first_by_peer: dict = {}
    per_panel_count: dict = {}
    version_warned: set = set()

    try:
        while True:
            try:
                data, peer = sock.recvfrom(8192)
            except socket.timeout:
                data = None
                peer = None
            now_mono = time.monotonic()
            now_wall = time.time()

            if data:
                try:
                    pkt = unpack(data)
                except ProtocolError as exc:
                    n_bad += 1
                    if n_bad_reported < 5:
                        print(f"[udp-sub {label}] pacote inválido de {peer}: {exc}",
                              file=sys.stderr)
                        n_bad_reported += 1
                    continue

                if is_v2 and pkt.version != V2_VERSION and peer not in version_warned:
                    print(f"[udp-sub V2] AVISO: {peer} enviou version={pkt.version}"
                          f" (esperado {V2_VERSION})", file=sys.stderr)
                    version_warned.add(peer)

                last_pkt = pkt
                n_total += 1
                n_window += 1
                lat = now_wall - pkt.timestamp
                sum_lat += lat
                if lat > max_lat:
                    max_lat = lat

                if is_v2:
                    per_panel_count[pkt.panel_id] = per_panel_count.get(pkt.panel_id, 0) + 1

                if peer not in first_by_peer:
                    first_by_peer[peer] = True
                    extra = f" panel_id={pkt.panel_id}" if is_v2 else ""
                    print(f"[udp-sub {label}] primeiro pacote de {peer[0]}:{peer[1]}"
                          f"{extra} frame={pkt.frame} pts={len(pkt.points)}"
                          f" bytes={len(data)}")

                if args.raw:
                    pts_s = _fmt_pts_v2(pkt) if is_v2 else _fmt_pts_v1(pkt)
                    extra = f" p{pkt.panel_id}" if is_v2 else ""
                    print(f"frame={pkt.frame:5d}{extra}  ts={pkt.timestamp:.3f}"
                          f"  N={len(pkt.points)}  bytes={len(data)}  {pts_s}")

            if now_mono - last_status >= 1.0:
                elapsed = now_mono - last_status
                fps = n_window / elapsed if elapsed > 0 else 0.0
                avg_lat_ms = sum_lat / max(n_total, 1) * 1000
                max_lat_ms = max_lat * 1000
                pts = len(last_pkt.points) if last_pkt else 0
                last_frame = last_pkt.frame if last_pkt else "-"
                panels_s = ""
                if is_v2 and per_panel_count:
                    panels_s = "  panels=" + ",".join(
                        f"p{pid}:{c}" for pid, c in sorted(per_panel_count.items()))
                print(f"[udp-sub {label}] fps={fps:5.1f}  total={n_total:6d}"
                      f"  last_frame={last_frame}  N={pts}"
                      f"  lat avg={avg_lat_ms:5.1f}ms max={max_lat_ms:5.1f}ms"
                      f"  bad={n_bad}{panels_s}")
                n_window = 0
                per_panel_count = {}
                last_status = now_mono

            if args.duration > 0 and now_mono - t0 >= args.duration:
                print(f"[udp-sub {label}] duração {args.duration:.1f}s atingida.")
                break
    except KeyboardInterrupt:
        print(f"\n[udp-sub {label}] Ctrl+C — saindo.")
    finally:
        sock.close()

    elapsed = time.monotonic() - t0
    print(f"[udp-sub {label}] total: {n_total} pacotes em {elapsed:.1f}s "
          f"({n_total / max(elapsed, 1e-6):.1f} fps, {n_bad} inválidos)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
