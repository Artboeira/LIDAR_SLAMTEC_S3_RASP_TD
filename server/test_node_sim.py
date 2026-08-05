"""
LidarMapper — simulador de nós Pi.

Gera tráfego V2 sintético para 1..N panel_ids configuráveis a 30 Hz, sem
Pi ligado. Ferramenta de dev do server_relay.py e do calibrate.py.

Modos de movimento (--pattern):
  circle    — 2 toques (cursor) por painel se movendo em círculo com ids
              persistentes. Padrão útil para observar tracking suave e OSC.
  static    — 1 toque parado no centro do painel. Verifica que down dispara
              uma vez e não repete.
  updown    — ids surgem e somem em ciclos, exercitando o detector de
              down/up do relay (§3.2).

Uso típico:
    # 4 painéis, círculo, host local, porta 5555 (default):
    python server/test_node_sim.py --panels 1,2,3,4

    # 1 painel, toque parado, host remoto:
    python server/test_node_sim.py --panels 1 --pattern static --host 10.10.0.10

    # eventos down/up:
    python server/test_node_sim.py --panels 1 --pattern updown --rate 5

Todos os pacotes saem pelo mesmo socket UDP; o relay demux por panel_id.
"""
from __future__ import annotations

import argparse
import math
import os
import socket
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.protocol import V2Point, pack_v2  # noqa: E402


def _circle_points(panel_id: int, t: float) -> list[V2Point]:
    """Dois cursores girando em círculos de raio diferente ao redor do
    centro do painel — coordenadas em mm no plano do sensor virtual.

    O panel_id serve só como offset de fase, pra os painéis não ficarem
    idênticos na visualização.

    Ids são persistentes por painel (2 tracks/frame, sempre id=1 e id=2)."""
    phase = panel_id * 0.7
    r1 = 400
    r2 = 250
    cx1 = 1200 + r1 * math.cos(t * 2 + phase)
    cy1 = -200 + r1 * math.sin(t * 2 + phase)
    cx2 = 1800 + r2 * math.cos(t * 1.3 + phase + math.pi / 3)
    cy2 = 200 + r2 * math.sin(t * 1.3 + phase + math.pi / 3)
    return [
        V2Point(id=1, x_mm=cx1, y_mm=cy1, confidence=1.0),
        V2Point(id=2, x_mm=cx2, y_mm=cy2, confidence=1.0),
    ]


def _static_points(panel_id: int, t: float) -> list[V2Point]:
    """Um toque parado no centro do painel (1500, 0). Serve para
    validar que o relay dispara /touch/N uma única vez (debounce)."""
    return [V2Point(id=1, x_mm=1500.0, y_mm=0.0, confidence=1.0)]


def _updown_points(panel_id: int, t: float, period_s: float = 1.5) -> list[V2Point]:
    """Ciclo down/up: por metade do período o toque existe, na outra
    metade some. Cada ciclo usa um id novo — testa o detector de down
    para novos ids (§3.2)."""
    cycle = int(t / period_s)
    phase = (t % period_s) / period_s
    if phase >= 0.5:
        return []
    return [V2Point(id=cycle + 1, x_mm=1500.0, y_mm=0.0, confidence=1.0)]


PATTERNS = {
    "circle": _circle_points,
    "static": _static_points,
    "updown": _updown_points,
}


def main() -> int:
    ap = argparse.ArgumentParser(description="Simulador de nós Pi (V2)")
    ap.add_argument("--panels", default="1,2,3,4",
                    help="lista CSV de panel_ids (1..8). Default: 1,2,3,4")
    ap.add_argument("--host", default="127.0.0.1",
                    help="destino UDP. Default: 127.0.0.1")
    ap.add_argument("--port", type=int, default=5555,
                    help="porta UDP do relay. Default: 5555")
    ap.add_argument("--rate", type=float, default=30.0,
                    help="Hz por painel. Default: 30")
    ap.add_argument("--pattern", choices=sorted(PATTERNS.keys()), default="circle",
                    help="padrão de movimento. Default: circle")
    ap.add_argument("--duration", type=float, default=0.0,
                    help="se >0, roda por N segundos e sai. Default: até Ctrl+C")
    ap.add_argument("--max-points", type=int, default=32,
                    help="cap de max_points do V2. Default: 32")
    ap.add_argument("--quiet", action="store_true",
                    help="não imprime status a cada 1 s")
    args = ap.parse_args()

    panels = [int(x) for x in args.panels.split(",") if x.strip()]
    for p in panels:
        if not 1 <= p <= 255:
            print(f"[sim] panel_id inválido: {p}", file=sys.stderr)
            return 1

    pattern_fn = PATTERNS[args.pattern]
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    period = 1.0 / args.rate if args.rate > 0 else 0.0

    print(f"[sim] panels={panels}  pattern={args.pattern}  rate={args.rate:g} Hz"
          f"  -> {args.host}:{args.port}")

    t0 = time.monotonic()
    frames = {pid: 0 for pid in panels}
    sent_total = 0
    last_status = t0
    try:
        while True:
            now = time.monotonic()
            t = now - t0
            for pid in panels:
                frames[pid] += 1
                pts = pattern_fn(pid, t)
                body = pack_v2(panel_id=pid, frame=frames[pid],
                               timestamp=time.time(), points=pts,
                               max_points=args.max_points)
                try:
                    sock.sendto(body, (args.host, args.port))
                    sent_total += 1
                except OSError as exc:
                    print(f"[sim] sendto falhou: {exc}", file=sys.stderr)
            if not args.quiet and now - last_status >= 1.0:
                print(f"[sim] t={t:5.1f}s  sent={sent_total}  "
                      f"frames_per_panel={frames[panels[0]]}")
                last_status = now
            if args.duration > 0 and t >= args.duration:
                print(f"[sim] duração {args.duration:.1f}s atingida; saindo.")
                break
            if period > 0:
                # dorme até o próximo tick do painel mais lento (aprox.)
                elapsed = time.monotonic() - now
                if elapsed < period:
                    time.sleep(period - elapsed)
    except KeyboardInterrupt:
        print("\n[sim] Ctrl+C — saindo.")
    finally:
        sock.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
