"""Radar em tempo real dos cursores V2 (Pi -> servidor) + calibração visual.

Escuta a porta de entrada V2 (:5555 por padrão) e plota os cursores em mm
num plano visto de cima: sensor na origem (topo), y crescendo para baixo.
Desenha a ROI configurada no nó para ajudar a ajustar montagem/ROI em campo.

Calibração pelo teclado: posicione a mão no canto físico do painel, confira
o ponto no radar e aperte 1-4 (1 TOP-LEFT, 2 TOP-RIGHT, 3 BOTTOM-RIGHT,
4 BOTTOM-LEFT). Cada tecla coleta ~2 s de amostras (mediana). Com os 4
cantos capturados, S valida (recusa geometria degenerada, igual ao
calibrate.py) e salva server/calib_pN.json — o relay faz hot-reload.

Diferença para o calibrate.py: aqui o alvo é o CANTO FÍSICO do painel
(norm 0/1 exato), não um alvo desenhado com inset na tela do TD.

Ponte para o TouchDesigner (mesmo contrato do demo_touch_bridge.py):
com --dest, aplica a homografia do calib_pN.json (hot-reload por mtime) e
envia os 6 canais x1 y1 active1 x2 y2 active2 (0..1, origem embaixo-esq)
via OSC ou CSV. O HUD mostra o destino, a taxa de saída, os canais ao vivo
e um mini-painel com os toques em coordenadas de tela.

ATENÇÃO: ocupa a mesma porta do server_relay.py / calibrate.py — rode um
de cada vez.

Uso:
    python server/radar_view.py                          # só radar, :5555
    python server/radar_view.py --panel 1                # filtra/calibra p1
    python server/radar_view.py --panel 1 --dest 192.168.1.101   # + ponte TD

Teclas: 1-4 captura canto · S salva calib · X descarta cantos ·
        C limpa trilhas · Q/ESC sai.
"""

from __future__ import annotations

import argparse
import collections
import os
import socket
import statistics
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.protocol import ProtocolError, unpack_v2  # noqa: E402
from server.calibrate import (  # noqa: E402
    CORNER_NAMES, degenerate_reason, reprojection_error_px,
)
from server.homography import (  # noqa: E402
    Calibration, apply_h, compute_homography, load_calibration,
    save_calibration,
)
from server.demo_touch_bridge import TouchSlots  # noqa: E402

# Paleta Estúdio AB (colors_and_type.css)
BONE = (237, 229, 211)     # fundo
INK = (34, 34, 35)         # texto / sensor
RULE_SOFT = (188, 181, 166)  # grade (ink 18% sobre bone, pré-composto)
STEEL = (75, 101, 126)     # ROI
EMBER = (191, 65, 40)      # cursores
MOSS = (137, 153, 62)      # trilhas
AMBER = (238, 162, 68)     # captura em andamento
PLUM = (88, 45, 64)        # cantos capturados
FG_MUTED = (69, 70, 63)    # labels

WINDOW = (900, 980)
MARGIN_PX = 60
TRAIL_LEN = 90             # ~3 s a 30 Hz
POINT_TTL = 0.6            # s sem update -> some

# Cantos físicos do painel em coords normalizadas (ordem = CORNER_NAMES).
CORNERS_NORM = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
COLLECT_S = 2.0
MIN_PTS = 30
SCREEN_WH = (1920, 1080)   # só como referência do erro de reprojeção em px


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description="radar em tempo real do tráfego V2 + calibração 1-4/S")
    ap.add_argument("--port", type=int, default=5555)
    ap.add_argument("--panel", type=int, default=None,
                    help="filtra e calibra este panel_id (padrão: todos; a "
                         "calibração exige um painel único na tela ou --panel)")
    ap.add_argument("--roi", default="-1400,1400,100,3800",
                    help="x_min,x_max,y_min,y_max em mm (igual ao node-config)")
    # --- ponte TD (opcional; mesmo contrato do demo_touch_bridge.py) ---
    ap.add_argument("--dest", default=None,
                    help="IP do Windows/TD — liga a ponte de 6 canais; aceita "
                         "vários separados por vírgula (127.0.0.1,192.168.1.101)")
    ap.add_argument("--dest-port", type=int, default=7000,
                    help="porta no TD (OSC In CHOP ou UDP In DAT) [7000]")
    ap.add_argument("--format", choices=("osc", "csv"), default="osc")
    ap.add_argument("--calib", default=None,
                    help="calib JSON [server/calib_p<panel>.json]")
    ap.add_argument("--no-flip-y", action="store_true",
                    help="mantém origem em cima-esquerda (default: embaixo-esquerda)")
    args = ap.parse_args()
    if args.dest is not None and args.panel is None:
        ap.error("--dest exige --panel N (qual painel alimenta o TD)")
    return args


def main() -> int:
    args = parse_args()
    x_min, x_max, y_min, y_max = (float(v) for v in args.roi.split(","))

    import pygame  # tardio: só o radar precisa

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("0.0.0.0", args.port))
    sock.setblocking(False)

    pygame.init()
    screen = pygame.display.set_mode(WINDOW)
    pygame.display.set_caption(f"radar v2 :{args.port}")
    font = pygame.font.SysFont("Menlo", 13)
    font_big = pygame.font.SysFont("Menlo", 17, bold=True)
    clock = pygame.time.Clock()

    # Mundo visível: ROI + folga, sensor (0,0) sempre incluído.
    wx0, wx1 = min(x_min, 0) - 200, max(x_max, 0) + 200
    wy0, wy1 = -150.0, y_max + 300
    scale = min((WINDOW[0] - 2 * MARGIN_PX) / (wx1 - wx0),
                (WINDOW[1] - 2 * MARGIN_PX) / (wy1 - wy0))

    def to_px(x_mm: float, y_mm: float) -> tuple[int, int]:
        return (int(MARGIN_PX + (x_mm - wx0) * scale),
                int(MARGIN_PX + (y_mm - wy0) * scale))

    # Estado por (panel, track_id): posição, última vez visto, trilha.
    cursors: dict[tuple[int, int], dict] = {}
    trails: dict[tuple[int, int], collections.deque] = {}
    pkt_times: collections.deque = collections.deque(maxlen=120)
    last_frame_by_panel: dict[int, int] = {}

    # Calibração: cantos capturados (mm) por índice; captura em andamento.
    corners_mm: dict[int, tuple[float, float]] = {}
    capture: dict | None = None   # {idx, t0, xs, ys}
    msg, msg_t = "", 0.0

    # Ponte TD (só com --dest): calib hot-reload + slots + saída OSC/CSV.
    bridge_on = args.dest is not None
    calib_path = args.calib or os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        f"calib_p{args.panel}.json")
    calib = None
    calib_mtime = 0.0
    osc_clients: list = []
    csv_sock = None
    dests = ([d.strip() for d in args.dest.split(",") if d.strip()]
             if bridge_on else [])
    slots = TouchSlots()
    out_count, out_rate = 0, 0.0
    rate_t0 = time.time()
    if bridge_on:
        if args.format == "osc":
            from pythonosc.udp_client import SimpleUDPClient
            osc_clients = [SimpleUDPClient(d, args.dest_port) for d in dests]
        else:
            csv_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    def bridge_send(frm) -> None:
        """Um frame V2 do painel-alvo -> 6 canais pro TD (contrato do demo)."""
        nonlocal out_count
        tracks: list[tuple[int, float, float]] = []
        if calib is not None:
            for p in frm.points:
                u, v = apply_h(calib.H, p.x_mm, p.y_mm)
                u = min(1.0, max(0.0, u))
                v = min(1.0, max(0.0, v))
                y = v if args.no_flip_y else 1.0 - v
                tracks.append((p.id, u, y))
        slots.update(tracks, time.time())
        ch = slots.channels()
        if osc_clients:
            for cli in osc_clients:
                for name, val in zip(("/x1", "/y1", "/active1",
                                      "/x2", "/y2", "/active2"), ch):
                    cli.send_message(name, val)
        else:
            line = ",".join(f"{c:.4f}" for c in ch) + "\n"
            for d in dests:
                csv_sock.sendto(line.encode("ascii"), (d, args.dest_port))
        out_count += 1

    def flash(text: str) -> None:
        nonlocal msg, msg_t
        msg, msg_t = text, time.time()

    def calib_panel() -> int | None:
        """Painel a calibrar: --panel, ou o único visto no tráfego."""
        if args.panel is not None:
            return args.panel
        if len(last_frame_by_panel) == 1:
            return next(iter(last_frame_by_panel))
        return None

    def try_save() -> None:
        if len(corners_mm) < 4:
            faltam = [CORNER_NAMES[i] for i in range(4) if i not in corners_mm]
            flash(f"faltam cantos: {', '.join(faltam)}")
            return
        panel = calib_panel()
        if panel is None:
            flash("painel ambíguo — rode com --panel N para salvar")
            return
        pts_mm = [corners_mm[i] for i in range(4)]
        motivo = degenerate_reason(pts_mm)
        if motivo is not None:
            flash(f"RECUSADA: {motivo} — recapture (X descarta tudo)")
            return
        H = compute_homography(pts_mm, CORNERS_NORM)
        errs = reprojection_error_px(H, pts_mm, CORNERS_NORM, SCREEN_WH)
        out = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           f"calib_p{panel}.json")
        save_calibration(out, Calibration(
            H=H, corners_lidar_mm=pts_mm, corners_screen_norm=CORNERS_NORM,
            screen_width_px=SCREEN_WH[0], screen_height_px=SCREEN_WH[1]))
        flash(f"SALVO calib_p{panel}.json — erros(px): "
              + " ".join(f"{e:.1f}" for e in errs))

    running = True
    while running:
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                running = False
            elif ev.type == pygame.KEYDOWN:
                if ev.key in (pygame.K_q, pygame.K_ESCAPE):
                    running = False
                elif ev.key == pygame.K_c:
                    trails.clear()
                elif ev.key == pygame.K_x:
                    corners_mm.clear()
                    capture = None
                    flash("cantos descartados")
                elif ev.key in (pygame.K_1, pygame.K_2, pygame.K_3, pygame.K_4):
                    if capture is not None:
                        flash("já capturando — aguarde")
                    elif calib_panel() is None:
                        flash("painel ambíguo — rode com --panel N")
                    else:
                        idx = ev.key - pygame.K_1
                        capture = {"idx": idx, "t0": time.time(),
                                   "xs": [], "ys": []}
                elif ev.key == pygame.K_s:
                    try_save()

        # Hot-reload do calib por mtime (mesma disciplina do relay).
        if bridge_on:
            try:
                mtime = os.path.getmtime(calib_path)
                if mtime != calib_mtime:
                    c = load_calibration(calib_path)
                    if c is not None:
                        calib = c
                        calib_mtime = mtime
                        flash(f"calibração carregada: {os.path.basename(calib_path)}")
            except OSError:
                pass

        # Drena tudo que chegou desde o último frame de tela.
        sent_this_tick = False
        while True:
            try:
                data, _addr = sock.recvfrom(2048)
            except BlockingIOError:
                break
            try:
                frm = unpack_v2(data)
            except ProtocolError:
                continue
            if args.panel is not None and frm.panel_id != args.panel:
                continue
            now = time.time()
            pkt_times.append(now)
            last_frame_by_panel[frm.panel_id] = frm.frame
            grab = (capture is not None and frm.panel_id == calib_panel())
            for p in frm.points:
                key = (frm.panel_id, p.id)
                cursors[key] = {"x": p.x_mm, "y": p.y_mm,
                                "conf": p.confidence, "seen": now}
                trails.setdefault(key, collections.deque(maxlen=TRAIL_LEN)) \
                      .append((p.x_mm, p.y_mm))
                if grab:
                    capture["xs"].append(p.x_mm)
                    capture["ys"].append(p.y_mm)
            if bridge_on and frm.panel_id == args.panel:
                bridge_send(frm)
                sent_this_tick = True

        now = time.time()
        if bridge_on and not sent_this_tick:
            # sem pacote do painel: expira slots (nó caiu != toque preso)
            slots.update([], now)
        if bridge_on and now - rate_t0 >= 1.0:
            out_rate = out_count / (now - rate_t0)
            out_count, rate_t0 = 0, now
        for key in [k for k, c in cursors.items() if now - c["seen"] > POINT_TTL]:
            del cursors[key]

        # Fecha a coleta do canto quando o tempo esgota.
        if capture is not None and now - capture["t0"] >= COLLECT_S:
            idx, n = capture["idx"], len(capture["xs"])
            if n < MIN_PTS:
                flash(f"{CORNER_NAMES[idx]} falhou ({n} pts, min={MIN_PTS}) — "
                      f"aperte {idx + 1} de novo")
            else:
                mx = statistics.median(capture["xs"])
                my = statistics.median(capture["ys"])
                corners_mm[idx] = (mx, my)
                flash(f"{CORNER_NAMES[idx]} ({mx:.0f},{my:.0f}) mm  {n} pts  "
                      f"[{len(corners_mm)}/4]")
            capture = None

        # --- desenho ---
        screen.fill(BONE)

        # Grade métrica a cada 500 mm.
        step = 500
        gx = int(wx0 // step) * step
        while gx <= wx1:
            a, b = to_px(gx, wy0), to_px(gx, wy1)
            pygame.draw.line(screen, RULE_SOFT, a, b, 1)
            screen.blit(font.render(f"{gx/1000:+.1f}m", True, FG_MUTED),
                        (a[0] + 3, WINDOW[1] - MARGIN_PX + 8))
            gx += step
        gy = 0
        while gy <= wy1:
            a, b = to_px(wx0, gy), to_px(wx1, gy)
            pygame.draw.line(screen, RULE_SOFT, a, b, 1)
            screen.blit(font.render(f"{gy/1000:.1f}m", True, FG_MUTED),
                        (6, a[1] - 7))
            gy += step

        # ROI e sensor.
        r0, r1 = to_px(x_min, y_min), to_px(x_max, y_max)
        pygame.draw.rect(screen, STEEL,
                         pygame.Rect(r0, (r1[0] - r0[0], r1[1] - r0[1])), 2)
        sx, sy = to_px(0, 0)
        pygame.draw.polygon(screen, INK,
                            [(sx, sy + 10), (sx - 8, sy - 6), (sx + 8, sy - 6)])

        # Cantos capturados (X plum) e polígono quando os 4 existem.
        if len(corners_mm) == 4:
            pygame.draw.polygon(screen, PLUM,
                                [to_px(*corners_mm[i]) for i in range(4)], 1)
        for idx, (cx, cy) in corners_mm.items():
            px = to_px(cx, cy)
            pygame.draw.line(screen, PLUM, (px[0] - 7, px[1] - 7),
                             (px[0] + 7, px[1] + 7), 2)
            pygame.draw.line(screen, PLUM, (px[0] - 7, px[1] + 7),
                             (px[0] + 7, px[1] - 7), 2)
            screen.blit(font.render(f"{idx + 1} {CORNER_NAMES[idx]}",
                                    True, PLUM), (px[0] + 10, px[1] + 6))

        # Trilhas e cursores.
        for key, tr in trails.items():
            if len(tr) > 1:
                pygame.draw.lines(screen, MOSS, False,
                                  [to_px(x, y) for x, y in tr], 1)
        for (panel, tid), c in cursors.items():
            px = to_px(c["x"], c["y"])
            pygame.draw.circle(screen, EMBER, px, 7)
            label = f"p{panel}#{tid} ({c['x']:.0f},{c['y']:.0f})"
            screen.blit(font.render(label, True, INK), (px[0] + 10, px[1] - 8))

        # HUD.
        rate = 0.0
        if len(pkt_times) > 1 and now - pkt_times[0] > 0:
            rate = (len(pkt_times) - 1) / (now - pkt_times[0])
        panels = " ".join(f"p{p}:f{f}" for p, f in sorted(last_frame_by_panel.items()))
        hud = (f"RADAR V2 :{args.port}  pkts={rate:5.1f}/s  cursores={len(cursors)}  "
               f"{panels or 'aguardando tráfego...'}")
        screen.blit(font.render(hud, True, INK), (MARGIN_PX, 14))
        screen.blit(font.render(
            f"ROI x[{x_min:.0f},{x_max:.0f}] y[{y_min:.0f},{y_max:.0f}] mm   "
            "1-4 canto · S salva · X descarta · C trilhas · Q sai",
            True, FG_MUTED), (MARGIN_PX, 32))

        if capture is not None:
            rem = COLLECT_S - (now - capture["t0"])
            screen.blit(font_big.render(
                f"CAPTURANDO {CORNER_NAMES[capture['idx']]} — não se mexa "
                f"({rem:.1f}s, {len(capture['xs'])} pts)", True, AMBER),
                (MARGIN_PX, 52))
        elif msg and now - msg_t < 6.0:
            screen.blit(font_big.render(msg, True, PLUM), (MARGIN_PX, 52))

        # Painel da ponte TD: destino, canais e mini-tela com os toques.
        if bridge_on:
            ch = slots.channels()
            mini_w, mini_h = 224, 126        # 16:9, canto superior direito
            mx0 = WINDOW[0] - MARGIN_PX - mini_w
            my0 = 76
            pygame.draw.rect(screen, BONE, (mx0, my0, mini_w, mini_h))
            pygame.draw.rect(screen, INK, (mx0, my0, mini_w, mini_h), 1)
            status = f"TD {args.format.upper()} {','.join(dests)}:{args.dest_port}"
            calib_txt = ("calib OK" if calib is not None
                         else "SEM CALIB (active=0)")
            screen.blit(font.render(status, True, INK), (mx0, my0 - 34))
            screen.blit(font.render(
                f"out={out_rate:5.1f}/s  {calib_txt}", True,
                INK if calib is not None else EMBER), (mx0, my0 - 17))
            for s, color in ((0, EMBER), (1, STEEL)):
                x, y, a = ch[3 * s], ch[3 * s + 1], ch[3 * s + 2]
                # desenha em origem cima-esq; desfaz o flip se ele existe
                v = y if args.no_flip_y else 1.0 - y
                px = (int(mx0 + x * mini_w), int(my0 + v * mini_h))
                if a > 0.0:
                    pygame.draw.circle(screen, color, px, 6)
                else:
                    pygame.draw.circle(screen, color, px, 6, 1)
                screen.blit(font.render(
                    f"t{s+1} {x:.2f},{y:.2f} {'ON' if a else '--'}",
                    True, color), (mx0, my0 + mini_h + 4 + 15 * s))

        pygame.display.flip()
        clock.tick(60)

    pygame.quit()
    sock.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
