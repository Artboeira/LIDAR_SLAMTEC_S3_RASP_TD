# LidarMapper Distribuído v3

Rastreamento de toque em painéis LED de grande formato por LIDAR, distribuído
em 8 painéis. Cada painel tem um RPLIDAR S3 ligado a um Raspberry Pi que
detecta e rastreia pessoas/mãos no plano do painel; dois servidores Windows
convertem essas posições em coordenadas de tela e entregam ao TouchDesigner
(visual) e ao Max/MSP (som).

Instalação interativa — o sistema roda por horas seguidas, sem operador, e
precisa voltar sozinho de uma queda de energia.

---

## Como funciona

```
   RPLIDAR S3         Raspberry Pi              Servidor Windows            Painel LED
   (varre o     USB   lidar-0N                  server-a / server-b
    plano do    --->  node/main.py                                              ^
    painel)           filtra, subtrai fundo,                                    | vídeo
                      agrupa, rastreia                                          |
                             |                                            +-----------+
                             |  UDP :5555  V2 (milímetros + panel_id)     |    TD     |
                             v                                            +-----------+
                      server/server_relay.py                                    ^
                      demux, homografia, clip,                                  |
                      detecção de toque                                    UDP :600N
                             |                                             V1 (0..1)
                             +--- OSC /touch/N :7500 ---> Max/MSP               |
                             +--------------------------------------------------+
```

Três decisões definem a arquitetura:

1. **O Pi é um sender burro.** Publica coordenadas em **milímetros no
   referencial do sensor**. Não conhece calibração, não sabe onde fica o painel.
2. **A calibração vive só no servidor.** Um `calib_pN.json` por painel, com
   hot-reload por `mtime` — recalibrar não derruba nada.
3. **O TouchDesigner só consome.** Recebe `0..1` e preenche uma tabela. Sem
   homografia, sem demux, sem matemática.

O pior caso de performance é o **Raspberry Pi 3B+**: é por isso que o parsing
serial do S3 (~32k amostras/s) é vetorizado com numpy, e não um laço Python.

---

## Estrutura

| Pasta | Alvo | Conteúdo |
|---|---|---|
| [shared/](shared/) | ambos | `protocol.py` — pack/unpack V1 e V2. **Único lugar do repo com format strings de `struct`** |
| [node/](node/) | Raspberry Pi, Linux arm64 headless | leitura serial vetorizada, filtros, tracker, publisher V2 |
| [server/](server/) | Windows | relay (middleware), calibrador multi-painel, homografia, simuladores |
| [docs/](docs/) | — | guias de instalação e configuração de campo |
| [deploy/](deploy/) | — | arquivos prontos para copiar: udev, systemd, chrony, `.bat`, callback do TD |
| [legacy/](legacy/) | bancada | LidarMapper single-node, reconstruído do build PyInstaller. READ-ONLY |
| [legacy_recovery/](legacy_recovery/) | — | kit extraído do `.exe` original (bytecode, disassembly, docs, configs reais). Intocável |

---

## Início rápido (sem hardware)

O sistema inteiro é exercitável sem nenhum LIDAR e sem nenhum Pi, via
simulador de nós. Três terminais, a partir da raiz do repo:

```bash
python3 -m venv .venv
.venv/bin/pip install -r server/requirements-server.txt
```

```bash
# Terminal 1 — 4 nós sintéticos publicando V2 a 30 Hz
.venv/bin/python server/test_node_sim.py --panels 1,2,3,4 --pattern circle

# Terminal 2 — o middleware
.venv/bin/python server/server_relay.py --no-osc

# Terminal 3 — o que o TouchDesigner receberia
.venv/bin/python server/test_udp_receiver.py --v1 --port 6001
```

O relay reporta o estado 1×/s:

```
p1[C] in=30 out=30 drop=0 down=2 age= 0.0s   p2[-] in=30 out=0 drop=0 down=0 age= 0.0s
```

`[C]` = painel calibrado (repassando ao TD); `[-]` = sem `calib_pN.json`, não
repassa nada — comportamento correto num repo recém-clonado. Para gerar uma
calibração e ver o fluxo completo, rode
`.venv/bin/python server/calibrate.py --panel 1 --target-source local --no-fullscreen`.

Validação automatizada das três sessões de trabalho:

```bash
.venv/bin/python w0_validate.py    # legacy reconstruído
.venv/bin/python w2_validate.py    # protocolo + servidor      (12/12)
.venv/bin/python w1_validate.py    # nó Pi + cross-check W1↔W2 (21/21)
```

---

## Comandos

Python 3.13 preferido (o bytecode do legado é 3.13); mínimo 3.11. Não há build
system nem framework de testes — os testes são scripts executáveis direto.

```bash
# --- nó (node/) ---
python node/main.py                        # pipeline completo (precisa do S3)
python node/main.py --no-publish            # sem enviar UDP, para ajustar ROI
python node/test_e2e.py                     # smoke sem hardware
python node/test_lidar.py --duration 30     # smoke do sensor
python node/bench_parse.py                  # gate de CPU do §10, roda em qualquer máquina

# --- servidor (server/) — SEMPRE a partir da raiz do repo ---
python server/server_relay.py               # middleware (--no-osc para dev sem Max)
python server/calibrate.py --panel 1 --target-source local
python server/test_node_sim.py              # tráfego V2 sintético
python server/test_udp_receiver.py --v2 --port 5555    # entrada dos Pis
python server/test_udp_receiver.py --v1 --port 6001    # saída pro TD
```

---

## Documentação

| Documento | O que é |
|---|---|
| [GUIA_LIDARMAPPER_DISTRIBUIDO_1.md](GUIA_LIDARMAPPER_DISTRIBUIDO_1.md) | **A spec — fonte de verdade.** Topologia, protocolos, rede, setup, calibração, gates de performance |
| [docs/INSTALACAO.md](docs/INSTALACAO.md) | Índice da instalação: rede, ordem das etapas, checklist de bring-up por painel |
| [docs/INSTALACAO_PI.md](docs/INSTALACAO_PI.md) | Nó Raspberry Pi: imagem, udev, chrony, `config.yaml`, systemd, golden image |
| [docs/INSTALACAO_SERVIDOR.md](docs/INSTALACAO_SERVIDOR.md) | Servidor Windows: Python, firewall, `config_server.yaml`, relay, calibração |
| [docs/INSTALACAO_TOUCHDESIGNER.md](docs/INSTALACAO_TOUCHDESIGNER.md) | TouchDesigner: 4 UDP In DATs, callback V1, consumo dos cursores |
| [node/VALIDACAO.md](node/VALIDACAO.md) · [server/VALIDACAO.md](server/VALIDACAO.md) | Roteiros de validação e o que cada um **não** cobre |
| [CLAUDE.md](CLAUDE.md) | Regras do repositório para sessões de Claude Code |
| [KICKOFF_CLAUDE_CODE.md](KICKOFF_CLAUDE_CODE.md) | Prompts das sessões W0/W1/W2 (histórico de processo) |

Em conflito entre a spec e qualquer outro documento (ou o código), **a spec
vence**.

---

## Protocolos

Little-endian, sem padding. Definição canônica em
[shared/protocol.py](shared/protocol.py) — nenhum outro módulo do repo pode
declarar format strings de `struct`.

| | Header | Ponto | Coordenadas | Trecho |
|---|---|---|---|---|
| **V2** — Pi → relay | `<BBIdH` (16 B): version, panel_id, frame, timestamp, num_points | `<Ifff` (16 B): id, x, y, confidence | milímetros, referencial do sensor | rede, UDP 5555 |
| **V1** — relay → TD | `<IdH` (14 B): frame, timestamp, num_points | `<Ifff` (16 B) | `0..1`, referencial do painel | localhost, UDP 6001–6004 |
| **OSC** — relay → Max | `/touch/<panel_id>`, sem argumentos | — | — | UDP 7500 |

O V1 é byte-idêntico ao do sistema single-node: o callback do TouchDesigner
que já estava validado continua valendo sem uma linha de mudança.

---

## Estado atual

| Área | Estado |
|---|---|
| `shared/protocol.py` | pronto — 24 asserts de round-trip |
| `node/` | pronto — validado 21/21 sem hardware; falta o gate de CPU num 3B+ real |
| `server/` relay + calibrador | pronto — validado 12/12 com simulador |
| `server/ui.py` (painel de status) | **não implementado** — o monitoramento hoje é o log 1×/s do relay |
| Windows (server-a/server-b) | código escrito para Windows, mas **nunca executado lá** |
| Contraparte TD do `--target-source td` | **não implementada** — ver [docs/INSTALACAO_TOUCHDESIGNER.md](docs/INSTALACAO_TOUCHDESIGNER.md) §7 |
| `calib_p*.json` | não existem no repo — são gerados na calibração de cada instalação |

---

## Regras do repositório

1. Nenhum format string de `struct` fora de [shared/protocol.py](shared/protocol.py).
   A exceção é [deploy/td/udp_callback_v1.py](deploy/td/udp_callback_v1.py),
   que roda dentro do TouchDesigner e não importa o repo.
2. `node/` **não pode** importar pygame, tkinter ou customtkinter — o Pi é
   headless. `server/` pode tudo.
3. Cada sessão de trabalho mexe em um workstream só (`node/` ou `server/`).
4. `legacy/` e `legacy_recovery/` são read-only: copiar de lá, nunca editar lá.
5. Comentários e docstrings em pt-BR; nomes de código em inglês.
