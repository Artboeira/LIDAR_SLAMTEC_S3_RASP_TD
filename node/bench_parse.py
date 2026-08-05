"""
LidarMapper node — benchmark do parsing+filtros (§5.0 e gate do §10).

Roda em QUALQUER máquina (x86 dev, Pi 5, 3B+): mede amostras/s e o custo
em % de um core do pipeline vetorizado, usando dados sintéticos que
imitam a saída do RPLIDAR S3 em Legacy Scan Mode.

Meta do §10 para o hot path completo no 3B+:
  parsing + project_and_filter + BG mask + ROI ≤ 30% de um core

Este bench mede o pipeline INTEIRO por batch: parse → project → BG mask
→ ROI. É a métrica que importa para o gate.

Uso:
    python node/bench_parse.py                 # 3 s de bench, 30 kHz sintético
    python node/bench_parse.py --hz 40000      # simular S3 no pico
    python node/bench_parse.py --chunk 4096    # bloco de leitura serial

Como comparar com o §10:
  - No Pi 5 (single-core ~4-5x mais rápido que 3B+):
      < 12% cpu  → quase certo que cabe no 3B+ (segue)
      12-20%     → zona cinzenta, testar em 3B+ real
      > 20%      → otimizar antes de escalar
  - No 3B+ real: critério direto (<= 30%).
"""
from __future__ import annotations

import argparse
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import ProcessingCfg, ROICfg  # noqa: E402
from lidar_reader import _parse_legacy_scan  # noqa: E402
from processing import BackgroundSubtractor, in_roi_mask, project_and_filter  # noqa: E402


def _encode_sample(angle_deg: float, dist_mm: float, quality: int,
                   new_scan: bool) -> bytes:
    """Codifica UM sample no formato Legacy Scan do RPLIDAR (5 bytes)."""
    aq = int(angle_deg * 64) & 0x7FFF
    dq = int(dist_mm * 4) & 0xFFFF
    s = 1 if new_scan else 0
    b0 = ((quality & 0x3F) << 2) | (s & 1) | ((1 - s) << 1)
    b1 = ((aq & 0x7F) << 1) | 1
    b2 = (aq >> 7) & 0xFF
    b3 = dq & 0xFF
    b4 = (dq >> 8) & 0xFF
    return bytes([b0, b1, b2, b3, b4])


def _make_synthetic_buf(n_samples: int, scan_stride: int = 3000,
                        rng: np.random.Generator | None = None) -> bytes:
    """Blob binário com `n_samples` amostras válidas. Ângulos varrem
    0..360, distâncias entre 500 e 5000 mm com um pouco de ruído,
    qualidade fixa em 30. `scan_stride` marca 1 new_scan a cada N."""
    if rng is None:
        rng = np.random.default_rng(0)
    parts = []
    for i in range(n_samples):
        angle = (i * 360.0 / scan_stride) % 360.0
        dist = 500.0 + (i * 4.5) % 4500.0
        # jitter para exercitar máscaras (não faz o parser mudar)
        dist += float(rng.normal(0, 5))
        parts.append(_encode_sample(angle, dist, 30, i % scan_stride == 0))
    return b"".join(parts)


def _run_one_batch(buf: bytes, proc_cfg: ProcessingCfg, roi_cfg: ROICfg,
                   bg: BackgroundSubtractor) -> int:
    """Executa o hot path inteiro num chunk. Retorna qtde de pontos
    foreground dentro da ROI (só para o compilador não otimizar)."""
    angle, dist, quality, _ns, valid = _parse_legacy_scan(buf)
    keep = valid & (dist > 0)
    arr = np.column_stack([angle[keep], dist[keep], quality[keep]])
    if bg.ready:
        fg = arr[bg.foreground_mask(arr)]
    else:
        bg.feed(arr)
        fg = arr
    xy = project_and_filter(fg, proc_cfg)
    m = in_roi_mask(xy, roi_cfg)
    return int(m.sum())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hz", type=int, default=30000,
                    help="throughput sintético de amostras/s. Default: 30000")
    ap.add_argument("--duration", type=float, default=3.0,
                    help="duração do bench em segundos. Default: 3")
    ap.add_argument("--chunk-bytes", type=int, default=4096,
                    help="tamanho do bloco de leitura simulado. Default: 4096")
    ap.add_argument("--warmup-s", type=float, default=0.3,
                    help="tempo de warmup (dropa do total). Default: 0.3")
    args = ap.parse_args()

    proc_cfg = ProcessingCfg()
    roi_cfg = ROICfg(x_min=-2000, x_max=2000, y_min=-1000, y_max=3000)
    bg = BackgroundSubtractor()
    # pula direto pra "ready" fingindo baseline (feed direto no _bg)
    # — não queremos medir o feed, só o foreground_mask que é o hot path
    bg._samples = None
    bg.ready = True
    # bg vazio significa que TODAS as amostras passam (bins não aprendidos)
    # → simula pior caso de foreground=100% (defasável na prod)

    samples_per_chunk = args.chunk_bytes // 5
    chunks_per_sec = args.hz / max(samples_per_chunk, 1)
    print(f"[bench] simulando {args.hz} amostras/s "
          f"({samples_per_chunk} samples/chunk × {chunks_per_sec:.1f} chunks/s)")

    # pré-gera um pool de blobs distintos pra evitar cache-hit trivial
    rng = np.random.default_rng(42)
    pool = [_make_synthetic_buf(samples_per_chunk, rng=rng) for _ in range(64)]

    # warmup
    t_end = time.monotonic() + args.warmup_s
    i = 0
    while time.monotonic() < t_end:
        _run_one_batch(pool[i % len(pool)], proc_cfg, roi_cfg, bg)
        i += 1

    # bench real
    n_chunks = 0
    n_samples = 0
    fg_total = 0
    t_start = time.monotonic()
    cpu_start = time.process_time()
    t_end = t_start + args.duration
    i = 0
    while time.monotonic() < t_end:
        buf = pool[i % len(pool)]
        fg_total += _run_one_batch(buf, proc_cfg, roi_cfg, bg)
        n_chunks += 1
        n_samples += samples_per_chunk
        i += 1
    wall = time.monotonic() - t_start
    cpu = time.process_time() - cpu_start

    achieved = n_samples / wall
    # % de um core: fração do wall que foi CPU (tudo síncrono)
    core_pct = 100.0 * cpu / wall
    # extrapola para o requisito de 30k/s
    scale_to_30k = 30000.0 / achieved if achieved > 0 else float("inf")
    projected = core_pct * scale_to_30k

    print(f"[bench] wall={wall:.2f}s  cpu={cpu:.2f}s")
    print(f"[bench] chunks={n_chunks}  samples={n_samples}  fg_hits={fg_total}")
    print(f"[bench] throughput={achieved:,.0f} samples/s (loop-limited)")
    print(f"[bench] custo em %core: {core_pct:.1f}%  "
          f"(equivalente a {projected:.1f}% p/ 30k amostras/s)")

    # regra do §10:
    if projected <= 30.0:
        veredicto = ("OK: <=30% de um core p/ 30k amostras/s. "
                     "Se a máquina alvo é o 3B+, provavelmente cabe.")
    elif projected <= 60.0:
        veredicto = "ZONA CINZENTA: caberia no 3B+ apenas com margem apertada."
    else:
        veredicto = "FALHA: acima de 60% projetado. Precisa otimização (§5.0)."
    print(f"[bench] {veredicto}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
