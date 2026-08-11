"""
LidarMapper — calibrador multi-painel (§7 do guia).

O coletor é um só: escuta a mesma porta V2 do relay (SO_REUSEADDR), filtra
por `--panel N`, coleta ~collect_s de pontos por canto (mediana em mm no
plano do sensor), roda DLT/SVD do server/homography.py, salva calib_pN.json
e reporta o erro de reprojeção em pixels.

Duas fontes de alvo (`--target-source`):

  local  — pygame fullscreen no display_index configurado no config_server.
           Portada da UX do legacy/calibrate.py: mesmos alvos, insets 0.06/0.94,
           ordem TL → TR → BR → BL. Operador toca o alvo aceso e aperta ESPAÇO.

  td     — stub OSC: envia `/calib/target <panel_id> <corner_idx> <u> <v>` ao
           TD (que desenha o alvo). O operador confirma no terminal com ENTER.
           A contraparte no TD fica fora do escopo desta sessão.

Rodar com o relay ativo: usa SO_REUSEADDR pra dividir a porta :5555.
Não sobrescreve o calib_pN.json em uso pelo relay: o hot-reload do relay
recarrega assim que a gravação termina.

Uso típico:
    python server/calibrate.py --panel 1 --target-source local
    python server/calibrate.py --panel 2 --target-source td
    python server/calibrate.py --panel 1 --target-source local --no-fullscreen
"""
from __future__ import annotations

import argparse
import logging
import math
import os
import socket
import sys
import time
from typing import Protocol

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.protocol import ProtocolError, unpack_v2  # noqa: E402
from server import config_server as cfg_mod  # noqa: E402
from server.homography import (  # noqa: E402
    Calibration, apply_h, compute_homography, save_calibration,
)

log = logging.getLogger("calibrate")

HERE = os.path.dirname(os.path.abspath(__file__))

CORNER_NAMES = ("TOP-LEFT", "TOP-RIGHT", "BOTTOM-RIGHT", "BOTTOM-LEFT")


def targets_norm(insets: tuple[float, float]) -> list[tuple[float, float]]:
    """4 alvos em coords normalizadas [0..1] com insets simétricos.
    Ordem: TL → TR → BR → BL (igual ao legacy)."""
    lo, hi = insets
    return [(lo, lo), (hi, lo), (hi, hi), (lo, hi)]


def collect_corner(sock: socket.socket, panel_id: int, collect_s: float,
                   min_pts: int) -> tuple[float | None, float | None, int]:
    """Escuta UDP por collect_s, mantém só V2 do painel `panel_id`, devolve
    (mediana_x_mm, mediana_y_mm, n_pontos) ou (None, None, n) se falhou.

    A mediana por eixo tolera ruído esporádico de outros clusters no fundo
    da cena (o operador vai estar tocando o alvo — cluster dominante).
    """
    xs: list[float] = []
    ys: list[float] = []
    t0 = time.monotonic()
    sock.settimeout(0.1)
    while time.monotonic() - t0 < collect_s:
        try:
            data, _ = sock.recvfrom(8192)
        except socket.timeout:
            continue
        try:
            frm = unpack_v2(data, strict_version=False)
        except ProtocolError:
            continue
        if frm.panel_id != panel_id:
            continue
        for p in frm.points:
            xs.append(p.x_mm)
            ys.append(p.y_mm)
    n = len(xs)
    if n < min_pts:
        return (None, None, n)
    return (float(np.median(xs)), float(np.median(ys)), n)


# Um quadrilátero menor que isto não descreve um painel — é ruído de coleta.
MIN_SIDE_MM = 150.0
MIN_AREA_MM2 = 50_000.0        # 0,05 m²


def degenerate_reason(corners_mm) -> str | None:
    """Devolve None se os 4 cantos formam um quadrilátero utilizável, ou a
    razão da recusa.

    Por que isto existe: com exatamente 4 correspondências a homografia passa
    pelos 4 pontos por construção, então `reprojection_error_px` dá ~0 MESMO
    num quadrilátero degenerado — ele não detecta o problema e ainda parece um
    resultado perfeito. O modo de falha real de campo é o operador coletar 4
    vezes o mesmo objeto (uma parede ao lado do sensor, ou o próprio corpo
    parado no campo): os cantos saem coincidentes ou colineares, a matriz fica
    singular, e o painel mapeia tudo errado sem nenhum aviso.
    """
    n = len(corners_mm)
    for i in range(n):
        d = math.dist(corners_mm[i], corners_mm[(i + 1) % n])
        if d < MIN_SIDE_MM:
            return (f"cantos {CORNER_NAMES[i]} e {CORNER_NAMES[(i + 1) % n]} "
                    f"estão a {d:.0f} mm (mínimo {MIN_SIDE_MM:.0f}) — "
                    f"provavelmente foi coletado o mesmo objeto duas vezes")
    area = abs(sum(corners_mm[i][0] * corners_mm[(i + 1) % n][1]
                   - corners_mm[(i + 1) % n][0] * corners_mm[i][1]
                   for i in range(n)) / 2.0)
    if area < MIN_AREA_MM2:
        return (f"área do quadrilátero é {area / 1e6:.4f} m² "
                f"(mínimo {MIN_AREA_MM2 / 1e6:.2f}) — os 4 cantos estão quase "
                f"colineares, a homografia seria singular")
    return None


def reprojection_error_px(H: np.ndarray, corners_mm, corners_norm,
                          screen_wh: tuple[int, int]) -> list[float]:
    """Erro por canto em pixels da tela (norma L2)."""
    w, h = screen_wh
    errs = []
    for (x, y), (u_t, v_t) in zip(corners_mm, corners_norm):
        u, v = apply_h(H, x, y)
        du = (u - u_t) * w
        dv = (v - v_t) * h
        errs.append((du * du + dv * dv) ** 0.5)
    return errs


# ---------------- fontes de alvo ----------------

class TargetSource(Protocol):
    def prompt(self, panel_id: int, corner_idx: int, corner_name: str,
               u: float, v: float) -> bool: ...
    def show_busy(self, msg: str) -> None: ...
    def show_result(self, saved: bool, errs_px: list[float] | None,
                    status: str) -> None: ...
    def close(self) -> None: ...


class TdOscSource:
    """Manda `/calib/target <panel_id> <corner_idx> <u> <v>` ao TD e
    aguarda o operador confirmar no terminal com ENTER."""

    def __init__(self, host: str, port: int):
        from pythonosc.udp_client import SimpleUDPClient
        self._client = SimpleUDPClient(host, port)
        self._host = host
        self._port = port
        print(f"[calib td] OSC -> {host}:{port}  (endpoint: /calib/target)")

    def prompt(self, panel_id, corner_idx, corner_name, u, v) -> bool:
        self._client.send_message("/calib/target",
                                  [int(panel_id), int(corner_idx),
                                   float(u), float(v)])
        try:
            ack = input(f"  canto {corner_idx + 1}/4 {corner_name} @ ({u:.2f},{v:.2f}) — "
                        f"toque no TD e ENTER (q = sair): ")
        except EOFError:
            return False
        return ack.strip().lower() != "q"

    def show_busy(self, msg: str) -> None:
        print(f"  ... {msg}")

    def show_result(self, saved, errs_px, status) -> None:
        self._client.send_message("/calib/target", [0, -1, 0.0, 0.0])
        print(f"[calib td] {status}")

    def close(self) -> None:
        pass


class LocalPygameSource:
    """Pygame fullscreen. Portada da UX do legacy/calibrate.py: alvos
    grandes (cruz + círculos), operador aperta ESPAÇO. Sem BackgroundSubtractor
    aqui: o Pi já publica tracks foreground pré-agrupados; a gente só
    coleta a mediana das medianas."""

    C_BG = (0, 25, 60)
    C_BG_BUSY = (0, 30, 70)
    C_WHITE = (240, 240, 240)
    C_YELLOW = (240, 200, 60)
    C_GREEN = (80, 220, 100)
    C_RED = (230, 80, 80)
    C_GREY = (110, 110, 120)
    C_DIM = (180, 180, 200)

    def __init__(self, display_index: int, width_px: int, height_px: int,
                 fullscreen: bool = True):
        import pygame  # noqa
        pygame.init()
        pygame.display.set_caption("LidarMapper — Calibração multi-painel")
        flags = pygame.SCALED | (pygame.FULLSCREEN if fullscreen else 0)
        try:
            self.screen = pygame.display.set_mode((width_px, height_px), flags,
                                                  display=display_index)
        except (TypeError, pygame.error):
            self.screen = pygame.display.set_mode((width_px, height_px), flags)
        self.w, self.h = width_px, height_px
        self.big = pygame.font.SysFont("consolas", 70, bold=True)
        self.mid = pygame.font.SysFont("consolas", 36)
        self.small = pygame.font.SysFont("consolas", 22)
        self._pygame = pygame

    def _draw_target(self, pos, active: bool, pulse: float) -> None:
        pg = self._pygame
        color = self.C_YELLOW if active else self.C_GREY
        r = 60 + (8 * int(np.sin(pulse * 4)) if active else 0)
        pg.draw.circle(self.screen, color, pos, r, 6)
        pg.draw.circle(self.screen, color, pos, 14)
        pg.draw.line(self.screen, color, (pos[0] - 90, pos[1]),
                     (pos[0] + 90, pos[1]), 3)
        pg.draw.line(self.screen, color, (pos[0], pos[1] - 90),
                     (pos[0], pos[1] + 90), 3)

    def _text_center(self, font, s, color, center) -> None:
        r = font.render(s, True, color)
        self.screen.blit(r, r.get_rect(center=center))

    def _target_positions(self, insets: tuple[float, float]) -> list[tuple[int, int]]:
        lo, hi = insets
        return [(int(self.w * lo), int(self.h * lo)),
                (int(self.w * hi), int(self.h * lo)),
                (int(self.w * hi), int(self.h * hi)),
                (int(self.w * lo), int(self.h * hi))]

    def prompt(self, panel_id, corner_idx, corner_name, u, v) -> bool:
        pg = self._pygame
        pos = (int(self.w * u), int(self.h * v))
        positions = self._target_positions((0.06, 0.94))
        clock = pg.time.Clock()
        t0 = time.monotonic()
        while True:
            for ev in pg.event.get():
                if ev.type == pg.QUIT:
                    return False
                if ev.type == pg.KEYDOWN:
                    if ev.key in (pg.K_ESCAPE, pg.K_q):
                        return False
                    if ev.key == pg.K_SPACE:
                        return True
            now = time.monotonic()
            pulse = (now - t0) * 2
            self.screen.fill(self.C_BG)
            for i, p in enumerate(positions):
                self._draw_target(p, active=i == corner_idx, pulse=pulse)
            self._text_center(self.big, f"PAINEL {panel_id} — CANTO {corner_idx + 1}/4",
                              self.C_WHITE, (self.w // 2, self.h // 2 - 60))
            self._text_center(self.mid, corner_name, self.C_YELLOW,
                              (self.w // 2, self.h // 2 - 10))
            self._text_center(self.mid, "toque o alvo e aperte ESPAÇO",
                              self.C_WHITE, (self.w // 2, self.h // 2 + 50))
            self._text_center(self.small, "ESC/Q = sair",
                              self.C_DIM, (self.w // 2, self.h - 30))
            pg.display.flip()
            clock.tick(60)

    def show_busy(self, msg: str) -> None:
        pg = self._pygame
        self.screen.fill(self.C_BG_BUSY)
        self._text_center(self.big, "CAPTURANDO...", self.C_WHITE,
                          (self.w // 2, self.h // 2))
        self._text_center(self.mid, msg, self.C_YELLOW,
                          (self.w // 2, self.h // 2 + 80))
        pg.display.flip()

    def show_result(self, saved: bool, errs_px, status: str) -> None:
        pg = self._pygame
        self.screen.fill(self.C_BG)
        color = self.C_GREEN if saved else self.C_RED
        self._text_center(self.big, "CALIBRADO ✓ SALVO" if saved else "FALHOU",
                          color, (self.w // 2, self.h // 2 - 80))
        self._text_center(self.mid, status, self.C_WHITE,
                          (self.w // 2, self.h // 2))
        if errs_px:
            e_s = "  ".join(f"{CORNER_NAMES[i][:2]}={e:.1f}px"
                            for i, e in enumerate(errs_px))
            self._text_center(self.small, f"erro de reprojeção: {e_s}",
                              self.C_DIM, (self.w // 2, self.h // 2 + 60))
        self._text_center(self.small, "qualquer tecla para sair",
                          self.C_DIM, (self.w // 2, self.h - 30))
        pg.display.flip()
        # aguarda tecla / clique
        done = False
        clock = pg.time.Clock()
        t0 = time.monotonic()
        while not done:
            for ev in pg.event.get():
                if ev.type in (pg.QUIT, pg.KEYDOWN, pg.MOUSEBUTTONDOWN):
                    done = True
            if time.monotonic() - t0 > 5:
                done = True
            clock.tick(30)

    def close(self) -> None:
        self._pygame.quit()


# ---------------- main ----------------

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=None)
    ap.add_argument("--panel", type=int, required=True,
                    help="panel_id do painel a calibrar")
    ap.add_argument("--target-source", choices=("local", "td"), default="local",
                    help="quem desenha os alvos (local=pygame, td=OSC stub)")
    ap.add_argument("--no-fullscreen", action="store_true",
                    help="(local) abre em janela em vez de fullscreen")
    ap.add_argument("--screen-size", default="1920x1080",
                    help="(local) tamanho da janela pygame. Ex.: 1920x1080")
    ap.add_argument("--out", default=None,
                    help="path do JSON de saída (default: do config)")
    ap.add_argument("--log-level", default="info")
    args = ap.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="[%(asctime)s] %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S")

    cfg = cfg_mod.load(args.config)
    panel = cfg.panels.get(args.panel)
    if panel is None:
        log.error("panel_id=%d não está no config_server (panels=%s)",
                  args.panel, sorted(cfg.panels.keys()))
        return 1

    out_path = args.out or (
        panel.calib_file if os.path.isabs(panel.calib_file)
        else os.path.join(HERE, panel.calib_file))

    # socket compartilhado com o relay (SO_REUSEADDR permite bind duplo em Linux;
    # em Windows a semântica é diferente — se der conflito, feche o relay).
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    if hasattr(socket, "SO_REUSEPORT"):
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        except OSError:
            pass
    sock.bind(("0.0.0.0", cfg.listen_port))

    # fonte de alvo
    if args.target_source == "local":
        w, h = (int(x) for x in args.screen_size.lower().split("x"))
        source: TargetSource = LocalPygameSource(
            display_index=panel.display_index, width_px=w, height_px=h,
            fullscreen=not args.no_fullscreen)
        screen_wh = (w, h)
    else:
        source = TdOscSource(cfg.osc.host, cfg.osc.port)
        # sem tela conhecida em modo TD; usa 1920x1080 como referência do erro
        screen_wh = (1920, 1080)

    corners_norm = targets_norm(cfg.calibration.target_insets)
    corners_mm: list[tuple[float, float]] = []
    rc = 0
    status_final = ""
    try:
        for i, (u, v) in enumerate(corners_norm):
            ok = source.prompt(args.panel, i, CORNER_NAMES[i], u, v)
            if not ok:
                status_final = "abortado pelo operador"
                log.warning(status_final)
                rc = 1
                break
            source.show_busy(f"{CORNER_NAMES[i]} — não se mexa")
            mx, my, n = collect_corner(sock, args.panel,
                                       cfg.calibration.collect_s,
                                       cfg.calibration.min_pts)
            if mx is None:
                status_final = (f"canto {CORNER_NAMES[i]} falhou "
                                f"({n} pts, min={cfg.calibration.min_pts})")
                log.error(status_final)
                rc = 1
                break
            log.info("[p%d %s] (%.1f, %.1f) mm  (%d pts)",
                     args.panel, CORNER_NAMES[i], mx, my, n)
            corners_mm.append((mx, my))
        else:
            # ANTES de calcular H: o erro de reprojeção não pega degeneração
            # (ver degenerate_reason). Recusar aqui é o que evita gravar uma
            # calibração inútil com cara de perfeita.
            motivo = degenerate_reason(corners_mm)
            if motivo is not None:
                status_final = f"calibração RECUSADA: {motivo}"
                log.error(status_final)
                log.error("cantos coletados (mm): %s",
                          [f"({x:.0f},{y:.0f})" for x, y in corners_mm])
                log.error("nada foi gravado — recolete com os alvos bem "
                          "separados e só o objeto-alvo no campo do LIDAR")
                source.show_result(False, None, status_final)
                return 1
            H = compute_homography(corners_mm, corners_norm)
            errs = reprojection_error_px(H, corners_mm, corners_norm, screen_wh)
            calib = Calibration(H=H, corners_lidar_mm=corners_mm,
                                corners_screen_norm=corners_norm,
                                screen_width_px=screen_wh[0],
                                screen_height_px=screen_wh[1])
            save_calibration(out_path, calib)
            status_final = (f"salvo em {os.path.basename(out_path)} — "
                            f"erros (px): {['%.1f' % e for e in errs]}")
            log.info(status_final)
            source.show_result(True, errs, status_final)
    except KeyboardInterrupt:
        status_final = "Ctrl+C — abortado"
        log.warning(status_final)
        rc = 1
    finally:
        if rc != 0 and status_final:
            try:
                source.show_result(False, None, status_final)
            except Exception:  # noqa
                pass
        source.close()
        sock.close()
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
