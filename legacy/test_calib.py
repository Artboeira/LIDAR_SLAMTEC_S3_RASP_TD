"""
LidarMapper — teste da Etapa 3: overlay de validação da calibração.

Carrega `calibration.json` e mostra, ao vivo:
  - LADO ESQUERDO: viz do plano do sensor (mm) com pontos crus +
    foreground (depois do baseline) e o quadrilátero salvo dos 4 cantos
  - LADO DIREITO: minimapa 0..1 da tela com cursores projetados via H

Teclas:
  ESC, Q : sair
  B      : recaptura o baseline (se a área mudou desde o calibrate)
  +/-    : zoom do painel esquerdo
"""
from __future__ import annotations

import argparse
import os
import sys
import time

import numpy as np
import pygame

import config as cfg_mod
import paths
from homography import apply_h, load_calibration
from lidar_reader import LidarReader
from processing import BackgroundSubtractor, project_batch, split_by_roi


def world_to_screen(x_mm, y_mm, x0, y0, panel_w, panel_h, scale):
    return (int(x0 + panel_w / 2 + x_mm / scale),
            int(y0 + panel_h / 2 - y_mm / scale))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=None)
    ap.add_argument("--calib", default="calibration.json")
    ap.add_argument("--port", default=None)
    ap.add_argument("--baseline-s", type=float, default=2)
    args = ap.parse_args()

    cfg = cfg_mod.load(args.config)

    calib_path = args.calib if os.path.isabs(args.calib) else os.path.join(paths.APP_DIR, args.calib)
    calib = load_calibration(calib_path)
    if calib is None:
        print(f"[test_calib] sem calibração em {calib_path} — rode 'python calibrate.py' antes.",
              file=sys.stderr)
        return 1

    print(f"[test_calib] carregado: tela {calib.screen_width_px}x{calib.screen_height_px}")
    print("[test_calib] cantos no LIDAR (mm):")
    for name, p in zip(("TL", "TR", "BR", "BL"), calib.corners_lidar_mm):
        print(f"  {name}: ({p[0]:+8.1f}, {p[1]:+8.1f})")

    port = args.port or cfg.sensor.port
    rdr = LidarReader(port=port, baud=cfg.sensor.baud)
    if not rdr.start():
        print(f"[test_calib] não iniciou: {rdr.status}", file=sys.stderr)
        return 1

    bg = BackgroundSubtractor(bins=720, margin_mm=120)
    bg.begin_baseline()
    bg.configure_time(args.baseline_s)
    baseline_t0 = time.monotonic()  # RECONSTRUÇÃO: atribuída no bytecode mas nunca lida depois

    pygame.init()
    pygame.display.set_caption("LidarMapper — Etapa 3 (teste da calibração)")
    W, H_ = 1280, 720
    screen = pygame.display.set_mode((W, H_))
    font = pygame.font.SysFont("consolas", 14)
    clock = pygame.time.Clock()

    scale = cfg.viz.scale_mm_per_px
    left_w = W // 2
    right_w = W - left_w

    running = True
    try:
        while running:
            for ev in pygame.event.get():
                if ev.type == pygame.QUIT:
                    running = False
                elif ev.type == pygame.KEYDOWN:
                    if ev.key in (pygame.K_ESCAPE, pygame.K_q):
                        running = False
                    elif ev.key in (pygame.K_PLUS, pygame.K_EQUALS, pygame.K_KP_PLUS):
                        scale = max(1, scale * 0.8)
                    elif ev.key in (pygame.K_MINUS, pygame.K_KP_MINUS):
                        scale = min(200, scale * 1.25)
                    elif ev.key == pygame.K_b:
                        bg.begin_baseline()
                        bg.configure_time(args.baseline_s)

            measurements = rdr.drain()
            points = project_batch(measurements, cfg.processing)
            bg.feed(measurements)

            screen.fill((10, 10, 14))

            # ----- painel esquerdo: plano do sensor (mm) -----
            pygame.draw.rect(screen, (20, 20, 26), (0, 0, left_w, H_))
            cx, cy = left_w / 2, H_ / 2

            step = 1000
            for i in range(-10, 11):
                x = int(cx + i * step / scale)
                if 0 <= x < left_w:
                    pygame.draw.line(screen, (40, 40, 45), (x, 0), (x, H_))
                y = int(cy + i * step / scale)
                if 0 <= y < H_:
                    pygame.draw.line(screen, (40, 40, 45), (0, y), (left_w, y))
            pygame.draw.line(screen, (70, 70, 80), (0, int(cy)), (left_w, int(cy)))
            pygame.draw.line(screen, (70, 70, 80), (int(cx), 0), (int(cx), H_))

            for p in points:
                sx, sy = world_to_screen(p.x, p.y, 0, 0, left_w, H_, scale)
                if 0 <= sx < left_w and 0 <= sy < H_:
                    screen.set_at((sx, sy), (80, 80, 90))

            fg_pts = bg.foreground_points(points) if bg.ready else []
            for p in fg_pts:
                sx, sy = world_to_screen(p.x, p.y, 0, 0, left_w, H_, scale)
                if 0 <= sx < left_w and 0 <= sy < H_:
                    pygame.draw.circle(screen, (60, 220, 90), (sx, sy), 2)

            poly = [world_to_screen(p[0], p[1], 0, 0, left_w, H_, scale)
                    for p in calib.corners_lidar_mm]
            pygame.draw.polygon(screen, (200, 180, 40), poly, 2)
            for i, p in enumerate(poly):
                pygame.draw.circle(screen, (240, 200, 60), p, 6, 2)
                screen.blit(font.render(("TL", "TR", "BR", "BL")[i], True, (240, 200, 60)),
                            (p[0] + 8, p[1] - 8))

            ssx, ssy = world_to_screen(0, 0, 0, 0, left_w, H_, scale)
            pygame.draw.circle(screen, (240, 80, 80), (ssx, ssy), 5)

            # ----- painel direito: minimapa 0..1 da tela -----
            pygame.draw.rect(screen, (16, 18, 30), (left_w, 0, right_w, H_))
            margin = 30
            ratio = calib.screen_height_px / max(calib.screen_width_px, 1)
            mm_w = right_w - 2 * margin
            mm_h = int(mm_w * ratio)
            if mm_h > H_ - 2 * margin:
                mm_h = H_ - 2 * margin
                mm_w = int(mm_h / ratio)
            mm_x = left_w + (right_w - mm_w) // 2
            mm_y = (H_ - mm_h) // 2
            pygame.draw.rect(screen, (30, 30, 50), (mm_x, mm_y, mm_w, mm_h))
            pygame.draw.rect(screen, (180, 180, 220), (mm_x, mm_y, mm_w, mm_h), 2)

            for (u, v), name in zip(calib.corners_screen_norm, ("TL", "TR", "BR", "BL")):
                sx = mm_x + int(u * mm_w)
                sy = mm_y + int(v * mm_h)
                pygame.draw.circle(screen, (180, 180, 220), (sx, sy), 5, 1)
                screen.blit(font.render(name, True, (180, 180, 220)), (sx + 6, sy + 6))

            n_in = 0
            for p in fg_pts:
                u, v = apply_h(calib.H, p.x, p.y)
                if -0.05 <= u <= 1.05 and -0.05 <= v <= 1.05:
                    n_in += 1
                    sx = mm_x + int(u * mm_w)
                    sy = mm_y + int(v * mm_h)
                    pygame.draw.circle(screen, (80, 220, 220), (sx, sy), 4)

            # ----- HUD -----
            base_msg = "BASELINE — aguarde…" if not bg.ready else "OK"
            hud = [
                f"calib: {os.path.basename(calib_path)}   tela: {calib.screen_width_px}x{calib.screen_height_px}",
                f"meas/s={rdr.meas_per_sec:6.0f}  scans/s={rdr.scans_per_sec:4.1f}"
                f"  fg_pts={len(fg_pts):4d}  in_screen={n_in:4d}",
                f"baseline: {base_msg}   bins aprendidos: {bg.learned_bins}/720",
                "keys: B re-baseline   +/- zoom   ESC sai",
            ]
            for i, line in enumerate(hud):
                screen.blit(font.render(line, True, (220, 220, 220)), (8, 8 + i * 16))

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
