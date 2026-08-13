"""Central da frota: ponte multi-painel + monitor + calibração + baseline.

Um processo, duas telas:

GRADE (inicial) — um cartão por painel: ON/OFF (idade do último V2), fps de
entrada, estado da calibração e os 2 toques na miniatura. Enquanto isso, a
ponte envia os 6 canais/painel para o(s) TD(s) e o /touch/N pro Max.

    teclas:  1..8  abre o radar do painel      A  baseline em TODOS (área livre!)
             Q/ESC sai            (clicar num cartão também abre o radar)

RADAR DO PAINEL — visão em mm (sensor no topo), ROI, cursores ao vivo, e:

    teclas:  1..4  captura canto (TL, TR, BR, BL — mão parada ~2 s)
             S     valida e salva calib_pN.json (a ponte recarrega sozinha)
             X     descarta cantos capturados
             B     refaz o BASELINE deste nó via SSH (área livre ~15 s!)
             ESC   volta pra grade

O baseline via SSH usa o mapa painel→hostname da instalação CURVA (tabela em
docs/PROVISIONAMENTO_FROTA.md), sobrescritível com --hosts "1=lidar-01,...".
Exige chave SSH instalada nos nós (deploy/install_server.ps1 §6).

Saídas (iguais às versões anteriores):
  TD:  OSC /pN_x1 /pN_y1 /pN_active1 /pN_x2 /pN_y2 /pN_active2 → --dest:--dest-port
       (0..1, origem EMBAIXO-esq, 30 Hz por painel; active segura o último x/y)
  Max: /touch/N 1|0 (active1 de cada tela, SÓ transições) → --max-dest:--max-port

ATENÇÃO: ocupa a porta 5555 — feche radar_view/calibrate/relay antes.

Uso:
    python server/fleet_bridge.py --panels 1,2,3,4,5,6,7,8 --dest 127.0.0.1
"""
from __future__ import annotations

import argparse
import os
import socket
import statistics
import subprocess
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.protocol import ProtocolError, unpack_v2  # noqa: E402
from server.homography import (  # noqa: E402
    Calibration, apply_h, compute_homography, load_calibration,
    save_calibration,
)
from server.calibrate import (  # noqa: E402
    CORNER_NAMES, degenerate_reason, reprojection_error_px,
)
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

OFFLINE_S = 1.5            # sem pacote por mais que isso = nó OFF
POINT_TTL = 0.6            # cursor no radar some após esse silêncio
PAD = 20

# Instalação CURVA: painel físico → hostname do cartão SD
# (docs/PROVISIONAMENTO_FROTA.md, "Estado da frota"). --hosts sobrescreve.
DEFAULT_HOSTS = {1: "lidar-01", 2: "lidar-03", 3: "lidar-08", 4: "lidar-06",
                 5: "lidar-07", 6: "lidar-02", 7: "lidar-04", 8: "lidar-05"}

# Cantos físicos do painel em coords normalizadas (ordem = CORNER_NAMES).
CORNERS_NORM = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
COLLECT_S = 2.0
MIN_PTS = 30
SCREEN_WH = (1920, 1080)   # referência do erro de reprojeção em px

# Mundo do radar do painel (mm) — cobre a ROI padrão da frota com folga.
WORLD_X = (-1600.0, 1600.0)
WORLD_Y = (-150.0, 4300.0)
ROI_DRAW = (-1400.0, 1400.0, 100.0, 4000.0)


class PanelState:
    """Estado de um painel: slots, calibração (hot-reload), taxas e radar."""

    def __init__(self, panel_id: int, hostname: str | None):
        self.panel_id = panel_id
        self.hostname = hostname
        self.slots = TouchSlots()
        self.calib = None
        self.calib_mtime = 0.0
        self.calib_path = os.path.join(HERE, f"calib_p{panel_id}.json")
        self.last_pkt = 0.0
        self.in_count = 0
        self.in_rate = 0.0
        self.frame = 0
        self.max_active = 0            # último /touch/N enviado ao Max (0|1)
        self.cursors: dict[int, tuple[float, float, float]] = {}  # id→(x,y,seen)

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


def parse_hosts(spec: str | None) -> dict[int, str]:
    hosts = dict(DEFAULT_HOSTS)
    if spec:
        for part in spec.split(","):
            k, _, v = part.partition("=")
            hosts[int(k.strip())] = v.strip()
    return hosts


def ssh_restart(hostname: str, done: dict) -> None:
    """Roda em thread: reinicia o lidarmapper do nó (refaz o baseline)."""
    try:
        r = subprocess.run(
            ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8",
             f"pi@{hostname}.local", "sudo systemctl restart lidarmapper"],
            capture_output=True, text=True, timeout=30)
        done["msg"] = (f"{hostname}: baseline disparado — área livre ~15 s"
                       if r.returncode == 0
                       else f"{hostname}: FALHOU ({(r.stderr or '').strip()[:60]})")
    except Exception as exc:                       # noqa: BLE001
        done["msg"] = f"{hostname}: FALHOU ({exc})"
    done["t"] = time.time()


def main() -> int:
    ap = argparse.ArgumentParser(
        description="ponte multi-painel + monitor + calibração + baseline")
    ap.add_argument("--panels", default="1,2,3,4,5,6,7,8",
                    help="panel_ids atendidos, ex.: 1,2,3,4")
    ap.add_argument("--dest", required=True,
                    help="IP(s) do TD, separados por vírgula")
    ap.add_argument("--dest-port", type=int, default=7000)
    ap.add_argument("--format", choices=("osc", "csv"), default="osc")
    ap.add_argument("--listen-port", type=int, default=5555)
    ap.add_argument("--no-flip-y", action="store_true",
                    help="mantém origem em cima-esquerda")
    ap.add_argument("--max-dest", default="127.0.0.1",
                    help="IP do Max (audio): /touch/N 1|0 nas transições [127.0.0.1]")
    ap.add_argument("--max-port", type=int, default=7500,
                    help="porta OSC do Max [7500]")
    ap.add_argument("--no-max", action="store_true",
                    help="desliga a saída pro Max")
    ap.add_argument("--hosts", default=None,
                    help='sobrescreve painel→hostname: "1=lidar-01,2=lidar-03"')
    args = ap.parse_args()

    panel_ids = [int(p) for p in args.panels.split(",")]
    dests = [d.strip() for d in args.dest.split(",") if d.strip()]
    hosts = parse_hosts(args.hosts)
    panels = {pid: PanelState(pid, hosts.get(pid)) for pid in panel_ids}

    osc_clients: list = []
    csv_sock = None
    from pythonosc.udp_client import SimpleUDPClient
    if args.format == "osc":
        osc_clients = [SimpleUDPClient(d, args.dest_port) for d in dests]
    else:
        csv_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    max_osc = None if args.no_max else SimpleUDPClient(args.max_dest, args.max_port)

    rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    rx.bind(("0.0.0.0", args.listen_port))
    rx.setblocking(False)

    import pygame
    pygame.init()
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

    # navegação/calibração
    view: int | None = None            # None = grade; senão panel_id do radar
    corners_mm: dict[int, tuple[float, float]] = {}
    capture: dict | None = None        # {idx, t0, xs, ys}
    flash_msg, flash_t = "", 0.0
    ssh_done: dict = {}                # thread de baseline escreve msg aqui
    card_rects: dict[int, "pygame.Rect"] = {}

    def flash(text: str) -> None:
        nonlocal flash_msg, flash_t
        flash_msg, flash_t = text, time.time()

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

    def start_baseline(pid: int) -> None:
        ps = panels[pid]
        if not ps.hostname:
            flash(f"painel {pid}: sem hostname (--hosts)")
            return
        flash(f"painel {pid} ({ps.hostname}): reiniciando serviço... "
              "ÁREA LIVRE ~15 s")
        threading.Thread(target=ssh_restart, args=(ps.hostname, ssh_done),
                         daemon=True).start()

    def try_save(pid: int) -> None:
        if len(corners_mm) < 4:
            faltam = [CORNER_NAMES[i] for i in range(4) if i not in corners_mm]
            flash(f"faltam cantos: {', '.join(faltam)}")
            return
        pts_mm = [corners_mm[i] for i in range(4)]
        motivo = degenerate_reason(pts_mm)
        if motivo is not None:
            flash(f"RECUSADA: {motivo} — recapture (X descarta)")
            return
        H = compute_homography(pts_mm, CORNERS_NORM)
        errs = reprojection_error_px(H, pts_mm, CORNERS_NORM, SCREEN_WH)
        out = os.path.join(HERE, f"calib_p{pid}.json")
        save_calibration(out, Calibration(
            H=H, corners_lidar_mm=pts_mm, corners_screen_norm=CORNERS_NORM,
            screen_width_px=SCREEN_WH[0], screen_height_px=SCREEN_WH[1]))
        corners_mm.clear()
        flash(f"SALVO calib_p{pid}.json — erros(px): "
              + " ".join(f"{e:.1f}" for e in errs))

    # geometria do radar do painel (tela cheia da janela)
    rx0, ry0 = PAD, 76
    rw, rh = win[0] - 2 * PAD, win[1] - ry0 - PAD
    sc = min(rw / (WORLD_X[1] - WORLD_X[0]), rh / (WORLD_Y[1] - WORLD_Y[0]))

    def to_px(x_mm: float, y_mm: float) -> tuple[int, int]:
        return (int(rx0 + (x_mm - WORLD_X[0]) * sc),
                int(ry0 + (y_mm - WORLD_Y[0]) * sc))

    running = True
    while running:
        now = time.time()
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                running = False
            elif ev.type == pygame.MOUSEBUTTONDOWN and view is None:
                for pid, rect in card_rects.items():
                    if rect.collidepoint(ev.pos):
                        view = pid
                        corners_mm.clear()
                        capture = None
                        break
            elif ev.type == pygame.KEYDOWN:
                if view is None:
                    # ---- grade ----
                    if ev.key in (pygame.K_q, pygame.K_ESCAPE):
                        running = False
                    elif ev.key == pygame.K_a:
                        for pid in panel_ids:
                            start_baseline(pid)
                    elif pygame.K_1 <= ev.key <= pygame.K_8:
                        pid = ev.key - pygame.K_0
                        if pid in panels:
                            view = pid
                            corners_mm.clear()
                            capture = None
                else:
                    # ---- radar do painel ----
                    if ev.key == pygame.K_ESCAPE:
                        view = None
                        capture = None
                    elif ev.key == pygame.K_q:
                        running = False
                    elif ev.key == pygame.K_b:
                        start_baseline(view)
                    elif ev.key == pygame.K_x:
                        corners_mm.clear()
                        capture = None
                        flash("cantos descartados")
                    elif ev.key == pygame.K_s:
                        try_save(view)
                    elif ev.key in (pygame.K_1, pygame.K_2, pygame.K_3,
                                    pygame.K_4):
                        if capture is not None:
                            flash("já capturando — aguarde")
                        else:
                            idx = ev.key - pygame.K_1
                            capture = {"idx": idx, "t0": now,
                                       "xs": [], "ys": []}

        for ps in panels.values():
            ps.reload_calib()

        # Drena tudo que chegou desde o último frame de tela.
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
            for p in frm.points:
                ps.cursors[p.id] = (p.x_mm, p.y_mm, now)
                if capture is not None and view == frm.panel_id:
                    capture["xs"].append(p.x_mm)
                    capture["ys"].append(p.y_mm)
                if ps.calib is not None:
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
            for tid in [t for t, c in ps.cursors.items()
                        if now - c[2] > POINT_TTL]:
                del ps.cursors[tid]

        # Max (áudio): /touch/N 1|0 — active1 de cada tela, só na transição.
        if max_osc is not None:
            for pid, ps in panels.items():
                a = 1 if ps.slots.slot_id[0] is not None else 0
                if a != ps.max_active:
                    ps.max_active = a
                    try:
                        max_osc.send_message(f"/touch/{pid}", a)
                    except OSError:
                        pass

        # Fecha a coleta do canto quando o tempo esgota.
        if capture is not None and now - capture["t0"] >= COLLECT_S:
            idx, n = capture["idx"], len(capture["xs"])
            if n < MIN_PTS:
                flash(f"{CORNER_NAMES[idx]} falhou ({n} pts, min={MIN_PTS}) "
                      f"— aperte {idx + 1} de novo")
            else:
                mx = statistics.median(capture["xs"])
                my = statistics.median(capture["ys"])
                corners_mm[idx] = (mx, my)
                flash(f"{CORNER_NAMES[idx]} ({mx:.0f},{my:.0f}) mm  {n} pts"
                      f"  [{len(corners_mm)}/4]")
            capture = None

        if ssh_done.get("msg"):
            flash(ssh_done.pop("msg"))

        if now - rate_t0 >= 1.0:
            dt = now - rate_t0
            for ps in panels.values():
                ps.in_rate = ps.in_count / dt
                ps.in_count = 0
            out_rate = out_count / dt
            out_count, rate_t0 = 0, now

        # ------------------------------ desenho ------------------------------
        screen.fill(BONE)
        n_on = sum(1 for ps in panels.values() if ps.online)
        max_txt = ("" if max_osc is None
                   else f"   MAX /touch/N → {args.max_dest}:{args.max_port}")
        hdr = (f"FLEET BRIDGE :{args.listen_port}   nós {n_on}/{len(panels)} ON"
               f"   out={out_rate:6.1f} pkt/s → {','.join(dests)}"
               f":{args.dest_port} ({args.format.upper()}){max_txt}")
        screen.blit(font_big.render(hdr, True, INK), (PAD, 20))

        if view is None:
            # ============================ GRADE =============================
            screen.blit(font.render(
                "1-8 ou clique: radar do painel  ·  A: baseline em TODOS "
                "(áreas livres!)  ·  Q sai", True, FG_MUTED), (PAD, 48))
            card_rects.clear()
            for i, pid in enumerate(panel_ids):
                ps = panels[pid]
                cx = PAD + (i % cols) * (card_w + PAD)
                cy = 96 + (i // cols) * (card_h + PAD)
                card_rects[pid] = pygame.Rect(cx, cy, card_w, card_h)
                pygame.draw.rect(screen, INK, card_rects[pid], 1)
                on = ps.online
                pygame.draw.circle(screen, MOSS if on else EMBER,
                                   (cx + 16, cy + 18), 6)
                screen.blit(font_big.render(
                    f"PAINEL {pid}  {'ON ' if on else 'OFF'}", True, INK),
                    (cx + 30, cy + 10))
                calib_ok = ps.calib is not None
                screen.blit(font.render(
                    f"in={ps.in_rate:5.1f}/s  f{ps.frame}  "
                    f"calib {'OK' if calib_ok else 'AUSENTE'}",
                    True, INK if calib_ok else EMBER), (cx + 30, cy + 32))
                mx0, my0 = cx + 28, cy + 56
                pygame.draw.rect(screen, RULE_SOFT,
                                 (mx0, my0, mini_w, mini_h), 1)
                ch = ps.slots.channels()
                for s, color in ((0, EMBER), (1, STEEL)):
                    x, y, a = ch[3 * s], ch[3 * s + 1], ch[3 * s + 2]
                    v = y if args.no_flip_y else 1.0 - y
                    px = (int(mx0 + x * mini_w), int(my0 + v * mini_h))
                    if a > 0.0:
                        pygame.draw.circle(screen, color, px, 7)
                    else:
                        pygame.draw.circle(screen, color, px, 7, 1)
                    screen.blit(font.render(
                        f"t{s+1} {x:.2f},{y:.2f} {'ON' if a else '--'}",
                        True, color),
                        (mx0 + s * (mini_w // 2 + 6), my0 + mini_h + 6))
        else:
            # ======================= RADAR DO PAINEL ========================
            ps = panels[view]
            hn = ps.hostname or "?"
            screen.blit(font.render(
                f"PAINEL {view} ({hn})   1-4 canto · S salva · X descarta · "
                "B baseline · ESC volta", True, FG_MUTED), (PAD, 48))

            # grade métrica 500 mm + ROI + sensor
            step = 500
            gx = int(WORLD_X[0] // step) * step
            while gx <= WORLD_X[1]:
                a2, b2 = to_px(gx, WORLD_Y[0]), to_px(gx, WORLD_Y[1])
                pygame.draw.line(screen, RULE_SOFT, a2, b2, 1)
                gx += step
            gy = 0
            while gy <= WORLD_Y[1]:
                a2, b2 = to_px(WORLD_X[0], gy), to_px(WORLD_X[1], gy)
                pygame.draw.line(screen, RULE_SOFT, a2, b2, 1)
                screen.blit(font.render(f"{gy/1000:.1f}m", True, FG_MUTED),
                            (rx0 + 4, a2[1] - 7))
                gy += step
            r0 = to_px(ROI_DRAW[0], ROI_DRAW[2])
            r1 = to_px(ROI_DRAW[1], ROI_DRAW[3])
            pygame.draw.rect(screen, STEEL,
                             pygame.Rect(r0, (r1[0] - r0[0], r1[1] - r0[1])), 2)
            sx, sy = to_px(0, 0)
            pygame.draw.polygon(screen, INK,
                                [(sx, sy + 10), (sx - 8, sy - 6),
                                 (sx + 8, sy - 6)])

            # cantos capturados
            if len(corners_mm) == 4:
                pygame.draw.polygon(screen, PLUM,
                                    [to_px(*corners_mm[i]) for i in range(4)],
                                    1)
            for idx, (cx2, cy2) in corners_mm.items():
                px = to_px(cx2, cy2)
                pygame.draw.line(screen, PLUM, (px[0] - 7, px[1] - 7),
                                 (px[0] + 7, px[1] + 7), 2)
                pygame.draw.line(screen, PLUM, (px[0] - 7, px[1] + 7),
                                 (px[0] + 7, px[1] - 7), 2)
                screen.blit(font.render(f"{idx + 1} {CORNER_NAMES[idx]}",
                                        True, PLUM), (px[0] + 10, px[1] + 6))

            # cursores ao vivo (mm)
            for tid, (cx2, cy2, _seen) in ps.cursors.items():
                px = to_px(cx2, cy2)
                pygame.draw.circle(screen, EMBER, px, 7)
                screen.blit(font.render(
                    f"#{tid} ({cx2:.0f},{cy2:.0f})", True, INK),
                    (px[0] + 10, px[1] - 8))

            status = (f"in={ps.in_rate:5.1f}/s  "
                      f"calib {'OK' if ps.calib is not None else 'AUSENTE'}")
            screen.blit(font.render(status, True, INK),
                        (win[0] - PAD - 220, 48))
            if capture is not None:
                rem = COLLECT_S - (now - capture["t0"])
                screen.blit(font_big.render(
                    f"CAPTURANDO {CORNER_NAMES[capture['idx']]} — não se mexa"
                    f" ({rem:.1f}s, {len(capture['xs'])} pts)", True, AMBER),
                    (PAD, ry0 + 4))

        if flash_msg and now - flash_t < 6.0:
            screen.blit(font_big.render(flash_msg, True, PLUM),
                        (PAD, win[1] - 30))

        pygame.display.flip()
        clock.tick(60)

    pygame.quit()
    rx.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
