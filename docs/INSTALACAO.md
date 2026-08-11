# Instalação — LidarMapper Distribuído v3

Guia de campo para instalar o sistema do zero: 8 painéis LED, 8 nós Raspberry
Pi com RPLIDAR S3, 2 servidores Windows com TouchDesigner + relay Python.

Este documento é o índice e a visão geral. O procedimento detalhado está
dividido por função:

| Documento | Para quem | O que cobre |
|---|---|---|
| [INSTALACAO_PI.md](INSTALACAO_PI.md) | quem monta os nós | imagem, udev, chrony, `config.yaml`, systemd, golden image |
| [INSTALACAO_SERVIDOR.md](INSTALACAO_SERVIDOR.md) | quem opera server-a/server-b | Python no Windows, firewall, `config_server.yaml`, relay, calibração |
| [INSTALACAO_TOUCHDESIGNER.md](INSTALACAO_TOUCHDESIGNER.md) | quem monta o projeto TD | 4 UDP In DATs, callback V1, consumo dos cursores |

> A spec do sistema é o [GUIA_LIDARMAPPER_DISTRIBUIDO_1.md](../GUIA_LIDARMAPPER_DISTRIBUIDO_1.md).
> Em conflito entre este guia de instalação e a spec, **a spec vence** — abra
> uma correção aqui. Cada seção abaixo aponta o § de origem.

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
2. **A calibração vive só no servidor.** `calib_p1.json .. calib_p4.json` ficam
   em [server/](../server/) e são recarregados a quente (por `mtime`) — trocar
   uma calibração não derruba nada.
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

## 3. Plano de rede (§4)

Rede cabeada gigabit dedicada ou VLAN isolada. DHCP com **reserva por MAC** —
a imagem do SD é idêntica em todos os nós, só o `config.yaml` difere.

| Host | IP | Função |
|---|---|---|
| server-a | 10.10.0.10 | TD #1 (painéis 1–4), relay, **master NTP** |
| server-b | 10.10.0.11 | TD #2 (painéis 5–8), relay |
| lidar-01 … lidar-04 | 10.10.0.21 … .24 | `panel_id` 1–4 → 10.10.0.10:5555 |
| lidar-05 … lidar-08 | 10.10.0.25 … .28 | `panel_id` 5–8 → 10.10.0.11:5555 |

Portas:

| Porta | Protocolo | De → Para | Firewall |
|---|---|---|---|
| **UDP 5555** | V2 (mm) | Pis → relay do seu servidor | **regra de entrada nos 2 Windows** |
| **UDP 6001–6004** | V1 (0..1) | relay → TouchDesigner, em `127.0.0.1` | localhost, sem regra |
| **UDP 7500** | OSC `/touch/N` | relay → Max/MSP | regra só se o Max estiver em outra máquina |

Banda por nó: ~16 kB/s a 30 Hz. Uma porta de entrada por servidor; o demux é
por `panel_id` dentro do pacote, **não** por porta.

> No server-b os painéis são 5–8, mas as portas de saída continuam **6001–6004**
> (o TD #2 é um espelho do TD #1). O mapeamento vive em
> [server/config_server.yaml](../server/config_server.yaml) — ver
> [INSTALACAO_SERVIDOR.md](INSTALACAO_SERVIDOR.md) §5.

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

Nos servidores, antes de abrir o TouchDesigner:

```
deploy\start_relay.bat
```

Deixe a janela do relay aberta — o status de 1×/s é o monitor de saúde do
sistema:

```
p1[C] in=30 out=30 drop=0 down=2 age= 0.0s   p2[C] in=30 out=30 drop=0 down=0 age= 0.0s   ...
```

- `[C]` = painel calibrado (repassando); `[-]` = **sem calibração, não repassa nada**
- `in` = pacotes V2 recebidos do Pi · `out` = pacotes V1 enviados ao TD
- `drop` = pontos descartados por caírem fora de `0..1` (ROI ou calibração ruins)
- `down` = total de `/touch/N` disparados · `age` = tempo desde o último pacote daquele nó

Os Pis sobem sozinhos no boot (systemd) — não precisa fazer nada neles.

**Refazer o fundo de um nó** (alguém ficou parado na frente do painel durante o
baseline):

```bash
ssh pi@lidar-0N sudo systemctl restart lidarmapper
```

Acesso aos nós: usuário `pi`, senha `pi123`, igual nos 8 — ver
[INSTALACAO_PI.md §1](INSTALACAO_PI.md#1-imagem-e-primeiro-boot).

---

## 7. Documentos relacionados

- [PROVISIONAMENTO_FROTA.md](PROVISIONAMENTO_FROTA.md) — **plano em execução**: levar os 8 nós de "cartão gravado" a "publicando V2", via SSH
- [GUIA_LIDARMAPPER_DISTRIBUIDO_1.md](../GUIA_LIDARMAPPER_DISTRIBUIDO_1.md) — a spec (fonte de verdade)
- [node/VALIDACAO.md](../node/VALIDACAO.md) — roteiro de validação do nó (W1)
- [server/VALIDACAO.md](../server/VALIDACAO.md) — roteiro de validação do servidor (W2)
- [shared/protocol.py](../shared/protocol.py) — os formatos V1/V2, único lugar do repo com `struct`
- [deploy/](../deploy/) — arquivos prontos para copiar (udev, systemd, chrony, callback do TD, `.bat`)
