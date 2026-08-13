"""Ponte MULTI-PAINEL + monitor da frota: V2 de N painéis → 6 canais/painel.

Junta num só processo o que o demo_touch_bridge.py faz para um painel:
escuta o V2 de todos os nós (:5555), aplica a homografia de cada
calib_pN.json (hot-reload por mtime) e envia, por painel, os 6 canais de
toque — em 0..1, origem EMBAIXO À ESQUERDA — para um ou mais TDs.

Canais OSC (endereços) por painel N:
    /pN_x1  /pN_y1  /pN_active1  /pN_x2  /pN_y2  /pN_active2

No TD: um único OSC In CHOP na porta --dest-port recebe TUDO — com 4
painéis são 24 canais (p1_x1 ... p4_active2), nomes prontos, sem código.

Dashboard pygame (paleta Estúdio AB, igual ao radar_view): um cartão por
painel com status ON/OFF (idade do último pacote V2), fps de entrada,
estado da calibração e os dois toques numa miniatura 16:9 da tela.

ATENÇÃO: ocupa a porta 5555 — feche radar_view/calibrate/relay antes.

Uso (lote 1, Mac + Windows):
    python server/fleet_bridge.py --panels 1,2,3,4 --dest 127.0.0.1,192.168.1.101

Teclas: Q/ESC sai.
"""
from __future__ import annotations

import argparse
import os
import socket
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.protocol import ProtocolError, unpack_v2  # noqa: E402
from server.homography import apply_h, load_calibration  # noqa: E402
from server.demo_touch_bridge import TouchSlots  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))

# Paleta Estúdio AB (colors_and_type.css)
BONE = (237, 229, 211)
INK = (34, 34, 35)
RULE_SOFT = (188, 181, 166)
STEEL = (75, 101, 126)
EMBER = (191, 65, 40)
MOSS = (137, 153, 62)
AMBER = (238, 162, 68)
PLUM = (88, 45, 64)
FG_MUTED = (69, 70, 63)

OFFLINE_S = 1.5          # sem pacote por mais que isso = nó OFF
PAD = 20


class PanelState:
    """Estado de um painel: slots, calibração (hot-reload) e taxas."""

    def __init__(self, panel_id: int):
        self.panel_id = panel_id
        self.slots = TouchSlots()
        self.calib = None
        self.calib_mtime = 0.0
        self.calib_path = os.path.join(HERE, f"calib_p{panel_id}.json")
        self.last_pkt = 0.0
        self.in_count = 0
        self.in_rate = 0.0
        self.frame = 0

    def reload_calib(self) -> None:
        try:
            mtime = os.path.getmtime(self.calib_path)
        except OSError:
            return
        if mtime != self.calib_mtime:
            c = load_calibration(self.calib_path)
            if c is not None:
                self.calib = c
                self.calib_mtime = mtime

    @property
    def online(self) -> bool:
        return time.time() - self.last_pkt < OFFLINE_S


def main() -> int:
    ap = argparse.ArgumentParser(
        description="ponte multi-painel (6 canais/painel) + monitor da frota")
    ap.add_argument("--panels", default="1,2,3,4",
                    help="panel_ids atendidos, ex.: 1,2,3,4")
    ap.add_argument("--dest", required=True,
                    help="IP(s) do TD, separados por vírgula")
    ap.add_argument("--dest-port", type=int, default=7000)
    ap.add_argument("--format", choices=("osc", "csv"), default="osc")
    ap.add_argument("--listen-port", type=int, default=5555)
    ap.add_argument("--no-flip-y", action="store_true",
                    help="mantém origem em cima-esquerda")
    args = ap.parse_args()

    panel_ids = [int(p) for p in args.panels.split(",")]
    dests = [d.strip() for d in args.dest.split(",") if d.strip()]
    panels = {pid: PanelState(pid) for pid in panel_ids}

    osc_clients: list = []
    csv_sock = None
    if args.format == "osc":
        from pythonosc.udp_client import SimpleUDPClient
        osc_clients = [SimpleUDPClient(d, args.dest_port) for d in dests]
    else:
        csv_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    rx.bind(("0.0.0.0", args.listen_port))
    rx.setblocking(False)

    import pygame
    pygame.init()
    # Grade horizontal: até 4 painéis em 2 colunas; acima disso, 4 colunas
    # (8 painéis = 4x2) com cartão compacto para caber em tela de notebook.
    cols = 2 if len(panel_ids) <= 4 else 4
    card_w = 420 if cols == 2 else 330
    mini_w = card_w - 56
    mini_h = int(mini_w * 9 / 16)
    card_h = 56 + mini_h + 44
    rows = (len(panel_ids) + cols - 1) // cols
    win = (PAD + cols * (card_w + PAD), 96 + rows * (card_h + PAD))
    screen = pygame.display.set_mode(win)
    pygame.display.set_caption(f"fleet bridge :{args.listen_port}")
    font = pygame.font.SysFont("Menlo", 13)
    font_big = pygame.font.SysFont("Menlo", 17, bold=True)
    clock = pygame.time.Clock()

    out_count, out_rate = 0, 0.0
    rate_t0 = time.time()

    def send_panel(ps: PanelState) -> None:
        """6 canais do painel para todos os destinos (contrato do demo)."""
        nonlocal out_count
        ch = ps.slots.channels()
        names = tuple(f"/p{ps.panel_id}_{s}" for s in
                      ("x1", "y1", "active1", "x2", "y2", "active2"))
        if osc_clients:
            for cli in osc_clients:
                try:
                    for name, val in zip(names, ch):
                        cli.send_message(name, val)
                except OSError:
                    pass
        else:
            line = (f"p{ps.panel_id}," +
                    ",".join(f"{c:.4f}" for c in ch) + "\n")
            for d in dests:
                try:
                    csv_sock.sendto(line.encode("ascii"), (d, args.dest_port))
                except OSError:
                    pass
        out_count += 1

    running = True
    while running:
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                running = False
            elif ev.type == pygame.KEYDOWN and ev.key in (pygame.K_q,
                                                          pygame.K_ESCAPE):
                running = False

        for ps in panels.values():
            ps.reload_calib()

        # Drena tudo que chegou desde o último frame de tela.
        now = time.time()
        touched: set[int] = set()
        while True:
            try:
                data, _ = rx.recvfrom(2048)
            except BlockingIOError:
                break
            try:
                frm = unpack_v2(data)
            except ProtocolError:
                continue
            ps = panels.get(frm.panel_id)
            if ps is None:
                continue
            ps.last_pkt = now
            ps.in_count += 1
            ps.frame = frm.frame
            tracks: list[tuple[int, float, float]] = []
            if ps.calib is not None:
                for p in frm.points:
                    u, v = apply_h(ps.calib.H, p.x_mm, p.y_mm)
                    u = min(1.0, max(0.0, u))
                    v = min(1.0, max(0.0, v))
                    y = v if args.no_flip_y else 1.0 - v
                    tracks.append((p.id, u, y))
            ps.slots.update(tracks, now)
            send_panel(ps)
            touched.add(frm.panel_id)

        # Painel sem pacote neste tick: expira slots (nó caiu ≠ toque preso).
        for pid, ps in panels.items():
            if pid not in touched:
                ps.slots.update([], now)

        if now - rate_t0 >= 1.0:
            dt = now - rate_t0
            for ps in panels.values():
                ps.in_rate = ps.in_count / dt
                ps.in_count = 0
            out_rate = out_count / dt
            out_count, rate_t0 = 0, now

        # --- desenho ---
        screen.fill(BONE)
        n_on = sum(1 for ps in panels.values() if ps.online)
        hdr = (f"FLEET BRIDGE :{args.listen_port}   nós {n_on}/{len(panels)} ON   "
               f"out={out_rate:6.1f} pkt/s → {','.join(dests)}:{args.dest_port} "
               f"({args.format.upper()})")
        screen.blit(font_big.render(hdr, True, INK), (PAD, 20))
        screen.blit(font.render(
            "canais por painel: pN_x1 pN_y1 pN_active1 pN_x2 pN_y2 pN_active2"
            "  ·  0..1, origem embaixo-esq  ·  Q sai", True, FG_MUTED),
            (PAD, 48))

        for i, pid in enumerate(panel_ids):
            ps = panels[pid]
            cx = PAD + (i % cols) * (card_w + PAD)
            cy = 96 + (i // cols) * (card_h + PAD)
            pygame.draw.rect(screen, INK, (cx, cy, card_w, card_h), 1)

            on = ps.online
            dot_color = MOSS if on else EMBER
            pygame.draw.circle(screen, dot_color, (cx + 16, cy + 18), 6)
            title = f"PAINEL {pid}"
            state = "ON " if on else "OFF"
            screen.blit(font_big.render(f"{title}  {state}", True, INK),
                        (cx + 30, cy + 10))
            calib_ok = ps.calib is not None
            screen.blit(font.render(
                f"in={ps.in_rate:5.1f}/s  f{ps.frame}  "
                f"calib {'OK' if calib_ok else 'AUSENTE'}",
                True, INK if calib_ok else EMBER), (cx + 30, cy + 32))

            # miniatura 16:9 da tela com os 2 toques
            mw, mh = mini_w, mini_h
            mx0, my0 = cx + 28, cy + 56
            pygame.draw.rect(screen, RULE_SOFT, (mx0, my0, mw, mh), 1)
            ch = ps.slots.channels()
            for s, color in ((0, EMBER), (1, STEEL)):
                x, y, a = ch[3 * s], ch[3 * s + 1], ch[3 * s + 2]
                v = y if args.no_flip_y else 1.0 - y
                px = (int(mx0 + x * mw), int(my0 + v * mh))
                if a > 0.0:
                    pygame.draw.circle(screen, color, px, 7)
                else:
                    pygame.draw.circle(screen, color, px, 7, 1)
                # os dois t's lado a lado, uma linha só (cartão compacto)
                screen.blit(font.render(
                    f"t{s+1} {x:.2f},{y:.2f} {'ON' if a else '--'}",
                    True, color),
                    (mx0 + s * (mw // 2 + 6), my0 + mh + 6))

        pygame.display.flip()
        clock.tick(60)

    pygame.quit()
    rx.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
