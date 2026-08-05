"""
LidarMapper — teste da Etapa 2: visualização 2D dos pontos filtrados.

Janela pygame mostrando, ao vivo:
  - sensor no centro
  - eixos cinza + grade a cada 1 m
  - ROI configurada (config.yaml) desenhada em amarelo
  - pontos DENTRO da ROI em verde, pontos FORA em cinza
  - HUD com FPS de leitura, FPS de viz, scans/s, contagem de pontos,
    porta e status

Teclas:
  ESC, Q : sair
  + / -  : zoom (altera scale_mm_per_px em runtime)
  R      : reset zoom (volta ao valor do config)
  TAB    : alterna mostrar/ocultar pontos fora da ROI

Convenção visual: +x do sensor aponta pra DIREITA, +y aponta pra CIMA
(coordenadas matemáticas). O pygame tem y crescendo pra baixo, então
fazemos `py = h/2 - y/scale`.
"""
from __future__ import annotations

import argparse
import math
import sys
import time

import pygame

import config as cfg_mod
from lidar_reader import LidarReader
from processing import project_batch, split_by_roi


def world_to_screen(x_mm: float, y_mm: float, w: int, h: int,
                    scale: float) -> tuple[int, int]:
    """Sensor em (w/2, h/2), +y do mundo aponta pra cima."""
    return (int(w / 2 + x_mm / scale), int(h / 2 - y_mm / scale))


def draw_grid(surf: pygame.Surface, w: int, h: int, scale: float) -> None:
    """Grade a cada 1 m + eixos + círculos em 1, 2, 3, 5 m."""
    g = (40, 40, 40)
    axis = (70, 70, 70)
    step_mm = 1000
    cx, cy = w / 2, h / 2
    n = int(w / 2 / (step_mm / scale)) + 1
    for i in range(-n, n + 1):
        x = int(cx + i * step_mm / scale)
        pygame.draw.line(surf, g, (x, 0), (x, h))
    n = int(h / 2 / (step_mm / scale)) + 1
    for i in range(-n, n + 1):
        y = int(cy + i * step_mm / scale)
        pygame.draw.line(surf, g, (0, y), (w, y))
    pygame.draw.line(surf, axis, (0, int(cy)), (w, int(cy)), 1)
    pygame.draw.line(surf, axis, (int(cx), 0), (int(cx), h), 1)
    for r_mm in (1000, 2000, 3000, 5000):
        r = int(r_mm / scale)
        if r < max(w, h):
            pygame.draw.circle(surf, (50, 50, 50), (int(cx), int(cy)), r, 1)


def draw_roi(surf: pygame.Surface, roi, w: int, h: int, scale: float) -> None:
    """Desenha o retângulo da ROI. Limites None viram a borda da tela."""
    x1 = roi.x_min if roi.x_min is not None else -w * scale
    x2 = roi.x_max if roi.x_max is not None else w * scale
    y1 = roi.y_min if roi.y_min is not None else -h * scale
    y2 = roi.y_max if roi.y_max is not None else h * scale
    sx1, sy1 = world_to_screen(x1, y2, w, h, scale)
    sx2, sy2 = world_to_screen(x2, y1, w, h, scale)
    rect = pygame.Rect(min(sx1, sx2), min(sy1, sy2),
                       abs(sx2 - sx1), abs(sy2 - sy1))
    pygame.draw.rect(surf, (200, 180, 40), rect, 2)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=None,
                    help="caminho do config.yaml (default: ./config.yaml)")
    ap.add_argument("--port", default=None,
                    help="sobrescreve sensor.port do YAML")
    args = ap.parse_args()

    cfg = cfg_mod.load(args.config)
    port_override = args.port or cfg.sensor.port
    rdr = LidarReader(port=port_override, baud=cfg.sensor.baud)
    if not rdr.start():
        print(f"[viz] não iniciou: {rdr.status}", file=sys.stderr)
        return 1

    pygame.init()
    pygame.display.set_caption("LidarMapper — Etapa 2 (filtragem + ROI)")
    w, h = cfg.viz.width, cfg.viz.height
    screen = pygame.display.set_mode((w, h))
    font = pygame.font.SysFont("consolas", 14)
    clock = pygame.time.Clock()

    scale = cfg.viz.scale_mm_per_px
    scale_default = scale
    show_outside = True
    trail = []
    last_print = 0
    viz_fps = 0

    running = True
    try:
        while running:
            for ev in pygame.event.get():
                if ev.type == pygame.QUIT:
                    running = False
                elif ev.type == pygame.KEYDOWN:
                    if ev.key in (pygame.K_ESCAPE, pygame.K_q):
                        running = False
                    elif ev.key in (pygame.K_PLUS, pygame.K_EQUALS,
                                    pygame.K_KP_PLUS):
                        scale = max(1, scale * 0.8)
                    elif ev.key in (pygame.K_MINUS, pygame.K_KP_MINUS):
                        scale = min(200, scale * 1.25)
                    elif ev.key == pygame.K_r:
                        scale = scale_default
                    elif ev.key == pygame.K_TAB:
                        show_outside = not show_outside

            measurements = rdr.drain()
            points = project_batch(measurements, cfg.processing)
            now = time.monotonic()
            for p in points:
                trail.append((p.x, p.y, now, p.quality))
            cutoff = now - cfg.viz.trail_window_s
            i = 0
            for i in range(len(trail)):
                if trail[i][2] >= cutoff:
                    break
            if i > 0:
                trail = trail[i:]

            # RECONSTRUÇÃO: import dentro do loop conforme o bytecode original
            from processing import Point2D
            pts_now = [Point2D(x=x, y=y, quality=q, angle=0, distance=0)
                       for (x, y, _t, q) in trail]
            inside, outside = split_by_roi(pts_now, cfg.roi)

            screen.fill((10, 10, 14))
            draw_grid(screen, w, h, scale)
            draw_roi(screen, cfg.roi, w, h, scale)

            if show_outside:
                col = (90, 90, 100)
                for p in outside:
                    sx, sy = world_to_screen(p.x, p.y, w, h, scale)
                    if 0 <= sx < w and 0 <= sy < h:
                        screen.set_at((sx, sy), col)

            for p in inside:
                sx, sy = world_to_screen(p.x, p.y, w, h, scale)
                if 0 <= sx < w and 0 <= sy < h:
                    pygame.draw.circle(screen, (60, 220, 90), (sx, sy), 2)

            sx, sy = world_to_screen(0, 0, w, h, scale)
            pygame.draw.circle(screen, (240, 80, 80), (sx, sy), 5)

            viz_fps = clock.get_fps()
            hud = [
                f"port={rdr.port}  status={rdr.status[:60]}",
                f"meas/s={rdr.meas_per_sec:7.0f}   scans/s={rdr.scans_per_sec:5.1f}"
                f"   viz_fps={viz_fps:4.1f}",
                f"trail={len(trail):5d}   in_roi={len(inside):4d}"
                f"   out={len(outside):4d}"
                f"   show_outside={'on' if show_outside else 'off'}",
                f"scale={scale:5.1f} mm/px"
                f"   trail_window={cfg.viz.trail_window_s:.2f}s",
                "keys: +/- zoom   R reset   TAB hide-outside   ESC/Q quit",
            ]
            for i, line in enumerate(hud):
                surf = font.render(line, True, (220, 220, 220))
                screen.blit(surf, (8, 8 + i * 16))

            pygame.display.flip()
            clock.tick(60)

            if now - last_print >= 2:
                print(f"[viz] meas/s={rdr.meas_per_sec:.0f}"
                      f"  scans/s={rdr.scans_per_sec:.1f}"
                      f"  trail={len(trail)}  in={len(inside)}"
                      f"  out={len(outside)}  viz_fps={viz_fps:.1f}")
                last_print = now
    except KeyboardInterrupt:
        pass
    finally:
        rdr.stop()
        pygame.quit()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
