# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

# LidarMapper Distribuído v3

Instalação interativa: 8 painéis LED, 8 nós Raspberry Pi (3B+/4/5) com
RPLIDAR S3, 2 servidores Windows com TouchDesigner + relay Python.

**Spec / fonte de verdade: `GUIA_LIDARMAPPER_DISTRIBUIDO_1.md`.** Leia
antes de qualquer implementação. Em conflito entre guia e código, o guia
vence. O §3 (protocolos) não muda sem atualizar o guia primeiro.

Os prompts de cada sessão de trabalho estão em `KICKOFF_CLAUDE_CODE.md`.

## Arquitetura (visão geral)

Fluxo de dados: **Pi (sender burro)** lê o S3, filtra, rastreia e envia
cursores em **mm** via UDP **V2** (`:5555`) → **`server_relay.py`
(middleware)** demultiplexa por `panel_id`, aplica homografia
(`calib_pN.json`), deriva eventos down/up → OSC `/touch/N` ao Max, e
reempacota **V1 (0..1)** via localhost (`:600N`) → **TouchDesigner**
consome V1 puro, sem matemática.

- Protocolo V2 (Pi → relay): header `"<BBIdH"`, ponto `"<Ifff"` (§3 do guia)
- Protocolo V1 (relay → TD): header `"<IdH"`, ponto `"<Ifff"` (§3.1)
- Calibração vive só no servidor; os Pis não conhecem homografia.

## Estrutura

- `shared/protocol.py` — pack/unpack dos protocolos V1 e V2. ÚNICO lugar
  do repo onde format strings de struct podem existir.
- `node/` — W1, roda nos Raspberry Pi (headless). PROIBIDO importar
  pygame, tkinter, customtkinter. Deps: rplidar-roboticia, pyserial,
  numpy, pyyaml, ruamel.yaml, stdlib.
- `server/` — W2, roda no Windows dos servidores. Pode usar pygame,
  customtkinter, python-osc.
- `docs/` — guias de instalação e configuração de campo (Pi, servidor,
  TouchDesigner). Derivados do guia; em conflito, o guia vence.
- `deploy/` — arquivos prontos para copiar na instalação: regra udev,
  unit systemd, snippet do chrony, `start_relay.bat`, `authorized_keys`
  da frota (+ `sync_authorized_keys.sh`) e o callback do TouchDesigner
  (`deploy/td/udp_callback_v1.py` — única exceção à regra do struct,
  porque roda dentro do TD e não importa o repo).
- `legacy/` — fonte do LidarMapper single-node, RECONSTRUÍDO do build
  PyInstaller na Sessão W0 (o repo original se perdeu). Após o W0:
  READ-ONLY — copiar de lá, nunca editar lá.
- `legacy_recovery/` — kit extraído do .exe (bytecode .pyc em Python
  3.13, disassembly pycdas em `dis/`, descompilação parcial, docs
  PLAN.md/UI.md/TOUCHDESIGNER.md, config.yaml e calibration.json reais).
  Fonte de verdade da Sessão W0; intocável depois.

## Ordem das sessões

1. **W0** — reconstruir os 15 módulos `.py` em `legacy/` a partir de
   `legacy_recovery/` (fidelidade > estilo; divergência = comentário
   `# RECONSTRUÇÃO:`). Obrigatória antes de tudo.
2. **W2** — `shared/protocol.py` + `server/` (relay, calibrador,
   simulador). 100% testável sem hardware via `server/test_node_sim.py`.
3. **W1** — `node/` (porte Pi + parsing serial vetorizado do §5.0).

## Regras

1. Cada sessão trabalha em UM workstream (node/ ou server/) e não edita
   a pasta do outro.
2. Todo teste que envolva bytes de rede importa `shared/protocol.py`.
3. Worst case de performance do node/: Raspberry Pi 3B+ (1 core fraco).
   Parsing serial vetorizado com numpy é requisito (§5.0 do guia).
4. Preferir copiar+adaptar de `legacy/` a reescrever.
5. Comentários e docstrings em pt-BR, nomes de código em inglês.

## Comandos

Python 3.13 preferido (o bytecode do legado é 3.13); mínimo 3.11.
Não há build system nem framework de testes — os testes são scripts
executáveis diretamente:

```bash
# Verificação de módulo reconstruído (Sessão W0)
python -m py_compile legacy/<modulo>.py

# Smoke test sem hardware (pipeline com medidas sintéticas)
python node/test_e2e.py

# Simulador de nós — gera tráfego V2 sintético, dev do server/ sem Pi
python server/test_node_sim.py

# Validar os dois lados do relay isoladamente
python server/test_udp_receiver.py --v2   # entrada dos Pis
python server/test_udp_receiver.py --v1   # saída pro TD

# Bench do parsing vetorizado (roda em qualquer máquina, gate do §10)
python node/bench_parse.py

# Relay do servidor — SEMPRE a partir da raiz do repo
python server/server_relay.py            # (--no-osc para dev sem Max)

# Calibração de um painel (grava server/calib_pN.json; hot-reload no relay)
python server/calibrate.py --panel 1 --target-source local
```

Alvos de execução: `node/` roda em Linux arm64 headless (mas deve rodar
no dev x86 também); `server/` tem Windows como alvo (paths, sockets,
sem systemd).

Instalação em campo: `docs/INSTALACAO.md` é o índice (Pi, servidor, TD).
