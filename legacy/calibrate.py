"""
LidarMapper — Etapa 3: calibração visual no painel/projetor.

Abre em FULLSCREEN no display configurado em `config.yaml > screen.display_index`
(default 1 = segundo monitor). Mostra os 4 cantos da tela como alvos grandes
(cruz + círculos), com o canto ATIVO em amarelo e os outros apagados.

Fluxo:
  1. BASELINE  — captura o fundo estático por ~2s (mantenha a área livre).
  2. CAPTURE   — para cada canto: posicione objeto/mão sobre o alvo aceso,
                 confira o "detectando: N pts" em verde e aperte ESPAÇO.
                 Tela mostra "CAPTURANDO..." e bloqueia ~1.2s acumulando pontos
                 foreground; o centróide do maior cluster vira o ponto desse canto.
  3. DONE      — modo teste: cursor verde sobre a tela onde sua mão estiver.
                 R = refaz a calibração, ESC = sai.

Teclas:
  SPACE     confirma o canto atual (em CAPTURE)
  B         re-captura o fundo (se algo entrou na cena no baseline)
  R         refaz a calibração (em DONE)
  ESC / Q   sai

Sobre a tela alvo:
  As coordenadas dos alvos ficam dentro de uma margem (default 6% da borda),
  porque você precisa conseguir alcançá-los fisicamente. Esses pontos são
  mapeados para o espaço normalizado 0..1 do TouchDesigner.
"""
from __future__ import annotations

import argparse
import math
import os
import sys
import time

import numpy as np
import pygame

import config as cfg_mod
import paths
from homography import Calibration, apply_h, compute_homography, save_calibration
from lidar_reader import LidarReader
from processing import BackgroundSubtractor, Point2D, cluster_greedy, project_batch, split_by_roi

CORNER_NAMES = ("SUPERIOR ESQUERDO", "SUPERIOR DIREITO", "INFERIOR DIREITO", "INFERIOR ESQUERDO")
CORNERS_NORM = ((0, 0), (1, 0), (1, 1), (0, 1))
TARGET_MARGIN = 0.06
C_BG = (0, 25, 60)
C_BG_BUSY = (0, 30, 70)
C_WHITE = (240, 240, 240)
C_YELLOW = (240, 200, 60)
C_GREEN = (80, 220, 100)
C_RED = (230, 80, 80)
C_GREY = (110, 110, 120)
C_DIM = (180, 180, 200)


def build_screen(scfg) -> pygame.Surface:
    """Abre a janela em fullscreen no display escolhido.
    Se o índice for inválido, cai no primário sem crashar."""
    flags = pygame.SCALED | (pygame.FULLSCREEN if scfg.fullscreen else 0)
    try:
        return pygame.display.set_mode((scfg.width_px, scfg.height_px), flags,
                                       display=scfg.display_index)
    except (TypeError, pygame.error):
        return pygame.display.set_mode((scfg.width_px, scfg.height_px), flags)


def corner_targets(scfg) -> list[tuple[int, int]]:
    """Posições dos 4 alvos em pixels da tela (TL, TR, BR, BL)."""
    mx = int(scfg.width_px * TARGET_MARGIN)
    my = int(scfg.height_px * TARGET_MARGIN)
    w, h = scfg.width_px, scfg.height_px
    return [(mx, my), (w - mx, my), (w - mx, h - my), (mx, h - my)]


def target_norm(scfg) -> list[tuple[float, float]]:
    """Mesmos 4 alvos, mas em coords normalizadas 0..1."""
    return [(p[0] / scfg.width_px, p[1] / scfg.height_px) for p in corner_targets(scfg)]


def draw_target(surf, pos, active: bool, fps_pulse: float = 0):
    """Mira grande: anel externo, ponto central e cruz. Ativo = amarelo + pulse."""
    color = C_YELLOW if active else C_GREY
    if active:
        r = 60 + int(8 * math.sin(fps_pulse * 4))
        pygame.draw.circle(surf, color, pos, r, 6)
    else:
        pygame.draw.circle(surf, color, pos, 60, 6)
    pygame.draw.circle(surf, color, pos, 14)
    pygame.draw.line(surf, color, (pos[0] - 90, pos[1]), (pos[0] + 90, pos[1]), 3)
    pygame.draw.line(surf, color, (pos[0], pos[1] - 90), (pos[0], pos[1] + 90), 3)


def text_center(surf, font, s, color, center):
    r = font.render(s, True, color)
    surf.blit(r, r.get_rect(center=center))


def collect_corner(rdr: LidarReader, bg: BackgroundSubtractor, cfg, duration_s: float,
                   cluster_radius_mm: float, min_pts: int) -> tuple[tuple[float, float] | None, int]:
    """Coleta foreground por `duration_s` e devolve (centróide_maior_cluster, n).
    None se o maior cluster não atinge `min_pts`."""
    rdr.drain()
    buf = []
    t0 = time.monotonic()
    while time.monotonic() - t0 < duration_s:
        meas = rdr.drain()
        pts = project_batch(meas, cfg.processing)
        fg = bg.foreground_points(pts)
        inside, _ = split_by_roi(fg, cfg.roi)
        for p in inside:
            buf.append((p.x, p.y))
        time.sleep(0.01)
    clusters = cluster_greedy(buf, cluster_radius_mm)
    if not clusters or clusters[0][1] < min_pts:
        return (None, clusters[0][1] if clusters else 0)
    (cx, cy), n = clusters[0]
    return ((cx, cy), n)


def count_recent_fg(buf: list, window_s: float) -> int:
    """Conta quantos pontos foreground caíram nos últimos `window_s` (live)."""
    now = time.monotonic()
    return sum(1 for _, _, t in buf if now - t <= window_s)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=None)
    ap.add_argument("--port", default=None, help="sobrescreve sensor.port")
    ap.add_argument("--out", default="calibration.json")
    ap.add_argument("--display", type=int, default=None,
                    help="sobrescreve screen.display_index")
    ap.add_argument("--no-fullscreen", action="store_true",
                    help="abre em janela (debug local)")
    ap.add_argument("--baseline-s", type=float, default=2)
    ap.add_argument("--collect-s", type=float, default=1.2)
    ap.add_argument("--cluster-mm", type=float, default=200,
                    help="raio do cluster greedy ao escolher o objeto no canto")
    ap.add_argument("--min-pts", type=int, default=5,
                    help="mínimo de pontos no cluster pra aceitar o canto")
    args = ap.parse_args()

    cfg = cfg_mod.load(args.config)
    if args.display is not None:
        cfg.screen.display_index = args.display
    if args.no_fullscreen:
        cfg.screen.fullscreen = False

    rdr = LidarReader(port=args.port or cfg.sensor.port, baud=cfg.sensor.baud)
    if not rdr.start():
        print(f"[calib] não iniciou: {rdr.status}", file=sys.stderr)
        return 1

    pygame.init()
    pygame.display.set_caption("LidarMapper — Calibração")
    screen = build_screen(cfg.screen)
    w, h = cfg.screen.width_px, cfg.screen.height_px
    cx = w // 2
    big = pygame.font.SysFont("consolas", 70, bold=True)
    mid = pygame.font.SysFont("consolas", 36)
    small = pygame.font.SysFont("consolas", 22)
    clock = pygame.time.Clock()

    bg = BackgroundSubtractor(bins=720, margin_mm=120)
    bg.begin_baseline()
    bg.configure_time(args.baseline_s)

    targets = corner_targets(cfg.screen)
    targets_norm = target_norm(cfg.screen)

    phase = "baseline"
    idx = 0
    captured_lidar = []
    H = None
    status = ""
    out_path = args.out if os.path.isabs(args.out) else os.path.join(paths.APP_DIR, args.out)
    live_fg_buf = []
    markers = []
    running = True
    saved = False

    try:
        while running:
            now = time.monotonic()
            for ev in pygame.event.get():
                if ev.type == pygame.QUIT:
                    running = False
                elif ev.type == pygame.KEYDOWN:
                    if ev.key in (pygame.K_ESCAPE, pygame.K_q):
                        running = False
                    elif ev.key == pygame.K_b and phase == "capture":
                        bg.begin_baseline()
                        bg.configure_time(args.baseline_s)
                        status = "Recapturando fundo..."
                        phase = "baseline"
                    elif ev.key == pygame.K_r and phase == "done":
                        captured_lidar.clear()
                        idx = 0
                        H = None
                        saved = False
                        status = ""
                        phase = "capture"
                    elif ev.key == pygame.K_SPACE and phase == "capture":
                        screen.fill(C_BG_BUSY)
                        text_center(screen, big, "CAPTURANDO...", C_WHITE, (cx, h // 2))
                        text_center(screen, mid, f"{CORNER_NAMES[idx]} — não se mexa",
                                    C_YELLOW, (cx, h // 2 + 80))
                        pygame.display.flip()
                        pt, n = collect_corner(rdr, bg, cfg,
                                               duration_s=args.collect_s,
                                               cluster_radius_mm=args.cluster_mm,
                                               min_pts=args.min_pts)
                        if pt is None:
                            status = f"Não detectei objeto suficiente ({n} pts). Posicione melhor e tente de novo."
                            print(f"[calib] {CORNER_NAMES[idx]}: falha ({n} pts)")
                        else:
                            captured_lidar.append(pt)
                            print(f"[calib] {CORNER_NAMES[idx]}: ({pt[0]:+8.1f}, {pt[1]:+8.1f}) mm  ({n} pts)")
                            idx += 1
                            status = ""
                            if idx >= 4:
                                try:
                                    H = compute_homography(
                                        captured_lidar,
                                        [(float(u), float(v)) for u, v in targets_norm])
                                    calib = Calibration(
                                        H=H,
                                        corners_lidar_mm=captured_lidar.copy(),
                                        corners_screen_norm=[(float(u), float(v)) for u, v in targets_norm],
                                        screen_width_px=cfg.screen.width_px,
                                        screen_height_px=cfg.screen.height_px)
                                    save_calibration(out_path, calib)
                                    saved = True
                                    print(f"[calib] homografia salva em {out_path}")
                                    phase = "done"
                                except Exception as exc:
                                    status = f"falha ao calcular H: {exc}"
                                    captured_lidar.clear()
                                    idx = 0

            measurements = rdr.drain()
            points = project_batch(measurements, cfg.processing)

            if phase == "baseline":
                bg.feed(measurements)
                if bg.ready:
                    phase = "capture"
                    status = ""

            if bg.ready and phase != "baseline":
                fg = bg.foreground_points(points)
                inside, _ = split_by_roi(fg, cfg.roi)
                for p in inside:
                    live_fg_buf.append((p.x, p.y, now))
                cutoff = now - 0.2
                live_fg_buf = [t for t in live_fg_buf if t[2] >= cutoff]

            screen.fill(C_BG)
            pulse = now * 2

            if phase == "baseline":
                rest = max(0, bg._until - now)
                text_center(screen, big, "CAPTURANDO O FUNDO...", C_WHITE,
                            (cx, h // 2 - 50))
                text_center(screen, mid, "Mantenha a área da tela LIVRE", C_YELLOW,
                            (cx, h // 2 + 30))
                text_center(screen, mid, f"{rest:3.1f}s   bins: {bg.learned_bins}/720",
                            C_DIM, (cx, h // 2 + 80))
            elif phase == "capture":
                for i, pos in enumerate(targets):
                    draw_target(screen, pos, active=i == idx, fps_pulse=pulse)
                text_center(screen, big, f"CANTO {idx + 1}/4", C_WHITE, (cx, h // 2 - 60))
                text_center(screen, mid, CORNER_NAMES[idx], C_YELLOW, (cx, h // 2 - 10))
                text_center(screen, mid, "Toque/segure o objeto no alvo aceso e aperte ESPAÇO",
                            C_WHITE, (cx, h // 2 + 50))
                n_live = len(live_fg_buf)
                if not bg.ready:
                    msg, col = "preparando fundo...", C_DIM
                elif n_live >= args.min_pts:
                    msg, col = f"detectando: {n_live} pts ✓ pronto pra capturar", C_GREEN
                else:
                    msg, col = (f"detectando: {n_live} pts (posicione melhor)",
                                C_RED if n_live == 0 else C_YELLOW)
                text_center(screen, mid, msg, col, (cx, h // 2 + 110))
                text_center(screen, small, "B = recapturar fundo   |   ESC = sair",
                            C_DIM, (cx, h - 60))
                if status:
                    text_center(screen, mid, status, C_RED, (cx, h - 110))
            else:
                if H is not None:
                    for lx, ly, _t in live_fg_buf:
                        u, v = apply_h(H, lx, ly)
                        if not (-0.05 <= u <= 1.05):
                            continue
                        if not (-0.05 <= v <= 1.05):
                            continue
                        sx = int(u * w)
                        sy = int(v * h)
                        pygame.draw.circle(screen, C_GREEN, (sx, sy), 12)
                        pygame.draw.circle(screen, C_WHITE, (sx, sy), 26, 2)
                for pos in targets:
                    pygame.draw.circle(screen, (60, 60, 70), pos, 30, 2)
                    pygame.draw.line(screen, (60, 60, 70), (pos[0] - 40, pos[1]),
                                     (pos[0] + 40, pos[1]), 1)
                    pygame.draw.line(screen, (60, 60, 70), (pos[0], pos[1] - 40),
                                     (pos[0], pos[1] + 40), 1)
                text_center(screen, big, "CALIBRADO" + (" ✓ SALVO" if saved else ""),
                            C_GREEN, (cx, 60))
                text_center(screen, mid, "Toque a tela — o ponto verde deve cair onde você tocou",
                            C_WHITE, (cx, 130))
                text_center(screen, mid, "R = refazer calibração   |   ESC = sair",
                            C_YELLOW, (cx, 180))

            stat = (f"port={rdr.port}  meas/s={rdr.meas_per_sec:.0f}"
                    f"  scans/s={rdr.scans_per_sec:.1f}  fg_ready={int(bg.ready)}"
                    f"  desync={rdr.desyncs}  recon={rdr.reconnects}")
            text_center(screen, small, stat, C_DIM, (cx, h - 20))
            pygame.display.flip()
            clock.tick(60)
    except KeyboardInterrupt:
        pass
    finally:
        rdr.stop()
        pygame.quit()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
