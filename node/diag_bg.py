"""
LidarMapper node — diagnóstico de baseline e de tracks fantasma.

Responde à pergunta que precede qualquer calibração: **com a área livre, o nó
está publicando alguma coisa?** Se estiver, a calibração vai colher esse
fantasma em vez do operador, sem dar erro nenhum — foi o que aconteceu na
bancada de 08/2026 e custou três calibrações degeneradas.

A causa mora em `processing.foreground_mask`: bin angular que não recebeu
nenhuma medida válida durante o baseline fica NaN, e NaN é tratado como
foreground INCONDICIONAL. O comportamento é proposital (bin sem retorno = espaço
vazio, logo o que aparecer ali é novo), mas um objeto estático num setor cego
vira track permanente.

Este script mede as duas coisas: quais setores ficaram cegos, e o que
efetivamente aparece como foreground dentro da ROI com a área desocupada.

Uso, a partir de ~/lidarmapper:

    .venv/bin/python -u node/diag_bg.py                    # 6s baseline, 8s observação
    .venv/bin/python -u node/diag_bg.py --baseline 10      # baseline mais longo
    .venv/bin/python -u node/diag_bg.py --config outro.yaml

Saída limpa = `ZERO pontos foreground na ROI`. Qualquer outra coisa precisa ser
resolvida antes de calibrar o painel.
"""
from __future__ import annotations

import argparse
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config as cfg_mod  # noqa: E402
from lidar_reader import LidarReader  # noqa: E402
from processing import (BackgroundSubtractor, dbscan_centroids,  # noqa: E402
                        in_roi_mask, project_and_filter)


def faixas_contiguas(indices: np.ndarray) -> list[tuple[int, int]]:
    """Agrupa índices de bin em faixas contíguas [(início, fim), ...]."""
    if len(indices) == 0:
        return []
    faixas = []
    ini = ant = int(indices[0])
    for i in indices[1:]:
        i = int(i)
        if i == ant + 1:
            ant = i
            continue
        faixas.append((ini, ant))
        ini = ant = i
    faixas.append((ini, ant))
    return faixas


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Diagnóstico de baseline e tracks fantasma do nó")
    ap.add_argument("--config", default=None, help="path do config.yaml")
    ap.add_argument("--baseline", type=float, default=6.0,
                    help="duração do baseline em s (default 6.0)")
    ap.add_argument("--observe", type=float, default=8.0,
                    help="duração da observação com a área livre (default 8.0)")
    ap.add_argument("--port", default=None, help="sobrescreve sensor.port")
    args = ap.parse_args()

    cfg = cfg_mod.load(args.config)
    rdr = LidarReader(port=args.port or cfg.sensor.port, baud=cfg.sensor.baud)
    if not rdr.start():
        print(f"LIDAR não iniciou: {rdr.status}", file=sys.stderr)
        return 1
    print(f"LIDAR: {rdr.status}")

    bg = BackgroundSubtractor(bins=cfg.baseline.bins,
                              margin_mm=cfg.baseline.margin_mm)
    print(f"\n== baseline de {args.baseline:.0f}s — MANTENHA A ÁREA LIVRE ==")
    bg.begin_baseline(args.baseline)
    while not bg.ready:
        bg.feed(rdr.drain())
        time.sleep(0.01)

    total = cfg.baseline.bins
    cegos = bg.blind_bins
    print(f"bins aprendidos: {bg.learned_bins}/{total} "
          f"({100.0 * bg.learned_bins / total:.1f}%) — "
          f"{len(cegos)} cegos = {360.0 * len(cegos) / total:.1f} graus")

    if len(cegos):
        gpb = 360.0 / total
        print("\nSETORES CEGOS (o que estiver neles vira foreground permanente):")
        for a, b in faixas_contiguas(cegos):
            print(f"   {a * gpb:6.1f}° .. {(b + 1) * gpb:6.1f}°   ({b - a + 1} bins)")
        print("  → setores dentro da ROI importam; fora dela são inofensivos.")

    print(f"\n== observando {args.observe:.0f}s com a área livre ==")
    t0 = time.monotonic()
    partes = []
    while time.monotonic() - t0 < args.observe:
        arr = rdr.drain()
        if arr.size:
            fg = arr[bg.foreground_mask(arr)]
            xy = project_and_filter(fg, cfg.processing)
            if len(xy):
                partes.append(xy[in_roi_mask(xy, cfg.roi)])
        time.sleep(0.01)
    rdr.stop()

    partes = [p for p in partes if len(p)]
    if not partes:
        print("\nRESULTADO: ZERO pontos foreground na ROI com a área livre.")
        print("O nó está limpo — pode calibrar.")
        return 0

    pts = np.vstack(partes)
    print(f"\nRESULTADO: {len(pts)} pontos foreground na ROI com a área LIVRE.")
    print("Isso é fantasma. NÃO calibre antes de resolver — a mediana do")
    print("collect_corner() vai colher isto em vez do alvo.\n")
    print(f"  extensão x: {pts[:, 0].min():8.1f} .. {pts[:, 0].max():8.1f} mm")
    print(f"  extensão y: {pts[:, 1].min():8.1f} .. {pts[:, 1].max():8.1f} mm")

    cent = dbscan_centroids(pts, cfg.tracker.dbscan_eps_mm,
                            cfg.tracker.dbscan_min_samples)
    if cent:
        print("\n  clusters (os que viram track):")
        for (cx, cy), n in cent[:8]:
            ang = float(np.degrees(np.arctan2(cy, cx)) % 360.0)
            print(f"    ({cx:8.1f}, {cy:8.1f}) mm   n={n:5d}   ângulo={ang:6.1f}°")
        print("\n  Um eixo constante (x fixo, y variando) = superfície plana:")
        print("  parede, quina de mesa, estrutura.")

    print("\n  O que fazer, na ordem:")
    print("   1. aumentar baseline.duration_s (2.0 costuma deixar setores cegos)")
    print("   2. apertar a ROI para excluir a região do fantasma")
    print("   3. conferir se o sensor foi movido depois do último baseline")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
