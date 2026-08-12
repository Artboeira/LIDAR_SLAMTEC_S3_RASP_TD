"""Ponte de DEMO: V2 de UM painel → 6 canais de toque para o TouchDesigner.

Para demonstração com cliente, sem instalar nada no Windows do TD: roda na
máquina de trabalho (que já recebe o V2 do nó), aplica a homografia do
calib_pN.json e envia DOIS toques — x1, y1, active1, x2, y2, active2 — em
coordenadas 0..1 com ORIGEM EMBAIXO À ESQUERDA.

Não substitui o server_relay.py (não emite V1 nem OSC /touch/N de evento);
é um atalho de demo. A cadeia definitiva é a do relay.

Formatos de saída (--format):
  osc  (default) — 6 mensagens OSC por frame: /x1 /y1 /active1 /x2 /y2 /active2
                   No TD: OSC In CHOP na porta --dest-port → 6 canais prontos.
  csv            — datagrama UDP de texto "x1,y1,a1,x2,y2,a2\\n"
                   No TD: UDP In DAT + parse do outro lado.

Uso (demo da tela 1, TD em 192.168.3.5):
    python server/demo_touch_bridge.py --panel 1 --dest 192.168.3.5
    python server/demo_touch_bridge.py --panel 1 --dest 192.168.3.5 --format csv

Antes, calibrar o painel (o operador toca os 4 cantos físicos, ENTER a cada um;
o alvo OSC do modo td pode não existir — os prompts do terminal bastam):
    python server/calibrate.py --panel 1 --target-source td

- `active` = 1.0 enquanto o toque existe; ao sumir, vai a 0.0 e o x/y congela
  no último valor (canal segura, o TD decide o fade).
- Slots estáveis: um track mantém o mesmo slot (1 ou 2) enquanto viver.
- Clip em [0..1] — mesmo contrato do relay (segunda linha de defesa da ROI).
- Hot-reload do calib_pN.json por mtime, como no relay: recalibrar não exige
  reiniciar a ponte.
"""
from __future__ import annotations

import argparse
import os
import socket
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np  # noqa: E402

from shared.protocol import ProtocolError, unpack_v2  # noqa: E402
from server.homography import apply_h, load_calibration  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))

# Track ausente por mais que isso perde o slot (o nó publica a 30 Hz;
# 0.25 s = ~7 frames de tolerância a perda de pacote).
SLOT_TIMEOUT_S = 0.25


class TouchSlots:
    """Mapeia ids de track do nó para 2 slots estáveis de toque."""

    def __init__(self, n_slots: int = 2):
        self.n = n_slots
        self.slot_id: list[int | None] = [None] * n_slots   # track id por slot
        self.slot_seen: list[float] = [0.0] * n_slots
        self.slot_xy: list[tuple[float, float]] = [(0.5, 0.5)] * n_slots

    def update(self, tracks: list[tuple[int, float, float]], now: float) -> None:
        """`tracks` = [(track_id, x01, y01), ...] já em 0..1 origem BL."""
        by_id = {tid: (x, y) for tid, x, y in tracks}
        # 1) refresca slots cujos tracks continuam vivos
        for s in range(self.n):
            tid = self.slot_id[s]
            if tid is not None and tid in by_id:
                self.slot_xy[s] = by_id.pop(tid)
                self.slot_seen[s] = now
        # 2) expira slots sem track há SLOT_TIMEOUT_S
        for s in range(self.n):
            if self.slot_id[s] is not None and now - self.slot_seen[s] > SLOT_TIMEOUT_S:
                self.slot_id[s] = None
        # 3) tracks novos ocupam o menor slot livre
        for tid, xy in by_id.items():
            for s in range(self.n):
                if self.slot_id[s] is None:
                    self.slot_id[s] = tid
                    self.slot_xy[s] = xy
                    self.slot_seen[s] = now
                    break

    def channels(self) -> list[float]:
        """[x1, y1, a1, x2, y2, a2] — x/y seguram o último valor."""
        out: list[float] = []
        for s in range(self.n):
            x, y = self.slot_xy[s]
            out += [x, y, 1.0 if self.slot_id[s] is not None else 0.0]
        return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--panel", type=int, required=True, help="panel_id do nó (1..8)")
    ap.add_argument("--dest", required=True, help="IP do Windows/TD")
    ap.add_argument("--dest-port", type=int, default=7000,
                    help="porta no TD (OSC In CHOP ou UDP In DAT) [7000]")
    ap.add_argument("--listen-port", type=int, default=5555,
                    help="porta local do V2 (a que o nó já usa) [5555]")
    ap.add_argument("--format", choices=("osc", "csv"), default="osc")
    ap.add_argument("--calib", default=None,
                    help=f"calib JSON [server/calib_p<panel>.json]")
    ap.add_argument("--no-flip-y", action="store_true",
                    help="mantém origem em cima-esquerda (default: embaixo-esquerda)")
    args = ap.parse_args()

    calib_path = args.calib or os.path.join(HERE, f"calib_p{args.panel}.json")

    if args.format == "osc":
        from pythonosc.udp_client import SimpleUDPClient
        osc = SimpleUDPClient(args.dest, args.dest_port)
        send_sock = None
    else:
        osc = None
        send_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    rx.bind(("0.0.0.0", args.listen_port))
    rx.settimeout(0.25)

    print(f"[demo] V2 painel {args.panel} em :{args.listen_port} → "
          f"{args.format.upper()} {args.dest}:{args.dest_port}")
    print(f"[demo] calib: {calib_path}  (hot-reload por mtime)")
    print("[demo] canais: x1 y1 active1 x2 y2 active2 — 0..1, origem "
          + ("CIMA-esq (--no-flip-y)" if args.no_flip_y else "EMBAIXO-esq"))

    calib = None
    calib_mtime = 0.0
    warned = False
    slots = TouchSlots()
    n_in = n_out = 0
    last_status = time.monotonic()

    while True:
        # hot-reload do calib (mesma disciplina do relay)
        try:
            mtime = os.path.getmtime(calib_path)
            if mtime != calib_mtime:
                c = load_calibration(calib_path)
                if c is not None:
                    calib = c
                    calib_mtime = mtime
                    print(f"[demo] calibração {'re' if warned or n_out else ''}carregada.")
        except OSError:
            if calib is None and not warned:
                print(f"[demo] SEM {calib_path} — rode o calibrate.py primeiro; "
                      "enviando active=0 até lá.")
                warned = True

        try:
            data, _ = rx.recvfrom(8192)
        except socket.timeout:
            data = None
        except KeyboardInterrupt:
            break

        now = time.monotonic()
        if data:
            try:
                frm = unpack_v2(data, strict_version=False)
            except ProtocolError:
                continue
            if frm.panel_id != args.panel:
                continue
            n_in += 1

            tracks: list[tuple[int, float, float]] = []
            if calib is not None:
                for p in frm.points:
                    u, v = apply_h(calib.H, p.x_mm, p.y_mm)
                    # mesmo clip do relay: fora de [0..1] = fora do painel
                    u = min(1.0, max(0.0, u))
                    v = min(1.0, max(0.0, v))
                    y = v if args.no_flip_y else 1.0 - v
                    tracks.append((p.id, u, y))
            slots.update(tracks, now)

            ch = slots.channels()
            if osc is not None:
                osc.send_message("/x1", ch[0])
                osc.send_message("/y1", ch[1])
                osc.send_message("/active1", ch[2])
                osc.send_message("/x2", ch[3])
                osc.send_message("/y2", ch[4])
                osc.send_message("/active2", ch[5])
            else:
                line = ",".join(f"{c:.4f}" for c in ch) + "\n"
                send_sock.sendto(line.encode("ascii"), (args.dest, args.dest_port))
            n_out += 1
        else:
            # sem pacote: expira slots mesmo assim (nó caiu ≠ toque preso)
            slots.update([], now)

        if now - last_status >= 1.0:
            a1, a2 = slots.channels()[2], slots.channels()[5]
            print(f"[demo] in={n_in:3d}/s out={n_out:3d}/s  "
                  f"t1={'ON ' if a1 else 'off'} t2={'ON ' if a2 else 'off'}  "
                  f"calib={'OK' if calib is not None else 'AUSENTE'}")
            n_in = n_out = 0
            last_status = now

    return 0


if __name__ == "__main__":
    sys.exit(main())
