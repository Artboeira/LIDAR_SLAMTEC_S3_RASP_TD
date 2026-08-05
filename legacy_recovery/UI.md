# LidarMapper — Control Panel (ui.py)

Janela CustomTkinter que controla todo o pipeline sem precisar do terminal.

## Como rodar

```powershell
pip install -r requirements.txt
python ui.py
```

## Layout

```
+- Main controls ------------------------------------+
| [▶ Start UDP] [■ Stop] [🎯 Calibrate] [Clear log]  | <- pipeline + calibração
|                                       status: ...  |
+- Viz tools ----------------------------------------+
| Viz tools: [📷 Test Viz] [🎯 Test Tracker]         | <- ferramentas de debug
|            [📐 Test Calib]                         |
+- Status -------------------------------------------+
| ●  LIDAR      ...                                   | <- indicadores ao vivo
| ●  Baseline   ...                                   |   (verde/amarelo/cinza/vermelho)
| ●  Tracks     ...                                   |
| ●  UDP        ...                                   |
| ●  Calib      ...                                   |
+- Settings -----------------------------------------+
| [Sensor][Processing][ROI][Tracker][Screen][UDP]    | <- tabs com inputs
| (campos da tab selecionada)                        |
|         [Reload from file]   [💾 Save] [↻ Apply]  |
+- Log ----------------------------------------------+
| [HH:MM:SS] INFO ...                                |
+----------------------------------------------------+
```

## Botões principais

- **▶ Start UDP** — sobe `main.py` (pipeline LIDAR → UDP). Habilitado só quando
  nada mais está usando o sensor.
- **■ Stop** — encerra `main.py` graceful (CTRL_BREAK_EVENT → wait 3s →
  terminate → kill).
- **🎯 Calibrate** — abre `calibrate.py` em fullscreen no display configurado.
  Bloqueado enquanto o pipeline ou alguma viz tool está rodando.
- **Clear log** — limpa o painel de log.

## Viz tools

Cada uma é uma janela pygame separada, abre via subprocess. **Uso exclusivo
do LIDAR** — não rodam ao mesmo tempo que o pipeline nem entre si.

- **📷 Test Viz** — visualização 2D crua dos pontos do sensor + ROI
  (não precisa de calibração).
- **🎯 Test Tracker** — clustering DBSCAN + tracking com IDs persistentes,
  minimapa em coords 0..1 (requer `calibration.json`).
- **📐 Test Calib** — overlay que mostra os 4 cantos salvos e a projeção
  ao vivo (requer `calibration.json`).

## Settings

Editor visual do `config.yaml` em 6 tabs.

- **Sensor**: porta serial (vazio = autodetecta CP210x), baud.
- **Processing**: range válido (mm), qualidade mínima, ângulo offset, mirror.
- **ROI**: bounding box em mm; campos vazios = sem limite.
- **Tracker**: DBSCAN eps/min_samples, gating, timeout, max_tracks,
  confidence_frames, smoothing.
- **Screen**: **preset** (FullHD/1920x1200/QHD/4K UHD/Custom) que preenche
  width/height; display_index (0 = primário, 1 = secundário); fullscreen;
  botão `Update calibration.json metadata` (atualiza só `screen_width_px`/
  `screen_height_px` do JSON existente — a homografia opera em 0..1, então
  não exige recalibrar quando muda a resolução-alvo).
- **UDP**: host, port, publish_rate_hz, max_points.

Botões:

- **Reload from file** — descarta edições, recarrega do `config.yaml`.
- **💾 Save** — grava no `config.yaml` preservando comentários (ruamel.yaml).
- **↻ Apply (Save + Restart)** — grava e reinicia o pipeline (se rodando).

## Status (indicadores)

| Linha     | Verde                | Amarelo            | Vermelho           | Cinza |
|-----------|----------------------|--------------------|--------------------|-------|
| LIDAR     | conectado, meas/s    | conectando         | falhou             | idle  |
| Baseline  | pronto (N/720)       | capturando…        | —                  | —     |
| Tracks    | N active             | (azul: 0 cursores) | —                  | —     |
| UDP       | endpoint + fps atual | —                  | —                  | —     |
| Calib     | loaded (mtime)       | —                  | ausente            | —     |

## Lifecycle

- Fechar a janela mata o `main.py`, o calibrate e os tools de viz que
  estiverem ativos.
- Cada subprocess tem stdout capturado pro log viewer.
- Polling do `calibration.json` a cada 2s atualiza o indicador Calib mesmo
  sem o pipeline rodando.

## Atalhos pro terminal (sem precisar do front)

```powershell
python main.py            # pipeline UDP
python calibrate.py       # calibração interativa
python test_viz.py        # viz dos pontos
python test_tracker.py    # tracking ao vivo
python test_calib.py      # overlay de validação
python test_udp_receiver.py   # SUB de debug
python test_e2e.py        # E2E sintético (sem hardware)
```

Todos lêem o mesmo `config.yaml` e `calibration.json` — front e CLI ficam
intercambiáveis.
