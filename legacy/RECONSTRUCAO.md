# Sessão W0 — Relatório da reconstrução do LidarMapper single-node

Fonte: `legacy_recovery/` (bytecode .pyc Python 3.13 + disassembly pycdas +
descompilação parcial + docs). Método: reconstrução módulo a módulo a partir
do disassembly completo, preservando nomes, assinaturas, defaults, constantes,
docstrings e lógica. Fidelidade > estilo.

## Ambiente

- **Python 3.13 indisponível na máquina de reconstrução** — verificação feita
  com Python **3.14.4** (py_compile + import + execução). O pycdas foi a fonte
  primária; o fallback `dis.dis` sobre os .pyc (que exigiria 3.13) não foi
  necessário em nenhum módulo.
- Validação global: `../w0_validate.py` (roda com `.venv/bin/python w0_validate.py`).

## Status por módulo

| Módulo | Fidelidade | Verificação |
|---|---|---|
| paths.py | **INFERIDO** (ausente do kit) | import ok; comportamento confirmado por README_DIST |
| config.py | máxima | carrega o config.yaml REAL do kit; todos os campos batem |
| homography.py | máxima | calibration.json REAL round-tripa sem perda; H reproduzida (max\|dH\|=1.65e-13) |
| processing.py | máxima | import ok; exercitado pelo test_e2e |
| tracker.py | máxima | import ok; exercitado pelo test_e2e |
| publisher.py | máxima | pack_frame V1 **byte-idêntico** ao TOUCHDESIGNER.md (46 B com 2 pts) |
| lidar_reader.py | máxima | import ok; timing real só com sensor (ver checklist) |
| main.py | máxima | import ok; pipeline completo só com sensor |
| calibrate.py | máxima | import ok; fluxo visual só com tela+sensor |
| test_udp_receiver.py | máxima | import ok |
| test_e2e.py | máxima | **roda e passa sem hardware** (7/7 asserts, 146 pacotes) |
| test_lidar.py | máxima | import ok; critérios são humanos+sensor |
| test_viz.py | best effort | import ok |
| test_tracker.py | best effort | import ok |
| test_calib.py | best effort | import ok |
| ui.py | best effort | import ok (customtkinter) |

## Divergências anotadas (`# RECONSTRUÇÃO:` no código)

1. **paths.py — módulo inteiro inferido.** Não estava no kit (ficou fora do
   PYZ do build). Atributos usados pelo resto do código: `APP_DIR`,
   `CONFIG_PATH`, `CALIB_PATH`. Reconstruído pelo padrão canônico PyInstaller
   (`sys.frozen`), confirmado pelo README_DIST ("arquivos editáveis ao lado
   do exe"). Decisão validada com o operador.
2. **Literais `0` vs `0.0` / `1` vs `1.0`.** O pycdas imprime iguais; onde o
   pool de constantes tinha entradas duplicadas (tipos distintos), o tipo foi
   inferido pela annotation/uso (ex.: `Track.u = 0.0`, `RateLimiter.period`,
   `ui.empty_state()`). Sem efeito de comportamento.
3. **`_, _, vt = np.linalg.svd(A)`** (homography) e **`for _, _, t in buf`**
   (calibrate.count_recent_fg): o otimizador do 3.13 elimina o store morto do
   primeiro `_`; forma com `_` duplicado é a leitura consistente com os locals.
4. **test_viz**: `from processing import Point2D` acontece DENTRO do loop de
   render (preservado); poda do trail tem quirk que preserva o último ponto
   vencido (comportamento original mantido).
5. **test_calib**: `baseline_t0` atribuída e nunca lida; `np` e `split_by_roi`
   importados sem uso — preservados.
6. **ui.py**: `_choice` e `_read_widget` são código morto no original —
   preservados.
7. Quirks originais preservados sem anotação: `BackgroundSubtractor.__init__`
   aceita `baseline_time_s` e ignora; `calibrate.main` cria `markers = []`
   sem uso; `tracker` importa `field`/`Iterable` sem uso.
8. Alvos de salto do pycdas têm off-by-2 sistemático — resolvidos pelo fluxo
   lógico + exception tables em todos os módulos.

## Só verificável com o sensor físico (checklist de bancada)

- [ ] `test_lidar.py` com S3 na USB: meas/s nominal (milhares), scans/s 8–15 Hz,
      `desyncs`/`reconnects` estáveis por 30 s+
- [ ] `lidar_reader.py`: timing real de `_connect_clean` (reset+sleep 2 s),
      soft recover e hard reconnect com desconexão física do USB
- [ ] `calibrate.py`: fluxo BASELINE→CAPTURE→DONE com tela no display_index
      e toques reais; erro de reprojeção aceitável
- [ ] `main.py`: pipeline completo publicando V1 pro TD (callback do
      TOUCHDESIGNER.md) com calibration.json gerado em bancada
- [ ] `ui.py`: control panel no Windows lançando main/calibrate/testes como
      subprocessos (CREATE_NEW_PROCESS_GROUP / CTRL_BREAK)
