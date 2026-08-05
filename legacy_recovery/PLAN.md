# LidarMapper — Plano

App Python que lê o **RPLIDAR S3** (SlamTec, USB serial @ 1 Mbps via CP210x) e envia via **UDP binário** (`struct.pack`) posições normalizadas (0..1) prontas pra consumir em TouchDesigner (UDP In DAT) e outros softwares criativos.

## Status das etapas

| # | Etapa | Status | Saída principal |
|---|---|---|---|
| 1 | Leitura do RPLIDAR S3 | ✅ concluído | `lidar_reader.py`, `test_lidar.py` |
| 2 | Filtragem + ROI + polar→cartesiano | ✅ concluído | `processing.py`, `config.py`, `test_viz.py` |
| 3 | Calibração 4 cantos (homografia) | ✅ concluído | `calibrate.py`, `homography.py`, `test_calib.py`, `calibration.json` |
| 4 | Clustering + tracking com IDs | ✅ concluído | `tracker.py`, `test_tracker.py` (DBSCAN numpy) |
| 5 | UDP binário (struct.pack) | ✅ concluído | `publisher.py`, `test_udp_receiver.py` |
| 6 | Integração final + threading + TD | ✅ concluído | `main.py`, `TOUCHDESIGNER.md`, `test_e2e.py` |

## Decisões técnicas

- **Lib do sensor:** `rplidar-roboticia` (importa como `from rplidar import RPLidar`). Usado e validado no `capivara_bra` com o mesmo S3 do usuário. `pyrplidar` foi sugerido no briefing mas não foi adotado — fica como nota.
- **Auto-detecta porta** pelo chip CP210x (VID `0x10C4` / PID `0xEA60`). Fallback: campo `port:` no `config.yaml`.
- **Baud S3:** 1 000 000.
- **Resiliência:** o thread de leitura **nunca morre**. Soft recover (`stop`+`clean_input`) nas primeiras desyncs; hard reconnect (recria `RPLidar`) se persistir. Logger do `rplidar` é silenciado pra `ERROR` (a 1 Mbps ele rouba throughput).
- **Saída UDP binária:** datagrama com `struct.pack` (LIDAR_MAPPER_V1).
  - Header (14 B): `uint32 frame`, `float64 timestamp`, `uint16 num_points`
  - Por ponto (16 B): `uint32 id`, `float32 x`, `float32 y`, `float32 confidence`
  - Sem dependência externa (socket + struct da stdlib). Pacote completo
    com 10 cursores = 174 bytes (sem fragmentação no MTU 1500).
  - Trocamos ZMQ por UDP em 2026-05-26 porque o TD não tem ZMQ DAT.

## Pastas / arquivos (estado final)

```
LidarMapper/
├── PLAN.md                  (este)
├── TOUCHDESIGNER.md         (setup do UDP In DAT no TD)
├── requirements.txt         (rplidar-roboticia, numpy, pygame, pyyaml — UDP é stdlib)
├── config.yaml              (config unificado: logging, sensor, processing, roi,
│                             viz, screen, tracker, udp)
├── config.py                (dataclasses tipadas + loader)
├── calibration.json         (gerado por calibrate.py)
│
├── lidar_reader.py          Etapa 1 — LidarReader (thread separado, resiliente)
├── processing.py            Etapa 2/4 — polar→cart, ROI, BackgroundSubtractor,
│                                       cluster_greedy, dbscan (numpy puro)
├── homography.py            Etapa 3 — DLT/SVD numpy, save/load calibration
├── tracker.py               Etapa 4 — Tracker com IDs persistentes + smoothing
├── publisher.py             Etapa 5 — UdpPublisher + RateLimiter + pack/unpack_frame
│
├── main.py                  entry point oficial (pipeline E2E completo)
├── calibrate.py             ferramenta de calibração interativa (fullscreen)
│
├── test_lidar.py            smoke do sensor (Etapa 1)
├── test_viz.py              viz pontos + ROI (Etapa 2)
├── test_calib.py            valida calibração salva (Etapa 3)
├── test_tracker.py          viz tracking IDs (Etapa 4)
├── test_udp_receiver.py     UDP receiver de validação (Etapa 5)
└── test_e2e.py              E2E sem hardware (Etapa 6) — pipeline real +
                             medidas sintéticas + 6 asserts
```

## Como usar (rápido)

```powershell
# 1) instalar deps (uma vez)
pip install -r requirements.txt

# 2) calibrar (uma vez por instalação)
python calibrate.py                # abre fullscreen no display 1

# 3) rodar o pipeline
python main.py                     # envia UDP -> 127.0.0.1:5555 (configurável)

# 4) validar (em outro terminal)
python test_udp_receiver.py        # resumo + latência (decode struct.unpack)
```

Setup do TouchDesigner: ver `TOUCHDESIGNER.md`.
