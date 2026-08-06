# Instalação do servidor — Windows (server-a / server-b)

O servidor roda o **middleware**: recebe V2 dos Pis, aplica a homografia,
dispara OSC para o Max e entrega V1 ao TouchDesigner. Toda a calibração do
sistema vive aqui.

Base: §2, §4, §5.2 e §7 da [spec](../GUIA_LIDARMAPPER_DISTRIBUIDO_1.md) + o
roteiro de [server/VALIDACAO.md](../server/VALIDACAO.md).

Faça este procedimento no **server-a** primeiro (ele também é o master de NTP)
e repita no **server-b** trocando os painéis.

Índice:

1. [Python no Windows](#1-python-no-windows)
2. [Repositório e ambiente virtual](#2-repositório-e-ambiente-virtual)
3. [Firewall](#3-firewall)
4. [Rede e relógio](#4-rede-e-relógio)
5. [config_server.yaml](#5-config_serveryaml)
6. [Smoke test sem nenhum Pi](#6-smoke-test-sem-nenhum-pi)
7. [Calibração de um painel](#7-calibração-de-um-painel)
8. [Operação diária](#8-operação-diária)
9. [Troubleshooting](#9-troubleshooting)

> ⚠️ **O código do servidor foi escrito para Windows (paths, sockets, sem
> systemd), mas os testes automatizados rodaram em macOS/Linux**
> ([server/VALIDACAO.md](../server/VALIDACAO.md) §3). As diferenças conhecidas
> estão marcadas com "Windows:" ao longo do texto — em especial o conflito de
> porta entre o relay e o calibrador (§7).

---

## 1. Python no Windows

Instale o **Python 3.13** (mínimo 3.11) do [python.org](https://www.python.org/downloads/windows/).
No instalador, marque **"Add python.exe to PATH"**.

Confirme:

```
py -3.13 --version
```

Não use a versão da Microsoft Store — ela isola o filesystem e complica os
paths do venv.

---

## 2. Repositório e ambiente virtual

Clone em um caminho curto e sem espaços:

```
cd C:\
git clone <URL-DO-REPO> lidarmapper
cd C:\lidarmapper
py -3.13 -m venv .venv
.venv\Scripts\pip install -r server\requirements-server.txt
```

Instala: `pyyaml`, `ruamel.yaml`, `numpy`, `python-osc`, `pygame`.

> ⚠️ **Sempre execute a partir da raiz do repo** (`C:\lidarmapper`), nunca de
> dentro de `server\`. O [server/server_relay.py](../server/server_relay.py)
> faz `from server import config_server` — de dentro da pasta `server\` o
> import quebra. O [deploy/start_relay.bat](../deploy/start_relay.bat) já faz
> o `cd` correto sozinho.

Confirme o ambiente:

```
.venv\Scripts\python -c "import numpy, yaml, pythonosc, pygame; print('ok')"
```

---

## 3. Firewall

Só a **entrada UDP 5555** precisa de regra: é por onde os Pis falam. As portas
6001–6004 (relay → TD) e 7500 (relay → Max) são tráfego de `127.0.0.1` e o
firewall do Windows não filtra loopback.

Num PowerShell/CMD **como administrador**:

```
netsh advfirewall firewall add rule name="LidarMapper V2 in" dir=in action=allow protocol=UDP localport=5555
```

Conferir:

```
netsh advfirewall firewall show rule name="LidarMapper V2 in"
```

Se o Max/MSP estiver em **outra máquina**, abra também a saída UDP 7500 nela e
ajuste `osc.host` no `config_server.yaml` (§5).

---

## 4. Rede e relógio

**IP fixo:** `10.10.0.10` no server-a, `10.10.0.11` no server-b, máscara
`255.255.255.0`, na interface cabeada. Sem gateway se a rede for isolada.

**Reservas de DHCP** dos 8 Pis por MAC, conforme a tabela de
[INSTALACAO.md §3](INSTALACAO.md#3-plano-de-rede-4).

**NTP — o server-a é o master.** Os Pis apontam para `10.10.0.10` via chrony.
Para o Windows responder como servidor NTP, num terminal como administrador:

```
w32tm /config /manualpeerlist:"time.windows.com" /syncfromflags:manual /reliable:yes /update
reg add "HKLM\SYSTEM\CurrentControlSet\Services\W32Time\TimeProviders\NtpServer" /v Enabled /t REG_DWORD /d 1 /f
reg add "HKLM\SYSTEM\CurrentControlSet\Services\W32Time\Config" /v AnnounceFlags /t REG_DWORD /d 5 /f
net stop w32time && net start w32time
w32tm /query /status
```

E libere a entrada UDP 123:

```
netsh advfirewall firewall add rule name="NTP in" dir=in action=allow protocol=UDP localport=123
```

Do lado do Pi, `chronyc sources -v` precisa mostrar o `10.10.0.10` com `^*`.
Se o serviço NTP do Windows der trabalho, a alternativa é apontar todos os Pis
(e o chrony do server-a) para um roteador/appliance da rede — o que importa é
**uma referência comum**, não qual é.

> Não é cosmético: o `timestamp` do V2 vem do relógio do Pi, e é ele que
> alimenta a latência reportada por `test_udp_receiver.py`. Sem NTP, esse
> número é ficção.

---

## 5. config_server.yaml

Arquivo: [server/config_server.yaml](../server/config_server.yaml).

```yaml
listen_port: 5555          # entrada V2 dos Pis (uma porta só; demux por panel_id)

panels:
  1:                       # <- panel_id que o Pi manda no header V2
    out_port: 6001         # porta do UDP In DAT no TD
    display_index: 1       # monitor onde a calibração local desenha os alvos
    calib_file: calib_p1.json   # relativo a server/
  2: { out_port: 6002, display_index: 2, calib_file: calib_p2.json }
  3: { out_port: 6003, display_index: 3, calib_file: calib_p3.json }
  4: { out_port: 6004, display_index: 4, calib_file: calib_p4.json }

osc:
  host: 127.0.0.1          # máquina do Max/MSP
  port: 7500
  timeout_s: 0.18          # sem ver a id por esse tempo, o track é considerado "up"

td:
  host: 127.0.0.1          # o TD roda na mesma máquina
  clip_out_of_range: true  # descarta pontos fora de [0..1] após a homografia

calibration:
  collect_s: 2.0           # tempo de coleta por canto
  window_mm: 250           # declarado, não usado pelo código atual
  min_pts: 30              # menos que isso num canto = calibração falha
  target_insets: [0.06, 0.94]   # posição dos 4 alvos em coords normalizadas
```

### server-b: painéis 5–8 nas mesmas portas 6001–6004

O arquivo versionado descreve o server-a. **No server-b, renumere as chaves de
`panels` para 5–8, mantendo as portas de saída 6001–6004** — o projeto do TD #2
é um espelho do TD #1:

```yaml
panels:
  5: { out_port: 6001, display_index: 1, calib_file: calib_p5.json }
  6: { out_port: 6002, display_index: 2, calib_file: calib_p6.json }
  7: { out_port: 6003, display_index: 3, calib_file: calib_p7.json }
  8: { out_port: 6004, display_index: 4, calib_file: calib_p8.json }
```

A chave é o `panel_id` que chega no pacote; `out_port` é para onde vai depois.
Um `panel_id` que não está no arquivo é ignorado (o relay loga uma vez).

`display_index` é o índice do monitor no pygame durante a calibração local —
descubra qual é qual rodando a calibração e vendo onde a janela abre.

---

## 6. Smoke test sem nenhum Pi

Valida servidor + TD antes de qualquer hardware chegar. Três janelas de
terminal, todas em `C:\lidarmapper`.

**Janela 1 — simulador dos 4 nós:**

```
.venv\Scripts\python server\test_node_sim.py --panels 1,2,3,4 --pattern circle
```

Cada painel manda 30 pacotes/s de V2 sintético para `127.0.0.1:5555`, com 2
cursores girando.

**Janela 2 — relay:**

```
deploy\start_relay.bat --no-osc
```

(`--no-osc` enquanto o Max não estiver de pé; sem isso o relay tenta enviar OSC
para `127.0.0.1:7500` e não reclama, mas o log fica menos claro.)

Esperado, 1×/s:

```
p1[-] in=30 out=0 drop=0 down=0 age= 0.0s   p2[-] in=30 out=0 drop=0 down=0 age= 0.0s   ...
```

`in=30` prova que a rede e o parsing V2 estão ok. `[-]` e `out=0` são o
comportamento correto **sem calibração** — o relay não inventa homografia.

**Janela 3 — o que o TD vai receber:**

```
.venv\Scripts\python server\test_udp_receiver.py --v1 --port 6001
```

Enquanto nenhum painel estiver calibrado, aqui não chega nada. Faça a
calibração do painel 1 (§7, funciona com o simulador para exercitar o fluxo) e
observe: o relay passa a `p1[C] ... out=30`, e esta janela mostra `fps=30.0`
com coordenadas em `0..1`. Use `--raw` para ver frame a frame.

Com isso funcionando, o TD já pode ser montado
([INSTALACAO_TOUCHDESIGNER.md](INSTALACAO_TOUCHDESIGNER.md)) sem esperar os Pis.

---

## 7. Calibração de um painel

A calibração casa **milímetros no plano do sensor** com **coordenadas
normalizadas do painel**. São 4 cantos (TL → TR → BR → BL), DLT/SVD, e um
`calib_pN.json` gravado em `server\`.

### Pré-requisitos

- O nó daquele painel publicando: confirme com
  `.venv\Scripts\python server\test_udp_receiver.py --v2 --port 5555` (o
  `panel_id` certo aparece em `panels=`).
- O painel exibindo os alvos — decida a fonte abaixo.
- **Windows: feche o relay antes de calibrar.** O calibrador tenta dividir a
  porta 5555 com o relay via `SO_REUSEADDR`; em Linux/macOS isso funciona, mas
  a semântica do Windows é diferente e o bind pode falhar ou roubar os pacotes.
  Feche a janela do relay, calibre, e suba o relay de novo — ele carrega a
  calibração nova sozinha.

### Duas fontes de alvo

| Fonte | Quando usar | Como funciona |
|---|---|---|
| `local` | há saída de vídeo direta da GPU para o painel | pygame fullscreen no `display_index`, desenha os 4 alvos; o TD precisa estar fora daquele output |
| `td` | cadeia de vídeo com processador (Novastar/Colorlight) ou canvas único | o calibrador manda OSC `/calib/target <panel_id> <corner_idx> <u> <v>` e o **TD** desenha o alvo |

### Fonte `local`

```
.venv\Scripts\python server\calibrate.py --panel 1 --target-source local
```

Abre em fullscreen no monitor `display_index` do painel. Para testar em janela:
acrescente `--no-fullscreen`.

Para cada um dos 4 cantos:

1. O alvo aceso pisca em amarelo, com o nome do canto na tela.
2. O operador **toca o alvo e mantém a posição**.
3. Aperta **ESPAÇO** — a tela muda para "CAPTURANDO..." e o coletor junta
   `collect_s` (2 s) de pacotes daquele painel.
4. Repete em TOP-LEFT → TOP-RIGHT → BOTTOM-RIGHT → BOTTOM-LEFT.

`ESC` ou `Q` abortam sem gravar nada.

> ⚠️ **Toque com a mão espalmada ou um objeto**, não com a ponta do dedo. O
> tracker exige `dbscan_min_samples: 4` pontos dentro de um raio de 150 mm — um
> dedo fino a 3 m de distância pode não formar cluster nenhum, e o canto falha
> com `(N pts, min=30)`.

### Fonte `td`

```
.venv\Scripts\python server\calibrate.py --panel 1 --target-source td
```

O calibrador envia `/calib/target <panel_id> <corner_idx> <u> <v>` para
`osc.host:osc.port` e espera **ENTER** no terminal a cada canto (`q` sai). No
fim manda `/calib/target 0 -1 0.0 0.0` para apagar o alvo.

> A contraparte no TD (um COMP que acende o alvo `corner_idx` na posição
> `(u,v)` do painel `panel_id`) **ainda não existe** — ver
> [INSTALACAO_TOUCHDESIGNER.md](INSTALACAO_TOUCHDESIGNER.md) §7.

### Resultado

No fim, o log mostra:

```
[calibrate] salvo em calib_p1.json — erros (px): ['3.2', '4.1', '2.8', '5.0']
```

Esses são os **erros de reprojeção em pixels** de cada canto. Poucos pixels =
bom. Dezenas de pixels = alguém se moveu durante a captura, ou o toque não foi
no centro do alvo — repita a calibração daquele painel.

O arquivo gravado (mesmo schema do sistema single-node) tem
`corners_lidar_mm`, `corners_screen_norm` e `H`.

### Hot-reload

O relay confere o `mtime` do `calib_pN.json` a cada pacote. Assim que a
gravação termina, o próximo pacote daquele painel já usa a homografia nova —
**não reinicie o relay** por causa de calibração. Se o relay estava rodando com
`[-]` naquele painel, ele passa a `[C]` sozinho.

---

## 8. Operação diária

Antes do show, com o TouchDesigner ainda fechado:

```
deploy\start_relay.bat
```

Deixe a janela aberta. Enquanto o `server/ui.py` não existir, esse console é o
monitor de saúde do sistema:

```
p1[C] in=30 out=30 drop=0 down=2 age= 0.0s   p2[C] in=30 out=29 drop=1 down=0 age= 0.0s
```

| Campo | Significado | O que é normal |
|---|---|---|
| `[C]` / `[-]` | painel calibrado / sem calibração | `[C]` em todos |
| `in` | pacotes V2 recebidos daquele nó (acumulado) | crescendo ~30/s |
| `out` | pacotes V1 enviados ao TD (acumulado) | acompanhando o `in` |
| `drop` | pontos descartados fora de `0..1` | perto de zero em repouso |
| `down` | total de `/touch/N` disparados | sobe a cada toque novo |
| `age` | tempo desde o último pacote daquele nó | `0.0s`; subindo = nó caiu |

Depois abra o TouchDesigner. Para encerrar: `Ctrl+C` na janela do relay.

Ordem inversa no fim: feche o TD, depois o relay. Os Pis podem ficar ligados.

---

## 9. Troubleshooting

| Sintoma | Causa provável | O que fazer |
|---|---|---|
| `in=0` em todos os painéis | firewall, IP errado no Pi, cabo | regra UDP 5555 (§3); `udp.host` no `node/config.yaml`; `ping lidar-0N` |
| `in=0` num painel só | aquele nó caiu ou está com `panel_id` errado | `ssh lidar-0N systemctl status lidarmapper` |
| `panel_id=9 fora do config_server.yaml` no log | `panel_id` do Pi não está no `panels:` | corrigir o `config.yaml` do nó ou o `config_server.yaml` |
| Painel em `[-]` | falta `calib_pN.json` | calibrar (§7) |
| `in` alto, `out=0`, `drop` alto | homografia errada ou ROI larga: tudo cai fora de `0..1` | recalibrar; conferir a ROI do nó |
| `age` subindo | nó parou de publicar | ver o Pi ([INSTALACAO_PI.md §12](INSTALACAO_PI.md#12-troubleshooting)) |
| `V2 inválido de ('10.10.0.2x', …)` | versão de protocolo divergente entre Pi e servidor | `git pull` nos dois lados; o `shared/protocol.py` é o contrato |
| Relay não sobe: `ModuleNotFoundError: server` | executado de dentro de `server\` | rodar da raiz, ou usar o `.bat` (§2) |
| Relay não sobe: `Address already in use` | já tem um relay ou um `calibrate.py` na 5555 | fechar o outro processo (`netstat -ano \| findstr :5555`) |
| `down` não sobe ao tocar | tracker não formou cluster, ou já havia a mesma id ativa | tocar com a mão espalmada; conferir `tracks` no log do Pi |
| Max não recebe `/touch/N` | relay com `--no-osc`, `osc.host` errado, Max escutando outra porta | conferir a linha de start do relay e o `config_server.yaml` |
| TD não recebe nada, mas o relay marca `out>0` | porta do UDP In DAT ≠ `out_port` | conferir §5 e [INSTALACAO_TOUCHDESIGNER.md](INSTALACAO_TOUCHDESIGNER.md) |

Ferramentas de diagnóstico, sempre da raiz do repo:

```
.venv\Scripts\python server\test_udp_receiver.py --v2 --port 5555        # o que os Pis mandam
.venv\Scripts\python server\test_udp_receiver.py --v1 --port 6001 --raw  # o que o TD recebe
.venv\Scripts\python server\test_node_sim.py --panels 1 --pattern static # gera tráfego sem Pi
```
