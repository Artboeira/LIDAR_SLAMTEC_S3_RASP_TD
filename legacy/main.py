"""
LidarMapper — entry point principal.

Pipeline completo: RPLIDAR S3 (thread separado) → filtro + ROI → background
subtraction → DBSCAN → tracker com IDs persistentes → UDP binário.

A leitura do LIDAR roda no thread interno de `LidarReader`. Processamento
e envio UDP rodam no thread principal — pra cada frame de tempo (limitado
por `udp.publish_rate_hz`), drena medidas acumuladas, atualiza tracker,
empacota com struct.pack e envia.

Uso típico:
    python main.py
    python main.py --port COM12
    python main.py --log-level debug

Encerra com Ctrl+C.
"""
from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import time

import numpy as np

import config as cfg_mod
import paths
from homography import load_calibration
from lidar_reader import LidarReader
from processing import BackgroundSubtractor, project_batch, split_by_roi
from publisher import RateLimiter, UdpPublisher
from tracker import Tracker

log = logging.getLogger("lidarmapper")


def configure_logging(level_name: str, fmt: str, override: str | None = None) -> None:
    """Configura o logger raiz com o nível e formato do config (ou CLI)."""
    name = (override or level_name or "info").upper()
    level = getattr(logging, name, logging.INFO)
    logging.basicConfig(level=level, format=fmt, datefmt="%H:%M:%S")
    logging.getLogger("rplidar").setLevel(logging.ERROR)


def main() -> int:
    ap = argparse.ArgumentParser(description="LidarMapper main pipeline")
    ap.add_argument("--config", default=None, help="caminho do config.yaml")
    ap.add_argument("--port", default=None, help="sobrescreve sensor.port (ex: COM12)")
    ap.add_argument("--calib", default="calibration.json",
                    help="arquivo de calibração (gerado por calibrate.py)")
    ap.add_argument("--baseline-s", type=float, default=2,
                    help="duração da captura de fundo na largada")
    ap.add_argument("--log-level", default=None,
                    help="debug|info|warning|error (override do config)")
    ap.add_argument("--no-publish", action="store_true",
                    help="roda o pipeline mas não envia UDP (debug)")
    args = ap.parse_args()

    cfg = cfg_mod.load(args.config)
    configure_logging(cfg.logging.level, cfg.logging.format, args.log_level)

    calib_path = args.calib if os.path.isabs(args.calib) else os.path.join(paths.APP_DIR, args.calib)
    calib = load_calibration(calib_path)
    if calib is None:
        log.error("sem calibração em %s — rode 'python calibrate.py'", calib_path)
        return 1
    log.info("calib: %s  tela %dx%d", os.path.basename(calib_path),
             calib.screen_width_px, calib.screen_height_px)

    port = args.port or cfg.sensor.port
    rdr = LidarReader(port=port, baud=cfg.sensor.baud)
    if not rdr.start():
        log.error("LIDAR não iniciou: %s", rdr.status)
        return 1
    log.info("LIDAR: %s", rdr.status)

    bg = BackgroundSubtractor(bins=720, margin_mm=120)
    bg.begin_baseline()
    bg.configure_time(args.baseline_s)
    log.info("baseline %.1fs — mantenha a área livre", args.baseline_s)

    trk = Tracker(cfg.tracker)

    pub = None
    if not args.no_publish:
        pub = UdpPublisher(host=cfg.udp.host, port=cfg.udp.port,
                           max_points=cfg.udp.max_points)
        log.info("UDP -> %s  rate=%g Hz  max_points=%d", pub.endpoint,
                 cfg.udp.publish_rate_hz, cfg.udp.max_points)

    limiter = RateLimiter(cfg.udp.publish_rate_hz)

    stop = {"flag": False}

    def _sig(*_):
        stop["flag"] = True

    signal.signal(signal.SIGINT, _sig)

    last_status_t = time.monotonic()
    last_frame_pub = 0
    rc = 0
    try:
        while not stop["flag"]:
            now = time.monotonic()
            measurements = rdr.drain()

            if not bg.ready:
                bg.feed(measurements)
                if bg.ready:
                    log.info("baseline pronto (%d/720 bins) — publicando.",
                             bg.learned_bins)
                if now - last_status_t >= 1:
                    log.debug("baseline... %d/720 bins  meas/s=%.0f",
                              bg.learned_bins, rdr.meas_per_sec)
                    last_status_t = now
                time.sleep(0.01)
                continue

            points = project_batch(measurements, cfg.processing)
            fg = bg.foreground_points(points)
            fg_in, _ = split_by_roi(fg, cfg.roi)

            if fg_in:
                xy = np.array([(p.x, p.y) for p in fg_in], dtype=float)
            else:
                xy = np.zeros((0, 2))
            tracks = trk.update(xy, calib.H)

            if pub is not None and limiter.ready(now):
                pub.publish(tracks)

            if now - last_status_t >= 1:
                pub_rate = pub.send_rate if pub else 0.0
                pub_frame = pub.frame if pub else 0
                delta = pub_frame - last_frame_pub
                last_frame_pub = pub_frame
                log.info("meas/s=%6.0f  scans/s=%4.1f  fg=%4d  tracks=%d  pub/s=%5.1f  +%4d frames  desync=%d  recon=%d",
                         rdr.meas_per_sec, rdr.scans_per_sec, len(fg_in),
                         len(tracks), pub_rate, delta, rdr.desyncs, rdr.reconnects)
                last_status_t = now

            time.sleep(0.005)
    except KeyboardInterrupt:
        pass
    except Exception:
        log.exception("erro fatal no loop principal")
        rc = 1
    finally:
        if pub is not None:
            pub.close()
        rdr.stop()
        log.info("encerrado.")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
