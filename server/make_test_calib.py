"""
LidarMapper — gera um `calib_pN.json` SINTÉTICO para teste.

⚠️ NÃO É CALIBRAÇÃO. Os cantos não são medidos com o LIDAR: são um retângulo
que você escolhe em milímetros no referencial do sensor. Serve para exercitar
homografia, clip, V1 e OSC quando ainda não dá para calibrar de verdade.
Ao montar o painel, apague o arquivo e rode `server/calibrate.py`.

Para que serve, concretamente:

- **Destravar o teste sem hardware.** O §3 do `docs/INSTALACAO_TOUCHDESIGNER.md`
  manda validar o TD com o `test_node_sim.py`, mas o relay **descarta** painel
  sem `calib_pN.json` — o smoke test não roda sem uma calibração existir. Este
  script fecha essa lacuna.
- **Separar problema de calibração de problema de cadeia.** Se o cursor não
  chega ao TD com uma calibração sintética conhecida, o defeito não está na
  calibração.

Uso (a partir da raiz do repo):

    .venv\\Scripts\\python server\\make_test_calib.py --panel 1
    .venv\\Scripts\\python server\\make_test_calib.py --panel 1 --x 200 800 --y 150 450

Os limites default cobrem uma área genérica à frente do sensor. Ajuste-os para
a região que o seu LIDAR realmente alcança — para descobrir qual é, mova um
objeto e observe as coordenadas com:

    .venv\\Scripts\\python server\\test_udp_receiver.py --v2 --port 5555 --raw

Usa o mesmo `compute_homography`/`save_calibration` do calibrador real, então o
arquivo é indistinguível de um legítimo para o relay — daí o aviso no topo.
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server import config_server as cfg_mod  # noqa: E402
from server.homography import (Calibration, apply_h,  # noqa: E402
                               compute_homography, save_calibration)

HERE = os.path.dirname(os.path.abspath(__file__))


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Gera um calib_pN.json sintético (NÃO é calibração real)")
    ap.add_argument("--config", default=None, help="path do config_server.yaml")
    ap.add_argument("--panel", type=int, required=True, help="panel_id (1..8)")
    ap.add_argument("--x", type=float, nargs=2, metavar=("X0", "X1"),
                    default=[200.0, 800.0],
                    help="faixa em x (mm) que vira 0..1 (default 200 800)")
    ap.add_argument("--y", type=float, nargs=2, metavar=("Y0", "Y1"),
                    default=[150.0, 450.0],
                    help="faixa em y (mm) que vira 0..1 (default 150 450)")
    ap.add_argument("--out", default=None,
                    help="path de saída (default: o do config)")
    args = ap.parse_args()

    cfg = cfg_mod.load(args.config)
    painel = cfg.panels.get(args.panel)
    if painel is None:
        print(f"panel_id={args.panel} não está no config_server "
              f"(panels={sorted(cfg.panels.keys())})", file=sys.stderr)
        return 1

    x0, x1 = args.x
    y0, y1 = args.y
    if x0 >= x1 or y0 >= y1:
        print("faixas inválidas: exige X0 < X1 e Y0 < Y1", file=sys.stderr)
        return 1

    destino = args.out or (
        painel.calib_file if os.path.isabs(painel.calib_file)
        else os.path.join(HERE, painel.calib_file))

    # ordem TL, TR, BR, BL — a mesma de calibrate.py
    cantos_mm = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    cantos_norm = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]

    H = compute_homography(cantos_mm, cantos_norm)
    save_calibration(destino, Calibration(
        H=H, corners_lidar_mm=cantos_mm, corners_screen_norm=cantos_norm,
        screen_width_px=1920, screen_height_px=1080))

    print("*" * 68)
    print("*  CALIBRAÇÃO SINTÉTICA — os cantos NÃO foram medidos com o LIDAR  *")
    print("*  Apague e rode server/calibrate.py ao montar o painel de fato.   *")
    print("*" * 68)
    print(f"\ngravado: {destino}")
    print(f"mapeia x {x0:.0f}..{x1:.0f} mm  e  y {y0:.0f}..{y1:.0f} mm  ->  0..1\n")

    print("verificação (mm -> normalizado):")
    casos = [
        ("centro       ", (x0 + x1) / 2, (y0 + y1) / 2),
        ("meio esquerda", x0 + (x1 - x0) * 0.25, (y0 + y1) / 2),
        ("meio direita ", x0 + (x1 - x0) * 0.75, (y0 + y1) / 2),
        ("fora, x baixo", x0 - (x1 - x0) * 0.5, (y0 + y1) / 2),
        ("fora, y alto ", (x0 + x1) / 2, y1 + (y1 - y0) * 0.5),
    ]
    for nome, x, y in casos:
        u, v = apply_h(H, x, y)
        dentro = ("dentro" if (0.0 <= u <= 1.0 and 0.0 <= v <= 1.0)
                  else "FORA -> o relay descarta")
        print(f"  {nome} ({x:7.0f},{y:7.0f}) mm -> ({u:6.3f}, {v:6.3f})  {dentro}")

    print(f"\nO relay recarrega sozinho por mtime — o painel {args.panel} deve "
          f"passar de [-] para [C] no status em até um pacote.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
