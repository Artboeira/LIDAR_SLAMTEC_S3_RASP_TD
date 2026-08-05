"""
LidarMapper node — entry point (§5.1 item 3 do guia).

Pipeline por frame:
  LidarReader.drain() → (N, 3) arrays de amostras válidas
  → project_and_filter (quality + dist + polar→xy, tudo vetorizado)
  → BackgroundSubtractor.foreground_mask
  → ROI (máscara numpy)
  → tracker.update (DBSCAN + NN com gating)
  → UdpPublisher V2 (via shared/protocol, com panel_id do config)

SEM homografia, SEM calibration.json — o relay do servidor aplica H.

Logs a cada 1 s:
  meas/s (throughput do sensor)
  scans/s (rotações/s)
  fg=N (pontos foreground dentro da ROI)
  tracks=K
  pub/s (Hz efetivo de envio)
  desync / recon
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
from lidar_reader import LidarReader
from processing import BackgroundSubtractor, in_roi_mask, project_and_filter
from publisher import RateLimiter, UdpPublisher
from tracker import Tracker

log = logging.getLogger("lidarmapper-node")


def configure_logging(level_name: str, fmt: str, override: str | None = None) -> None:
    name = (override or level_name or "info").upper()
    level = getattr(logging, name, logging.INFO)
    logging.basicConfig(level=level, format=fmt, datefmt="%H:%M:%S")
    logging.getLogger("rplidar").setLevel(logging.ERROR)


def main() -> int:
    ap = argparse.ArgumentParser(description="LidarMapper node (§5.1 do guia)")
    ap.add_argument("--config", default=None, help="path do config.yaml")
    ap.add_argument("--port", default=None,
                    help="sobrescreve sensor.port (ex.: /dev/rplidar)")
    ap.add_argument("--log-level", default=None,
                    help="debug|info|warning|error (override do config)")
    ap.add_argument("--no-publish", action="store_true",
                    help="roda o pipeline mas não envia UDP (debug)")
    args = ap.parse_args()

    cfg = cfg_mod.load(args.config)
    configure_logging(cfg.logging.level, cfg.logging.format, args.log_level)

    port = args.port or cfg.sensor.port
    rdr = LidarReader(port=port, baud=cfg.sensor.baud)
    if not rdr.start():
        log.error("LIDAR não iniciou: %s", rdr.status)
        return 1
    log.info("LIDAR: %s", rdr.status)

    bg = BackgroundSubtractor(bins=cfg.baseline.bins, margin_mm=cfg.baseline.margin_mm)
    bg.begin_baseline(cfg.baseline.duration_s)
    log.info("baseline %.1fs — mantenha a área livre", cfg.baseline.duration_s)

    trk = Tracker(cfg.tracker)

    pub: UdpPublisher | None = None
    if not args.no_publish:
        pub = UdpPublisher(host=cfg.udp.host, port=cfg.udp.port,
                           panel_id=cfg.udp.panel_id,
                           max_points=cfg.udp.max_points)
        log.info("UDP -> %s  rate=%g Hz  max_points=%d",
                 pub.endpoint, cfg.udp.publish_rate_hz, cfg.udp.max_points)
    else:
        log.info("--no-publish: pipeline sem envio UDP")

    limiter = RateLimiter(cfg.udp.publish_rate_hz)

    stop = {"flag": False}

    def _sig(*_):
        stop["flag"] = True

    signal.signal(signal.SIGINT, _sig)
    try:
        signal.signal(signal.SIGTERM, _sig)
    except (AttributeError, ValueError):
        pass

    last_status = time.monotonic()
    last_pub_frame = 0
    rc = 0
    try:
        while not stop["flag"]:
            now = time.monotonic()
            arr = rdr.drain()

            if not bg.ready:
                bg.feed(arr)
                if bg.ready:
                    log.info("baseline pronto (%d/%d bins) — publicando.",
                             bg.learned_bins, cfg.baseline.bins)
                if now - last_status >= 1.0:
                    log.debug("baseline... %d/%d bins  meas/s=%.0f",
                              bg.learned_bins, cfg.baseline.bins,
                              rdr.meas_per_sec)
                    last_status = now
                time.sleep(0.01)
                continue

            # aplica BG na "polar" (angle/dist) antes de projetar — mais barato
            fg_mask = bg.foreground_mask(arr)
            fg = arr[fg_mask]
            xy = project_and_filter(fg, cfg.processing)
            if len(xy):
                m = in_roi_mask(xy, cfg.roi)
                xy_in = xy[m]
            else:
                xy_in = xy
            tracks = trk.update(xy_in)

            if pub is not None and limiter.ready(now):
                pub.publish(tracks)

            if now - last_status >= 1.0:
                pub_rate = pub.send_rate if pub else 0.0
                pub_frame = pub.frame if pub else 0
                delta = pub_frame - last_pub_frame
                last_pub_frame = pub_frame
                log.info("meas/s=%6.0f  scans/s=%4.1f  fg=%4d  tracks=%d  "
                         "pub/s=%5.1f  +%4d frames  desync=%d  recon=%d",
                         rdr.meas_per_sec, rdr.scans_per_sec, len(xy_in),
                         len(tracks), pub_rate, delta, rdr.desyncs,
                         rdr.reconnects)
                last_status = now

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
