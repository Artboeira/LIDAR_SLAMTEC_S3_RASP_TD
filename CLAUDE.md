# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

# LidarMapper Distribuído v3

Instalação interativa: 8 painéis LED, 8 nós Raspberry Pi (3B+/4/5) com
RPLIDAR S3, 2 servidores Windows com TouchDesigner + relay Python.

**Spec / fonte de verdade: `GUIA_LIDARMAPPER_DISTRIBUIDO_1.md`.** Leia
antes de qualquer implementação. Em conflito entre guia e código (ou
qualquer doc), o guia vence. O §3 (protocolos) não muda sem atualizar o
guia primeiro.

`README.md` é a visão de produto; `KICKOFF_CLAUDE_CODE.md` é histórico de
processo (os prompts das sessões W0/W1/W2, já concluídas).

## Arquitetura (visão geral)

Fluxo de dados: **Pi (sender burro)** lê o S3, filtra, rastreia e envia
cursores em **mm** via UDP **V2** (`:5555`) → **`server_relay.py`
(middleware)** demultiplexa por `panel_id`, aplica homografia
(`calib_pN.json`), deriva eventos down → OSC `/touch/N` ao Max, e
reempacota **V1 (0..1)** via localhost (`:600N`) → **TouchDesigner**
consome V1 puro, sem matemática.

- Protocolo V2 (Pi → relay): header `"<BBIdH"`, ponto `"<Ifff"` (§3 do guia)
- Protocolo V1 (relay → TD): header `"<IdH"`, ponto `"<Ifff"` (§3.1)
- Calibração vive só no servidor; os Pis não conhecem homografia.
- V1 é byte-idêntico ao do LidarMapper single-node: o callback do TD que
  já estava validado continua valendo sem mudança.

### Invariantes do relay (leia antes de editar `server/server_relay.py`)

- Loop **single-threaded**, um socket de entrada com `settimeout(0.25)`.
  Não há locks em lugar nenhum — não introduza threads sem repensar isso.
- Calibração com **hot-reload por `mtime`**, checado a cada pacote.
  Arquivo sumido ou inválido = mantém a `H` antiga (nunca derruba o show).
- Painel sem `calib_pN.json` **descarta** os pacotes (status mostra `[-]`).
  Repo recém-clonado não tem nenhum: `calib_p*.json` é gerado por instalação.
- Clip para fora de `[0..1]` é a **segunda linha de defesa** contra um LIDAR
  enxergar o painel vizinho (§2) — a primeira é a ROI no Pi.
- `down` = id de track nova no frame → um OSC por evento. O `up` só expira
  estado interno (`osc.timeout_s`), não emite OSC.
- `panel_id` fora do `config_server.yaml` é ignorado com um warning único.
- Cap de **32 pontos** por datagrama nos dois protocolos.

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
  PyInstaller na Sessão W0 (o repo original se perdeu). READ-ONLY —
  copiar de lá, nunca editar lá.
- `legacy_recovery/` — kit extraído do .exe (bytecode .pyc em Python
  3.13, disassembly pycdas em `dis/`, descompilação parcial, docs
  PLAN.md/UI.md/TOUCHDESIGNER.md, config.yaml e calibration.json reais).
  Foi a fonte de verdade da Sessão W0; intocável depois.

### Imports e sys.path (assimetria proposital)

- `node/` é **flat, sem package**: os módulos importam irmãos direto
  (`from config import ...`) porque no Pi a pasta é implantada sozinha.
  Quem precisa do protocolo faz `sys.path.insert` da raiz do repo e então
  `from shared.protocol import ...`.
- `server/` é importado **como pacote** (`from server import
  config_server`), com `sys.path.insert` da raiz no topo dos entrypoints.
- Consequência: scripts que rodam `node/` em subprocesso precisam de
  `PYTHONPATH = <raiz> + <raiz>/node` (é o que os `w*_validate.py` fazem).
- Configs e calibrações resolvem paths **relativos ao próprio arquivo**
  (`node/config.yaml`, `server/config_server.yaml`, `calib_file` relativo
  a `server/`), não ao CWD. Ainda assim, rode tudo a partir da raiz.
- `node/config.py` exige `udp.panel_id` em 1..8 — sem default silencioso;
  é o único campo realmente distinto entre os 8 nós.

## Regras

1. Cada sessão trabalha em UM workstream (`node/` ou `server/`) e não
   edita a pasta do outro.
2. Todo teste que envolva bytes de rede importa `shared/protocol.py`.
3. Worst case de performance do `node/`: Raspberry Pi 3B+ (1 core fraco).
   Parsing serial vetorizado com numpy é requisito (§5.0 do guia) — nada
   de laço Python sobre as ~32k amostras/s do S3.
4. Preferir copiar+adaptar de `legacy/` a reescrever.
5. Comentários e docstrings em pt-BR, nomes de código em inglês.

## Comandos

Python 3.13 preferido (o bytecode do legado é 3.13); mínimo 3.11.
Não há build system nem framework de testes — os testes são scripts
executáveis diretamente. **Rode sempre a partir da raiz do repo.**

```bash
python -m venv .venv
.venv/bin/pip install -r server/requirements-server.txt   # dev completo
.venv/bin/pip install -r node/requirements-pi.txt         # só o nó (no Pi)
```

Suíte de validação — é o que aproxima um "test suite" aqui; rode antes de
declarar qualquer mudança pronta:

```bash
python w0_validate.py        # legacy reconstruído vs. os configs reais do kit
python w2_validate.py        # protocolo + servidor            (12/12)
python w1_validate.py        # nó Pi + cross-check W1↔W2       (21/21)
python shared/test_protocol.py   # round-trip e casos de borda V1/V2
```

Testes e ferramentas individuais:

```bash
# --- nó (node/) ---
python node/main.py                        # pipeline completo (precisa do S3)
python node/main.py --no-publish           # sem UDP, para ajustar ROI
python node/test_e2e.py --duration 1.5     # smoke sem hardware
python node/test_lidar.py --duration 30    # smoke do sensor (precisa do S3)
python node/bench_parse.py                 # gate de CPU do §10, qualquer máquina
python node/diag_bg.py                     # setores cegos + fantasmas; RODE ANTES DE CALIBRAR

# --- servidor (server/) ---
python server/server_relay.py --no-osc     # middleware (--no-osc = dev sem Max)
python server/calibrate.py --panel 1 --target-source local --no-fullscreen
python server/test_node_sim.py --panels 1,2,3,4 --pattern circle   # tráfego V2
python server/test_udp_receiver.py --v2 --port 5555   # entrada dos Pis
python server/test_udp_receiver.py --v1 --port 6001   # saída pro TD
python server/test_osc_receiver.py         # stub do Max: mostra os /touch/N
python server/make_test_calib.py --panel 1 # calib SINTÉTICA (destrava teste sem painel)
python server/bench_dhcp.py                # DHCP de bancada: Pi ligado direto no PC
```

Entrypoints aceitam `--config <path>` e `--log-level debug|info|warning|error`;
use isso (com um YAML em tmp) em vez de editar os configs versionados.

O relay loga status 1×/s: `p1[C] in=30 out=30 drop=0 down=2 age= 0.0s`.
`[C]` = calibrado e repassando; `[-]` = sem `calib_pN.json`.

Alvos de execução: `node/` roda em Linux arm64 headless (mas deve rodar
no dev x86 também); `server/` tem Windows como alvo (paths, sockets,
sem systemd).

## Estado atual

- `shared/protocol.py`, `node/`, `server/` (relay + calibrador): prontos e
  validados sem hardware.
- **Não implementado**: `server/ui.py` (painel de status — hoje o
  monitoramento é o log 1×/s do relay) e a contraparte TD do
  `calibrate.py --target-source td` (ver `docs/INSTALACAO_TOUCHDESIGNER.md` §7).
- **Validado no Windows (08/2026)**: `w2_validate.py` passa inteiro em
  Windows 11 + Python 3.14. Em 3.14 o `pygame` não tem wheel — o
  `requirements-server.txt` troca para `pygame-ce` por marcador de
  ambiente. No Windows o venv é `.venv\Scripts\`, não `.venv/bin/`.
- **Validado em hardware Pi (08/2026)**: `lidar-01` (Pi 4) rodou o pipeline
  completo com S3 real — 9,8 Hz, `desync=0 recon=0`, V2 chegando no relay.
  Gate de CPU no Pi 4: 2,3 % de um core (limite 30 %).
- **Não verificado**: gate de CPU num 3B+ real (os 3 da frota nunca foram
  medidos — é o risco aberto do §5 de `docs/PROVISIONAMENTO_FROTA.md`).
- **Frota (08/2026, em andamento)**: os 8 nós são provisionados por
  `deploy/provision_node.sh` (um por vez, idempotente); primeiro acesso via
  `deploy/bootstrap_keys.sh`; validação por `deploy/verify_node.sh`; update
  sem internet por `deploy/push_repo.sh`. O config que o serviço lê é
  `/home/pi/node-config.yaml` (fora do git, gerado por
  `deploy/render_node_config.py`) — `node/config.yaml` é o template.
  Plano e runbook: `docs/PROVISIONAMENTO_FROTA.md`.

Instalação em campo: `docs/INSTALACAO.md` é o índice (Pi, servidor, TD).
Roteiros de validação e o que eles **não** cobrem: `node/VALIDACAO.md`,
`server/VALIDACAO.md`.
