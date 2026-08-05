"""
LidarMapper — Etapa 3: homografia 4 pontos + persistência.

A homografia H é uma matriz 3×3 que mapeia (x_lidar_mm, y_lidar_mm) para
(u, v) em coordenadas normalizadas da tela (0..1). 4 correspondências
determinam H exatamente (DLT por SVD, numpy puro — sem dependência de cv2).

Persistência:
  calibration.json
  {
    "version": 1,
    "screen_width_px": 1920,
    "screen_height_px": 1080,
    "corners_lidar_mm":  [[x_TL,y_TL], [x_TR,y_TR], [x_BR,y_BR], [x_BL,y_BL]],
    "corners_screen_norm": [[0,0], [1,0], [1,1], [0,1]],
    "H": [[...],[...],[...]]
  }
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass

import numpy as np

Point = tuple[float, float]

CORNER_NAMES = ("TOP-LEFT", "TOP-RIGHT", "BOTTOM-RIGHT", "BOTTOM-LEFT")
CORNERS_NORM: tuple[Point, Point, Point, Point] = ((0, 0), (1, 0), (1, 1), (0, 1))


def compute_homography(src: list[Point], dst: list[Point]) -> np.ndarray:
    """Homografia H (3×3) tal que dst ≈ H · src (DLT por SVD).

    Cada par (x,y) → (u,v) gera 2 linhas no sistema A·h = 0:
        [-x, -y, -1,  0,  0,  0,  ux, uy, u]
        [ 0,  0,  0, -x, -y, -1,  vx, vy, v]
    """
    if len(src) != 4 or len(dst) != 4:
        raise ValueError("são necessários exatamente 4 pontos em src e dst")
    rows = []
    for (x, y), (u, v) in zip(src, dst):
        rows.append([-x, -y, -1, 0, 0, 0, u * x, u * y, u])
        rows.append([0, 0, 0, -x, -y, -1, v * x, v * y, v])
    A = np.array(rows, dtype=float)
    _, _, vt = np.linalg.svd(A)
    H = vt[-1].reshape(3, 3)
    if abs(H[2, 2]) > 1e-12:
        H = H / H[2, 2]
    return H


def apply_h(H: np.ndarray, x: float, y: float) -> Point:
    """Aplica H a um ponto (x, y) → (u, v)."""
    v = H @ np.array([x, y, 1])
    w = v[2] if abs(v[2]) > 1e-12 else 1e-12
    return (float(v[0] / w), float(v[1] / w))


def apply_h_batch(H: np.ndarray, pts_xy: np.ndarray) -> np.ndarray:
    """Aplica H a N pontos (shape (N,2)) e devolve (N,2) projetado."""
    if pts_xy.size == 0:
        return pts_xy
    homog = np.column_stack([pts_xy, np.ones(len(pts_xy))])
    proj = homog @ H.T
    w = proj[:, 2].copy()
    w[np.abs(w) < 1e-12] = 1e-12
    return proj[:, :2] / w[:, None]


@dataclass
class Calibration:
    H: np.ndarray
    corners_lidar_mm: list[Point]
    corners_screen_norm: list[Point]
    screen_width_px: int
    screen_height_px: int
    version: int = 1


def save_calibration(path: str, calib: Calibration) -> None:
    data = {
        "version": calib.version,
        "screen_width_px": calib.screen_width_px,
        "screen_height_px": calib.screen_height_px,
        "corners_lidar_mm": [list(p) for p in calib.corners_lidar_mm],
        "corners_screen_norm": [list(p) for p in calib.corners_screen_norm],
        "H": calib.H.tolist(),
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def load_calibration(path: str) -> Calibration | None:
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return Calibration(
            H=np.array(data["H"], dtype=float),
            corners_lidar_mm=[tuple(p) for p in data["corners_lidar_mm"]],
            corners_screen_norm=[tuple(p) for p in data["corners_screen_norm"]],
            screen_width_px=int(data.get("screen_width_px", 1920)),
            screen_height_px=int(data.get("screen_height_px", 1080)),
            version=int(data.get("version", 1)),
        )
    except (ValueError, KeyError) as exc:
        print(f"[calib] arquivo inválido ({exc}).")
        return None


if __name__ == "__main__":
    src = [(0, 0), (1000, 0), (1000, 1000), (0, 1000)]
    dst = list(CORNERS_NORM)
    H = compute_homography(src, dst)
    for p, q in zip(src, dst):
        proj = apply_h(H, *p)
        err = ((proj[0] - q[0]) ** 2 + (proj[1] - q[1]) ** 2) ** 0.5
        print(f"{p} -> {proj} (alvo {q}) erro={err:.2e}")
    print("centro:", apply_h(H, 500, 500))
