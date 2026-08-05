"""
LidarMapper — teste da Etapa 4: tracking ao vivo com IDs persistentes.

Janela 1280x720 dividida ao meio:
  ESQUERDA  — viz do plano do sensor (mm):
              pontos crus (cinza), foreground (verde fraco),
              centróides de cluster (amarelo), tracks com ID (azul claro)
              + quadrilátero da calibração

  DIREITA   — minimapa 0..1 da tela:
              tracks projetados via homografia, cada um com:
                - círculo proporcional à confidence
                - label "id=N  u=0.xx v=0.xx"
                - rastro dos últimos ~0.5s

Teclas:
  ESC, Q : sair
  B      : recaptura o fundo (use se a cena mudou)
  +/-    : zoom no painel LIDAR
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from collections import deque, defaultdict

import numpy as np
import pygame

import config as cfg_mod
import paths
from homography import load_calibration
from lidar_reader import LidarReader
from processing import BackgroundSubtractor, Point2D, dbscan_centroids, project_batch, split_by_roi
from tracker import Tracker, Track


def world_to_panel(x_mm, y_mm, panel_w, panel_h, scale):
    return (int(panel_w / 2 + x_mm / scale), int(panel_h / 2 - y_mm / scale))


def color_for_id(track_id: int) -> tuple[int, int, int]:
    """Cor estável por ID — útil pra visual distinguir tracks."""
    palette = [
        (80, 220, 220),
        (240, 180, 80),
        (220, 80, 220),
        (120, 220, 80),
        (240, 100, 100),
        (80, 140, 240),
        (220, 220, 120),
        (200, 200, 200),
    ]
    return palette[(track_id - 1) % len(palette)]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=None)
    ap.add_argument("--port", default=None)
    ap.add_argument("--calib", default="calibration.json")
    ap.add_argument("--baseline-s", type=float, default=2)
    args = ap.parse_args()

    cfg = cfg_mod.load(args.config)

    calib_path = args.calib if os.path.isabs(args.calib) else os.path.join(paths.APP_DIR, args.calib)
    calib = load_calibration(calib_path)
    if calib is None:
        print(f"[tracker] sem calibração em {calib_path} — rode calibrate.py antes.", file=sys.stderr)
        return 1

    rdr = LidarReader(port=args.port or cfg.sensor.port, baud=cfg.sensor.baud)
    if not rdr.start():
        print(f"[tracker] não iniciou: {rdr.status}", file=sys.stderr)
        return 1

    bg = BackgroundSubtractor(bins=720, margin_mm=120)
    bg.begin_baseline()
    bg.configure_time(args.baseline_s)

    tr = Tracker(cfg.tracker)

    pygame.init()
    pygame.display.set_caption("LidarMapper — Etapa 4 (tracker)")
    W, H_ = 1280, 720
    screen = pygame.display.set_mode((W, H_))
    font = pygame.font.SysFont("consolas", 14)
    font_id = pygame.font.SysFont("consolas", 18, bold=True)
    clock = pygame.time.Clock()

    scale = cfg.viz.scale_mm_per_px
    left_w = W // 2
    right_w = W - left_w

    trails = defaultdict(lambda: deque(maxlen=30))

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
                        tr.reset()
                        trails.clear()

            measurements = rdr.drain()
            points = project_batch(measurements, cfg.processing)

            if not bg.ready:
                bg.feed(measurements)

            fg_pts = bg.foreground_points(points) if bg.ready else []
            fg_in, _ = split_by_roi(fg_pts, cfg.roi)

            fg_xy = np.array([(p.x, p.y) for p in fg_in], dtype=float) if fg_in else np.zeros((0, 2))

            centroids = dbscan_centroids(fg_xy, cfg.tracker.dbscan_eps_mm,
                                         cfg.tracker.dbscan_min_samples) if bg.ready else []

            tracks = tr.update(fg_xy, calib.H) if bg.ready else []

            now = time.monotonic()
            for t in tracks:
                trails[t.id].append((t.u, t.v, now))
            live_ids = {t.id for t in tracks}
            for tid in list(trails.keys()):
                if tid not in live_ids:
                    trails[tid].clear()
                    del trails[tid]

            screen.fill((10, 10, 14))

            # ---- painel esquerdo: plano do sensor ----
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
                sx, sy = world_to_panel(p.x, p.y, left_w, H_, scale)
                if 0 <= sx < left_w and 0 <= sy < H_:
                    screen.set_at((sx, sy), (80, 80, 90))

            for p in fg_in:
                sx, sy = world_to_panel(p.x, p.y, left_w, H_, scale)
                if 0 <= sx < left_w and 0 <= sy < H_:
                    pygame.draw.circle(screen, (40, 140, 60), (sx, sy), 2)

            poly = [world_to_panel(px, py, left_w, H_, scale) for px, py in calib.corners_lidar_mm]
            pygame.draw.polygon(screen, (180, 160, 40), poly, 1)

            for (ccx, ccy), n in centroids:
                sx, sy = world_to_panel(ccx, ccy, left_w, H_, scale)
                pygame.draw.circle(screen, (230, 200, 80), (sx, sy), 6, 1)

            for t in tracks:
                col = color_for_id(t.id)
                sx, sy = world_to_panel(t.x_mm, t.y_mm, left_w, H_, scale)
                r = 8 + int(8 * t.confidence)
                pygame.draw.circle(screen, col, (sx, sy), r, 2)
                lbl = font_id.render(f"#{t.id}", True, col)
                screen.blit(lbl, (sx + 10, sy - 10))

            ssx, ssy = world_to_panel(0, 0, left_w, H_, scale)
            pygame.draw.circle(screen, (240, 80, 80), (ssx, ssy), 5)

            # ---- painel direito: minimapa da tela ----
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
                msx = mm_x + int(u * mm_w)
                msy = mm_y + int(v * mm_h)
                pygame.draw.circle(screen, (180, 180, 220), (msx, msy), 5, 1)
                screen.blit(font.render(name, True, (180, 180, 220)), (msx + 6, msy + 6))

            for tid, trail in trails.items():
                col = color_for_id(tid)
                pts_scr = []
                for u, v, _t in trail:
                    if -0.05 <= u <= 1.05 and -0.05 <= v <= 1.05:
                        pts_scr.append((mm_x + int(u * mm_w), mm_y + int(v * mm_h)))
                if len(pts_scr) >= 2:
                    pygame.draw.lines(screen, col, False, pts_scr, 1)

            for t in tracks:
                col = color_for_id(t.id)
                if not (-0.05 <= t.u <= 1.05 and -0.05 <= t.v <= 1.05):
                    continue
                sx = mm_x + int(t.u * mm_w)
                sy = mm_y + int(t.v * mm_h)
                r = 6 + int(10 * t.confidence)
                pygame.draw.circle(screen, col, (sx, sy), r)
                pygame.draw.circle(screen, (10, 10, 20), (sx, sy), r, 1)
                lbl = font_id.render(f"#{t.id}", True, col)
                screen.blit(lbl, (sx + r + 4, sy - 12))
                sub = font.render(f"u={t.u:.2f} v={t.v:.2f}", True, col)
                screen.blit(sub, (sx + r + 4, sy + 6))

            # ---- HUD ----
            hud = [
                f"calib: {os.path.basename(calib_path)}   tela: {calib.screen_width_px}x{calib.screen_height_px}",
                f"meas/s={rdr.meas_per_sec:6.0f}  scans/s={rdr.scans_per_sec:4.1f}"
                f"  fg_pts={len(fg_in):4d}  clusters={len(centroids):2d}"
                f"  tracks={len(tracks)}/{cfg.tracker.max_tracks}",
                f"DBSCAN eps={cfg.tracker.dbscan_eps_mm:.0f}mm  min_samples={cfg.tracker.dbscan_min_samples}"
                f"  timeout={cfg.tracker.timeout_s:.2f}s",
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
