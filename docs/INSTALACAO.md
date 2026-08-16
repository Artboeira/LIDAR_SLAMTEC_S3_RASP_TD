# Instalação — LidarMapper Distribuído v3

Guia de campo para instalar o sistema do zero: 8 painéis LED, 8 nós Raspberry
Pi com RPLIDAR S3, servidor Windows com TouchDesigner + ponte Python.

> **O sistema já está instalado (08/2026).** Para operar, diagnosticar ou
> refazer partes da instalação existente, use o
> **[MANUAL_DE_CAMPO.md](MANUAL_DE_CAMPO.md)** — este índice serve para uma
> instalação nova do zero.

Este documento é o índice e a visão geral. O procedimento detalhado está
dividido por função:

| Documento | Para quem | O que cobre |
|---|---|---|
| [MANUAL_DE_CAMPO.md](MANUAL_DE_CAMPO.md) | operador / herdeiro do sistema | operação, runbooks, troubleshooting, tabela canônica da frota |
| [OPERACAO_EVENTO_WINDOWS.md](OPERACAO_EVENTO_WINDOWS.md) | operador do servidor | installer em 1 comando, SSH, rotina diária do evento |
| [INSTALACAO_PI.md](INSTALACAO_PI.md) | quem monta os nós | imagem, udev, chrony, config, systemd, provisionamento da frota |
| [INSTALACAO_SERVIDOR.md](INSTALACAO_SERVIDOR.md) | quem monta o servidor | Python no Windows, firewall, fleet_bridge/relay, calibração |
| [INSTALACAO_TOUCHDESIGNER.md](INSTALACAO_TOUCHDESIGNER.md) | quem monta o projeto TD | OSC In CHOP (modo real) e V1 binário (alternativo) |

> A spec do projeto é o [GUIA_LIDARMAPPER_DISTRIBUIDO_1.md](../GUIA_LIDARMAPPER_DISTRIBUIDO_1.md) —
> o **§3 (protocolos) é normativo**; a topologia e o consumo do TD descritos
> lá foram substituídos na instalação real (ver a nota de status no topo dela).

---

## 1. O que o sistema faz (§1, §2)

Um painel LED por vez:

```
   RPLIDAR S3                Raspberry Pi                Servidor Windows                 Painel LED
   (varre o plano       USB   lidar-0N                    server-a / server-b
    à frente do         --->  node/main.py                                                    ^
    painel)                   • filtra ruído                                                  |
                              • subtrai fundo                                            vídeo|
                              • agrupa (DBSCAN)                                                |
                              • rastreia cursores                                        +-----------+
                              • publica mm                                               |    TD     |
                                    |                                                    +-----------+
                                    | UDP :5555   protocolo V2 (mm, com panel_id)              ^
                                    v                                                          |
                              server_relay.py                                                  |
                              • demux por panel_id                                             |
                              • aplica homografia (calib_pN.json)                        UDP :600N
                              • descarta fora de [0..1]                                  protocolo V1
                              • detecta toque (down)  --- OSC /touch/N :7500 ---> Max     (0..1)
                              • reempacota V1 ------------------------------------------------+
```

As três regras que explicam todo o resto:

1. **O Pi é burro.** Ele não sabe onde fica o painel; publica coordenadas em
   **milímetros no referencial do sensor**. Não existe `calibration.json` no Pi.
2. **A calibração vive só no servidor.** `calib_p1.json .. calib_p8.json` ficam
   em [server/](../server/) e são recarregados a quente (por `mtime`) — trocar
   uma calibração não derruba nada. (O diagrama mostra o modo relay+V1 da
   spec; na instalação real o `fleet_bridge.py` faz esse papel com saída OSC.)
3. **O TouchDesigner só consome.** Ele recebe coordenadas já em `0..1` e
   preenche uma tabela. Nenhuma matemática, nenhum demux, nenhuma calibração.

---

## 2. Inventário por painel

| Item | Especificação | Observação |
|---|---|---|
| LIDAR | RPLIDAR S3 | ~32k amostras/s, USB (CP210x) |
| Computador | Raspberry Pi 3B+, 4 ou 5 | **3B+ é o pior caso de projeto** — se roda nele, roda em todos |
| Fonte | 3B+: 5 V / 2,5 A microUSB · Pi 4: 5 V / 3 A USB-C · Pi 5: 5 V / 5 A USB-C (oficial) | ver §5 abaixo |
| Refrigeração | 3B+: dissipador passivo · Pi 4: case ventilado ou dissipador · Pi 5: active cooler | operação de horas |
| Cartão SD | 16 GB+ classe 10 | 8 clones da golden image |
| Rede | cabo ethernet até o switch gigabit | **Wi-Fi é vetado** |
| Cabo USB | o que vem com o S3, curto | cabo longo/ruim = subtensão |

Central: 1 switch gigabit (ou VLAN isolada), 2 PCs Windows com TouchDesigner,
1 máquina com Max/MSP (pode ser o próprio servidor).

> ⚠️ **Subtensão derruba o show.** O S3 puxa ~1,5 W do USB. Fonte genérica ou
> cabo ruim faz o CP210x cair no meio da operação — e o modo de falha é
> intermitente, difícil de diagnosticar. Fonte oficial não é opcional,
> principalmente no 3B+ (§6 da spec).

---

## 3. Plano de rede (o que a instalação real usa)

Rede cabeada gigabit. O DHCP é o **roteador do evento** (com ou sem saída
para a internet) ligado ao switch, e a faixa de IP é a dele — na instalação
CURVA, `192.168.1.x`. **Reserva DHCP por MAC é obrigatória para o servidor**
(sem ela, o IP muda num reboot e os 8 nós ficam mudos — aconteceu) e
recomendada para os 8 Pis (MACs na tabela do
[MANUAL_DE_CAMPO.md §2](MANUAL_DE_CAMPO.md)).

Cada nó envia o V2 para UM IP (`udp.host`). Para (re)apontar os 8 sem perder
a ROI ajustada de cada um, use o modo `--update` — o runbook completo (bash e
PowerShell, com o mapa painel→hostname) é o
[R2 do manual](MANUAL_DE_CAMPO.md):

```bash
ssh pi@<hostname> "~/lidarmapper/.venv/bin/python \
  ~/lidarmapper/deploy/render_node_config.py --panel <N> \
  --udp-host <IP-do-servidor> --update --out /home/pi/node-config.yaml \
  && sudo systemctl restart lidarmapper"
```

Se usar NTP local, atualize o `server` de
[deploy/chrony-node.conf](../deploy/chrony-node.conf) para o IP real do
servidor (o default `10.10.0.10` vem da spec e não existe na rede real).

Portas:

| Porta | Protocolo | De → Para | Firewall |
|---|---|---|---|
| **UDP 5555** | V2 (mm) | Pis → fleet_bridge/relay | **regra de entrada no Windows** (o installer cria) |
| **UDP 7000** | OSC `pN_*` | fleet_bridge → TouchDesigner | regra de entrada na máquina do TD (o installer cria) |
| **UDP 7500** | OSC `/touch/N` | fleet_bridge/relay → Max/MSP | regra só se o Max estiver em outra máquina |
| UDP 6001–6004 | V1 (0..1) | relay → TD, em `127.0.0.1` | só no modo relay+V1; localhost, sem regra |

Banda por nó: ~16 kB/s a 30 Hz. Uma porta de entrada por servidor; o demux é
por `panel_id` dentro do pacote, **não** por porta.

> A spec previa 2 servidores (`10.10.0.10/.11`, painéis 1–4 e 5–8). A
> instalação real usa **1 servidor com o fleet_bridge**; o modo com 2 relays
> continua possível (o demux por `panel_id` não depende de um servidor só).

---

## 4. Ordem de instalação (§13 da spec)

A ordem importa: cada etapa é validável sozinha, e a etapa seguinte só começa
quando a anterior está verde. Não instale os 8 painéis em paralelo — instale
**um** painel inteiro, valide ponta a ponta, e só então clone.

| # | Etapa | Documento | Critério de pronto |
|---|---|---|---|
| 1 | Rede: switch, IPs fixos dos servidores, reservas DHCP | este doc §3 | `ping` entre todos os hosts |
| 2 | server-a: Python, venv, firewall, `config_server.yaml` | [SERVIDOR](INSTALACAO_SERVIDOR.md) §1–5 | smoke com simulador passa (§6 de lá) |
| 3 | TouchDesigner do server-a: 4 UDP In DATs + callback | [TD](INSTALACAO_TOUCHDESIGNER.md) | `touches_p1` popula com o simulador rodando |
| 4 | lidar-01 na bancada: imagem, udev, chrony, testes | [PI](INSTALACAO_PI.md) §1–8 | `test_lidar.py` nominal, gate de CPU do §10 ok |
| 5 | Montagem física do painel 1 + ROI/offset/mirror | [PI](INSTALACAO_PI.md) §7 | `main.py` com `fg` subindo quando alguém passa |
| 6 | Calibração do painel 1 | [SERVIDOR](INSTALACAO_SERVIDOR.md) §7 | `calib_p1.json` gravado, erro de reprojeção aceitável |
| 7 | Ponta a ponta do painel 1: Pi → relay → TD + Max | checklist §5 abaixo | tocar os 4 cantos responde no lugar certo |
| 8 | systemd + teste de reboot no lidar-01 | [PI](INSTALACAO_PI.md) §9 | corta energia, volta sozinho |
| 9 | **Golden image** do lidar-01 validado | [PI](INSTALACAO_PI.md) §10 | imagem gravada e guardada |
| 10 | Clonar e fazer bring-up dos painéis 2–4 | checklist §5 abaixo | 4 painéis verdes no server-a |
| 11 | server-b + painéis 5–8, repetindo 2–10 | todos | 8 painéis verdes |

Etapas 2, 3 e 4 podem andar em paralelo por pessoas diferentes: o servidor e o
TD são validáveis sem nenhum Pi ligado, via
[server/test_node_sim.py](../server/test_node_sim.py).

---

## 5. Checklist de bring-up por painel (§12)

Rode este checklist inteiro em cada um dos 8 painéis. É o mesmo do §12 da spec,
com o link do passo correspondente.

- [ ] 1. Pi na rede, hostname/IP corretos — `ping lidar-0N`
- [ ] 2. `chronyc tracking` sincronizado com o server-a — [PI §6](INSTALACAO_PI.md)
- [ ] 3. `ls -l /dev/rplidar` existe — [PI §5](INSTALACAO_PI.md)
- [ ] 4. `python node/test_e2e.py` passa (não precisa do sensor)
- [ ] 5. `python node/test_lidar.py` com `meas/s` nominal, CPU dentro do gate, `vcgencmd get_throttled` = `0x0`
- [ ] 6. LIDAR montado, nivelado no plano do painel, altura definida
- [ ] 7. `node/config.yaml`: `udp.host`, **`udp.panel_id`**, `roi`, `angle_offset_deg`, `mirror` — [PI §7](INSTALACAO_PI.md)
- [ ] 8. `sudo systemctl enable --now lidarmapper` com a área livre durante o baseline
- [ ] 9. No servidor: `python server/test_udp_receiver.py --v2 --port 5555` mostra pacotes com o `panel_id` certo
- [ ] 10. Calibração do painel, erro de reprojeção aceitável, relay recarregou sozinho — [SERVIDOR §7](INSTALACAO_SERVIDOR.md)
- [ ] 11. `python server/test_udp_receiver.py --v1 --port 600N` com coords em `0..1`
- [ ] 12. TD: `touches_pN` populando, cursor segue a mão — [TD](INSTALACAO_TOUCHDESIGNER.md)
- [ ] 13. Toque dispara `/touch/N` no Max (som do cubo certo)
- [ ] 14. Tocar os 4 cantos → as partículas respondem no lugar certo
- [ ] 15. Teste de reboot: cortar energia do Pi → volta sozinho; o relay segue de pé no servidor

---

## 6. Operação diária (resumo)

O modo normal do evento é o **fleet_bridge** — no servidor Windows:

```
start_fleet.bat
```

Critério: 8 cartões verdes com `in=30/s`. Teclas, contratos OSC, baseline e
recalibração: [MANUAL_DE_CAMPO.md §3–§4](MANUAL_DE_CAMPO.md). Rotina de
ligar/desligar do evento: [OPERACAO_EVENTO_WINDOWS.md §5](OPERACAO_EVENTO_WINDOWS.md).

Os Pis sobem sozinhos no boot (systemd) — não precisa fazer nada neles.
**Refazer o fundo de um nó** (alguém ficou parado na frente durante o
baseline): `ssh pi@<hostname> sudo systemctl restart lidarmapper` com a área
livre, ou `deploy\baseline.ps1 <painel>` do Windows.

No modo alternativo relay+V1 (`deploy\start_relay.bat`), o monitor é o status
1×/s do relay: `p1[C] in=30 out=30 drop=0 down=2 age= 0.0s` — `[C]` =
calibrado; `[-]` = sem calibração, não repassa; `drop` = pontos fora de
`0..1`; `age` = tempo desde o último pacote do nó.

Acesso aos nós: usuário `pi`, senha `pi123`, igual nos 8 — ver
[INSTALACAO_PI.md §1](INSTALACAO_PI.md#1-imagem-e-primeiro-boot).

---

## 7. Documentos relacionados

- [MANUAL_DE_CAMPO.md](MANUAL_DE_CAMPO.md) — operação, runbooks e troubleshooting do sistema instalado
- [OPERACAO_EVENTO_WINDOWS.md](OPERACAO_EVENTO_WINDOWS.md) — operar tudo a partir do servidor Windows
- [PROVISIONAMENTO_FROTA.md](PROVISIONAMENTO_FROTA.md) — stub histórico (provisionamento concluído; conteúdo migrado)
- [GUIA_LIDARMAPPER_DISTRIBUIDO_1.md](../GUIA_LIDARMAPPER_DISTRIBUIDO_1.md) — a spec original (§3 normativo)
- [node/VALIDACAO.md](../node/VALIDACAO.md) — roteiro de validação do nó (W1)
- [server/VALIDACAO.md](../server/VALIDACAO.md) — roteiro de validação do servidor (W2)
- [shared/protocol.py](../shared/protocol.py) — os formatos V1/V2, único lugar do repo com `struct`
- [deploy/](../deploy/) — arquivos prontos para copiar (udev, systemd, chrony, callback do TD, `.bat`)
