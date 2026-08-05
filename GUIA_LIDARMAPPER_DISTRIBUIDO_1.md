# LidarMapper Distribuído v3 — 8 painéis, 8 nós Pi, middleware, 2 servidores TD

Arquitetura 2026-07-14; revisão multi-modelo e **middleware (Opção C)**
em 2026-08-05:

- **Pi = sender burro.** Lê o S3, filtra, rastreia, envia cursores em **mm
  (referencial do sensor)** via UDP V2. Não conhece calibração.
- **Middleware no servidor (`server_relay.py`) = calibração + homografia.**
  Evolução do LidarMapper single-node (o "zip"). Recebe V2 dos 4 nós,
  aplica a H por painel e reenvia **V1 (0..1) via localhost** pro TD —
  o TD consome exatamente o formato já testado do `TOUCHDESIGNER.md`.
- **TD = consumidor puro.** 4 UDP In DATs com o callback V1 do zip,
  sem matemática. Zero mudança em relação ao que foi validado.
- **Hardware: frota mista Raspberry Pi 3B+ / 4 / 5.** Motivação: aproveitar
  os 3B+ já disponíveis no estúdio. O **3B+ é o worst case de projeto** —
  todo o pipeline do nó deve rodar confortável nele (§10). A imagem
  Raspberry Pi OS Lite **arm64** é a mesma para os três modelos, então a
  golden image (§11) continua única; só `config.yaml` difere por nó.
- **Parsing serial vetorizado (numpy) é requisito, não fallback** (§5.0) —
  é o que torna o 3B+ viável.
- **Dois workstreams de Claude Code em paralelo (§5):** W1 = porte do nó
  Pi; W2 = evolução do zip em middleware de servidor.

---

## 1. Topologia

```
 Painel 1..4                                  Painel 5..8: espelho no Servidor B
 ┌─────────┐
 │ LED     │◄── vídeo ──── Servidor A ─────────────────────────┐
 └────┬────┘               │                                   │
      │ interação          │  ┌─────────────────────────────┐  │
 ┌────┴────┐               │  │ server_relay.py (middleware)│  │
 │RPLIDAR S3├─USB─┐        │  │  - 4× calib_pN.json         │  │
 └─────────┘      │        │  │  - homografia mm → 0..1     │  │
             ┌────┴───┐    │  │  - eventos down/up → OSC    │  │
             │Pi 3B+/ │    │  └──┬──────────────────────┬───┘  │
             │4/5     ├────┼────►│ UDP :5555 (V2, rede) │      │
             │lidar-0N│    │     │                      ▼      │
             └────┬───┘    │     │   localhost UDP V1 :6001-04 │
                  │        │     │                      │      │
                  │        │  ┌──┴──────────────────────┴───┐  │
                  │        │  │ TouchDesigner (consumidor)  │◄─┘
                  │        │  │  4× UDP In DAT (callback V1)│
                  │        │  └─────────────────────────────┘
                  └────────┴──── switch gigabit ── demais nós
```

Nota sobre o 3B+: a porta "gigabit" dele é atendida pelo barramento USB 2.0
interno (~220–300 Mbps reais). Irrelevante para nós — a carga por nó é
~16 kB/s (§4) — mas fica registrado para ninguém estranhar num `iperf`.

## 2. Divisão de responsabilidades

| Camada                            | Onde roda           | Módulo              |
|-----------------------------------|---------------------|---------------------|
| Leitura serial resiliente         | Pi                  | `lidar_reader.py`   |
| Filtros, ROI (mm), baseline       | Pi                  | `processing.py`     |
| DBSCAN + tracking + smoothing     | Pi                  | `tracker.py`        |
| Empacotamento UDP V2 (mm)         | Pi                  | `publisher.py`      |
| **Demux por `panel_id`**          | **Servidor (relay)**| `server_relay.py`   |
| **Homografia mm → 0..1**          | **Servidor (relay)**| `server_relay.py`   |
| **Descarte fora de 0..1**         | **Servidor (relay)**| `server_relay.py`   |
| **Eventos down/up + OSC → Max**   | **Servidor (relay)**| `server_relay.py`   |
| **Calibração 4 cantos**           | **Servidor**        | `calibrate.py` (§7) |
| Reempacote V1 → localhost         | Servidor (relay)    | `server_relay.py`   |
| Consumo V1 (visual)               | TD                  | callback do zip     |

Consequência boa: o descarte pós-homografia no relay é uma segunda linha
de defesa contra o LIDAR ver o painel vizinho (além do ROI em mm no Pi).

O `calibration.json` não existe nos Pis. Vive no servidor, um por painel:
`calib_p1.json .. calib_p4.json` (mesmo schema do zip — reusa
`homography.py` intacto, incluindo `corners_lidar_mm`,
`corners_screen_norm` e `H`).

## 3. Protocolo LIDAR_MAPPER_V2

Little-endian, sem padding.

```
Header (16 bytes):
  uint8    version        (= 2)
  uint8    panel_id       (1..8, definido no config.yaml do nó)
  uint32   frame
  float64  timestamp      (time.time() do Pi — NTP obrigatório, §6)
  uint16   num_points     (N)

Por ponto (16 bytes × N):
  uint32   id             (único por nó, não global)
  float32  x_mm           (referencial do sensor)
  float32  y_mm
  float32  confidence     (0..1)
```

Struct format strings:
- header: `"<BBIdH"` (16 B)
- ponto:  `"<Ifff"` (16 B, inalterado)

Com `max_tracks: 10` → 176 bytes. Com `max_points: 32` → 528 B. Sem
fragmentação.

O byte `version` na frente permite conviver com V1 durante a transição e
rejeitar pacotes alheios na porta.

### 3.1 Saída do relay: LIDAR_MAPPER_V1 (inalterado do zip)

O relay reempacota e envia ao TD via **localhost**, uma porta por painel,
no formato V1 já testado (`TOUCHDESIGNER.md` do zip vale sem edição):

```
Header (14 bytes, "<IdH"):  uint32 frame, float64 timestamp, uint16 N
Ponto  (16 bytes, "<Ifff"): uint32 id, float32 x (0..1), float32 y (0..1),
                            float32 confidence
```

`frame` e `timestamp` são repassados do pacote V2 de origem (o timestamp
segue sendo o do Pi — a latência fim-a-fim continua mensurável no TD).
`id` é o track id do nó, repassado como veio: como cada painel tem sua
própria porta/tabela, não há colisão entre nós.

### 3.2 Eventos de toque → OSC (relay → Max/MSP)

O relay é quem tem visão de estado por painel, então é ele que deriva
down/up e dispara o áudio — o TD não participa desse caminho:

- mantém, por `panel_id`, o conjunto de ids do último frame;
- id inédito neste frame → **down** → envia OSC `/touch/<panel_id>` ao
  Max (UDP :7500, host no `config_server.yaml`);
- id ausente por `timeout_s` → **up** (reservado; o patch atual do Max
  só consome down, o envelope de release é interno ao patch);
- debounce: um mesmo track não redispara down (ids são persistentes no
  tracker do nó — é o que torna essa regra confiável).

## 4. Plano de rede

| Host        | IP sugerido   | Função                              |
|-------------|---------------|-------------------------------------|
| server-a    | 10.10.0.10    | TD #1 (painéis 1–4), NTP master     |
| server-b    | 10.10.0.11    | TD #2 (painéis 5–8)                 |
| lidar-01..04| 10.10.0.21–24 | panel_id 1–4 → 10.10.0.10:5555      |
| lidar-05..08| 10.10.0.25–28 | panel_id 5–8 → 10.10.0.11:5555      |

- **Uma porta (5555) por servidor** para a entrada V2; demux por
  `panel_id` **no relay** (não mais no TD).
- **Localhost, saída V1 do relay → TD:** portas 6001–6004 (painéis 1–4 no
  server-a; 5–8 mapeiam para 6001–6004 no server-b). 4 UDP In DATs por
  servidor, `Network Address: 127.0.0.1`.
- **OSC pro Max:** relay → :7500 (endereço do host do Max no
  `config_server.yaml`).
- Rede cabeada gigabit dedicada ou VLAN isolada. Wi-Fi vetado.
- DHCP com **reserva por MAC** (imagem SD idêntica; só `config.yaml` difere).
- Firewall dos servidores Windows: liberar UDP 5555 entrada (as portas
  600x são localhost, não precisam de regra).
- Banda: ~16 kB/s por nó a 30 Hz — irrelevante; o requisito é estabilidade.

## 5. Escopo de código — dois workstreams de Claude Code

Desenvolvimento em paralelo, repositórios/pastas separados:

- **W1 — Nó Pi** (`lidarmapper-node`): porte V2 + parsing vetorizado.
- **W2 — Middleware de servidor** (`lidarmapper-server`): evolução do
  LidarMapper single-node ("zip") em relay + calibrador multi-painel.

O contrato entre os dois é o §3 (V2 na entrada do relay) — os workstreams
só se encontram nesse formato de bytes, então podem andar em paralelo com
o `test_udp_receiver.py` de cada lado validando o contrato.

**Regra de ouro do monorepo (§14): todo pack/unpack de V1 e V2 vive em
`shared/protocol.py`, importado pelos dois lados.** Nenhum struct format
string duplicado em node/ ou server/ — é o que garante que o simulador,
o Pi real e o relay nunca divirjam em bytes.

### 5.0 Parsing serial vetorizado — REQUISITO do W1 (não fallback)

O gargalo real do nó não é rede nem UDP (30 pacotes/s, já em batch): é o
**parsing das ~32k amostras/s do S3**, que o `rplidar-roboticia` faz em
Python puro, byte a byte. No 3B+ (1× Cortex-A53 fraco por core) isso pode
saturar sozinho. A versão vetorizada vira parte do porte V2:

- Ler a serial em **blocos grandes** (`serial.read(4096)` ou maior), nunca
  amostra a amostra.
- Acumular num buffer e desempacotar por frame com
  **`numpy.frombuffer` + operações vetorizadas** (máscaras de bits para
  quality/ângulo/distância em arrays, sem loop Python por amostra).
- Alternativa equivalente: `struct.iter_unpack` sobre `memoryview` — usar
  numpy se o downstream (ROI, DBSCAN) já consome arrays, que é o caso.
- O ROI em mm (§9) aplica-se **como máscara numpy no array de amostras**,
  antes de qualquer objeto Python ser criado — reduz drasticamente o que
  chega ao DBSCAN.
- Meta de orçamento: parsing + filtros ≤ 30% de um core **no 3B+**; DBSCAN
  + tracking + publish no restante do critério do §10.

Se o `rplidar-roboticia` não expor o stream cru de forma utilizável,
substituir a camada de leitura por implementação própria do protocolo do
S3 (dense/express scan) — é um formato binário simples e documentado, e o
controle total sobre o buffer é justamente o que permite vetorizar.

### 5.1 W1 — Nó Pi (base: código do zip, enxugado)

1. **`publisher.py`** — `pack_frame` V2: header `"<BBIdH"` com
   `version=2` e `panel_id` vindo do config. `unpack_frame` correspondente
   (o `test_udp_receiver.py` usa).
2. **`main.py`** — remover o passo de homografia do pipeline; tracker
   publica direto em mm. Remover dependência de `calibration.json`
   (o indicador "Calib" morre no contexto do nó). Integrar o reader
   vetorizado do §5.0 no lugar do loop amostra-a-amostra.
3. **`config.py` / `config.yaml`** — novo campo `udp.panel_id: 1..8`
   (obrigatório, validado, sem default silencioso).
4. **`requirements-pi.txt`** — mínimo: `rplidar-roboticia`, `numpy`,
   `pyyaml`, `ruamel.yaml`. Sem pygame/customtkinter nos nós.
5. **`test_udp_receiver.py`** — decode V2, mostrar `panel_id` e alertar
   version mismatch.
6. **`test_e2e.py`** — atualizar asserts pro formato V2; vira o smoke test
   do ambiente ARM (roda sem hardware).

O que NÃO vai pros nós: `ui.py`, `calibrate.py`, `test_viz.py`,
`test_tracker.py`, `test_calib.py`. Continuam úteis na bancada (sensor no
notebook) para ajustar ROI/offset/mirror antes da instalação.

### 5.2 W2 — Middleware de servidor (evolução do zip)

Reusa intacto: `homography.py` (DLT/SVD, save/load), a UX do
`calibrate.py` (alvos, insets ~0.06/0.94, ordem TL→TR→BR→BL, mediana de
~2 s), o callback V1 do `TOUCHDESIGNER.md`, e a estrutura do `ui.py`.

7. **`server_relay.py`** (novo; nasce de um `main.py` sem sensor) — loop:
   socket V2 :5555 → valida version/tamanho → demux `panel_id` → aplica
   `H` do `calib_pN.json` → descarta fora de 0..1 → detecta down/up
   (§3.2) → envia OSC `/touch/N` ao Max → reempacota V1 → sendto
   `127.0.0.1:600N`. Sem calibração de um painel, repassa nada dele (e
   loga uma vez). Hot-reload dos JSONs via mtime ou sinal. Roda como
   serviço (NSSM/Task Scheduler).
8. **`calibrate.py` multi-painel, duas fontes de alvo** — ver §7. Ganha
   `--panel N` e `--target-source {local,td}`; a fonte de pontos passa a
   ser o UDP V2 filtrado por `panel_id == N` (não mais a serial).
9. **`config_server.yaml`** (novo) — `listen_port: 5555`, mapa
   `panel_id → {out_port, display_index, calib_file}`, `osc: {host, port:
   7500}`, parâmetros de coleta da calibração.
10. **`ui.py` → painel do servidor** — status por nó (idade do último
    pacote por `panel_id`), estado das 4 calibrações (mtime), botões
    "Calibrate painel N", start/stop do relay, log. A infra de
    subprocess + indicadores do zip é a base; muda o conteúdo.
11. **`test_udp_receiver.py`** — modos `--v2` (entrada dos Pis) e `--v1`
    (saída pro TD), pra validar os dois lados do relay isoladamente.
12. **Simulador de nó** (`test_node_sim.py`, novo) — gera V2 sintético
    (círculos, toques fake) pros 4 panel_ids; desenvolve e testa o relay
    e a calibração **sem nenhum Pi ligado**. Análogo ao simulador de
    loopback que usamos no patch do Max.

### 5.3 No TD (mínimo)

13. **4× UDP In DAT** (:6001–6004, `127.0.0.1`, Format Binary) + o
    callback V1 do `TOUCHDESIGNER.md` do zip, um Table DAT
    `touches_p1..p4` por painel. Nada de homografia, nada de demux.

## 6. Setup do nó (3B+ / 4 / 5)

Raspberry Pi OS **Lite 64-bit (arm64)**, headless — **a mesma imagem serve
os três modelos**, o que preserva a golden image única (§11).

**Alimentação e térmica por modelo:**

| Modelo | Fonte oficial            | Conector | Térmica (operação de horas)        |
|--------|--------------------------|----------|------------------------------------|
| 3B+    | 5V / 2,5 A               | microUSB | dissipador passivo basta           |
| 4      | 5V / 3 A                 | USB-C    | case ventilado ou dissipador; case fechado sem dissipador faz throttle |
| 5      | 5V / 5 A (PSU oficial)   | USB-C    | active cooler recomendado          |

Em todos: o S3 puxa ~1,5 W do USB; subtensão derruba o CP210x no meio do
show — fonte oficial não é opcional, e no 3B+ (margem menor de corrente)
é onde o risco de subtensão com o S3 pendurado é maior. Verificar
`vcgencmd get_throttled` no bring-up (deve ser `0x0`).

**Cortes de sistema (importam de verdade no 3B+):** em `config.txt`,
desabilitar o que o nó não usa — `dtoverlay=disable-wifi`,
`dtoverlay=disable-bt` — e não conectar display (headless já evita o
compositor). Swap desativado (`sudo systemctl disable dphys-swapfile`).

```bash
sudo apt update && sudo apt full-upgrade -y
sudo apt install -y git python3-venv chrony
sudo usermod -aG dialout $USER    # relogar

git clone <repo> ~/lidarmapper && cd ~/lidarmapper
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-pi.txt
```

**udev — nome estável do S3** (`/etc/udev/rules.d/99-rplidar.rules`):

```
SUBSYSTEM=="tty", ATTRS{idVendor}=="10c4", ATTRS{idProduct}=="ea60", SYMLINK+="rplidar", MODE="0666"
```

**chrony** — todos os Pis sincronizam com o server-a
(`server 10.10.0.10 iburst prefer`). Sem NTP, o `timestamp` do header é
inútil entre nós e qualquer medição de latência no TD mente.

**Serviço systemd** (`/etc/systemd/system/lidarmapper.service`):

```ini
[Unit]
Description=LidarMapper node (LIDAR -> UDP V2)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=pi
WorkingDirectory=/home/pi/lidarmapper
ExecStart=/home/pi/lidarmapper/.venv/bin/python main.py
Restart=always
RestartSec=3
ExecStartPre=/bin/sleep 5
Nice=-10

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now lidarmapper
journalctl -u lidarmapper -f
```

**Baseline no boot:** o `BackgroundSubtractor` captura o fundo no start do
serviço. Com `Restart=always`, uma queda de energia religa sozinho — se
houver público parado na frente nesse instante, vira "fundo". Mínimo viável
de rebaseline remoto: `ssh lidar-0N sudo systemctl restart lidarmapper`.
Fase 2: endpoint HTTP `POST /rebaseline` (padrão FastAPI do
ivl-dice-controller).

## 7. Calibração server-side — duas fontes de alvo

A decisão de vídeo (4 outputs diretos da GPU vs. processador
Novastar/Colorlight com canvas único do TD) **ainda está aberta**. Para
não bloquear o W2, o `calibrate.py` separa dois papéis que no zip eram um
só:

- **Coletor** (sempre igual): escuta o UDP V2, filtra `panel_id == N`,
  coleta ~2 s de pacotes por canto, guarda a mediana do centróide em mm,
  roda o DLT/SVD do `homography.py`, salva `calib_pN.json`, reporta erro
  de reprojeção em mm. É o coração — independe de quem desenha.
- **Fonte de alvo** (plugável, `--target-source`):
  - **`local`** — pygame fullscreen no `display_index` mapeado ao painel
    N (o fluxo do zip). Requer saída de vídeo direta e o TD fora desse
    output durante a calibração.
  - **`td`** — o TD desenha os alvos (COMP simples com 4 círculos nos
    insets, controlado por OSC ou Table DAT: "painel N, alvo k aceso").
    O calibrate.py apenas comanda qual alvo acender e coleta. Funciona
    com qualquer cadeia de vídeo, inclusive processador.

Fluxo do operador (idêntico nas duas fontes, e idêntico ao zip):

1. Alvo 1 (TL) acende no painel N.
2. Operador toca; coletor junta ~2 s de pacotes e trava a mediana.
3. Repete TR → BR → BL.
4. DLT/SVD → `calib_pN.json` + erro de reprojeção.
5. Relay recarrega (hot-reload por mtime — sem reiniciar nada).

Manter os insets ~0.06/0.94 do zip. A calibração pode rodar com o relay
ativo (portas distintas; o coletor abre socket próprio em modo
`SO_REUSEADDR` **ou** o relay ganha um modo "espelhar painel N pro
coletor" — decidir na implementação, o mais simples vence).

Precisão de dedo vs. LIDAR: o alvo visual deve pedir toque com a mão
espalmada ou objeto (≥ `dbscan_min_samples: 4` pontos no raio de 150 mm) —
um dedo fino a 3 m pode não formar cluster.

## 8. TD: consumo V1 (callback do zip, sem mudanças)

Por servidor: **4× UDP In DAT** (`Network Address: 127.0.0.1`, portas
6001–6004, `Format: Binary`), cada um com o **callback V1 do
`TOUCHDESIGNER.md` do zip, inalterado**, escrevendo na sua Table DAT
(`touches_p1..touches_p4`, header `id x y confidence`).

```python
# udp_callback_v1.py — idêntico ao zip; só o nome da tabela muda por DAT
import struct

_HEADER = struct.Struct("<IdH")     # frame, timestamp, num_points  = 14 B
_POINT  = struct.Struct("<Ifff")    # id, x, y, confidence          = 16 B

TABLE = 'touches_p1'                # p2/p3/p4 nos outros DATs

def onReceive(dat, rowIndex, message, bytes_, peer):
    buf = bytes_ if bytes_ else (message.encode('latin-1') if message else None)
    if not buf or len(buf) < _HEADER.size:
        return
    frame, ts, n = _HEADER.unpack_from(buf, 0)
    if len(buf) != _HEADER.size + n * _POINT.size:
        return
    table = op(TABLE)
    table.clear(keepFirstRow=True)
    for i in range(n):
        pid, x, y, conf = _POINT.unpack_from(buf, _HEADER.size + i * _POINT.size)
        table.appendRow([pid, x, y, conf])
```

Sem homografia, sem demux, sem carregamento de calibração — tudo isso
mora no relay (§5.2). Os padrões do `TOUCHDESIGNER.md` (Replicator por
`id`, conversão pra pixels, Filter CHOP) valem como estão.

**Monitor de saúde por painel:** agora vive no relay/ui do servidor
(§5.2, item 10) — idade do último pacote V2 por `panel_id`. No TD, um
fallback simples: se a `touches_pN` não muda há >1 s com o relay vivo, o
problema é o nó.

**IDs de track:** únicos por nó; como cada painel tem porta e tabela
próprias, não há colisão. Se algum efeito global precisar de ID único
entre painéis: `N * 100000 + id` ao consumir.

## 9. Config por nó — diff típico

```yaml
sensor:
  port: /dev/rplidar        # symlink udev; autodetect segue como fallback

roi:                        # POR PAINEL, em mm — 1ª linha de defesa contra
  x_min: -1400              # o LIDAR enxergar o público do painel vizinho
  x_max:  1400
  y_min:   100
  y_max:  2100

processing:
  angle_offset_deg: 0.0     # por montagem
  mirror: false             # por montagem

udp:
  host: "10.10.0.10"        # server-a (nós 1-4) ou server-b (nós 5-8)
  port: 5555                # igual em todos
  panel_id: 3               # ÚNICO por nó — o único campo realmente distinto
  publish_rate_hz: 30
```

## 10. Gate de CPU — worst case é o 3B+

Lógica revisada: não é mais "reprovar no Pi 4 → subir pro Pi 5", e sim
**"otimizar até caber no 3B+"** — o objetivo é aproveitar os 3B+ do
estúdio. O parsing vetorizado (§5.0) entra ANTES do gate, não como plano B.

O S3 entrega até ~32k amostras/s. Em Python puro isso pode saturar um core
do 3B+; vetorizado com numpy, a expectativa é folga confortável.

Teste de bancada (com o pipeline V2 + §5.0 já implementados):

```bash
python test_lidar.py     # meas/s igual ao baseline do Windows?
htop                     # CPU do processo
python main.py           # pipeline completo: pub/s estável, fg=N responsivo
vcgencmd get_throttled   # 0x0 sob carga
```

**Critério final (no 3B+):** meas/s nominal e `main.py` < 70% de um core,
30 Hz de publish estáveis, sem throttle térmico após 1 h.

**Gate provisório no Pi 5** (único hardware em mãos hoje): o single-core
do Pi 5 é ~4–5× o do 3B+, então o resultado precisa de margem de escala:

| `main.py` no Pi 5 (1 core) | Leitura                                    |
|----------------------------|--------------------------------------------|
| < 12%                      | quase certo que cabe no 3B+ — seguir       |
| 12–20%                     | zona cinzenta — validar em 1× 3B+ real     |
| > 20%                      | otimizar mais antes de escalar             |

Como os 3B+ já existem no estúdio, o gate definitivo é barato: repetir o
teste **em um 3B+ real** antes do bring-up em escala (item do §13). O Pi 5
serve para desenvolver e ter leitura antecipada, não para aprovar.

Interferência entre S3 vizinhos coplanares: possível, mas na prática vira
ruído esporádico que `min_quality` + `dbscan_min_samples: 4` descartam.
Observar no bring-up do segundo painel.

## 11. Replicação (golden image)

Com V2 a clonagem ficou mais simples — o Pi não carrega calibração — e a
**frota mista (3B+/4/5) usa a mesma imagem arm64**: o firmware certo por
modelo já vive na partição de boot da imagem oficial, sem ajuste manual.

1. Configurar **lidar-01** completo (§6) e validar na bancada.
2. Clonar o SD. Gravar 7 cópias.
3. Por nó, mudar apenas: hostname (`lidar-0N`), reserva DHCP por MAC,
   e no `config.yaml`: `udp.host`, `udp.panel_id`, `roi`,
   `angle_offset_deg`, `mirror`.

Única ressalva da frota mista: os overlays de `config.txt` usados (§6 —
disable-wifi/bt) valem para os três modelos; evitar overlays específicos
de um modelo na golden image.

Atualização de código em produção:

```bash
for h in lidar-0{1..8}; do ssh $h 'cd lidarmapper && git pull && sudo systemctl restart lidarmapper'; done
```

## 12. Checklist de bring-up por painel

1. [ ] Pi na rede, hostname/IP corretos (`ping lidar-0N`)
2. [ ] `chronyc tracking` sincronizado com server-a
3. [ ] `ls -l /dev/rplidar` ok
4. [ ] `test_e2e.py` passa (ambiente ARM ok, sem hardware)
5. [ ] `test_lidar.py` → meas/s nominal, CPU no critério (§10),
       `vcgencmd get_throttled` = 0x0
6. [ ] LIDAR montado, nivelado no plano do painel, altura definida
7. [ ] `config.yaml`: host, **panel_id**, ROI, offset, mirror
8. [ ] `systemctl enable --now lidarmapper`, baseline com área livre
9. [ ] `test_udp_receiver.py --v2` no servidor → pacotes V2 com panel_id
       certo
10. [ ] Calibração (§7), erro de reprojeção aceitável; relay recarregou
11. [ ] `test_udp_receiver.py --v1` na porta 600N → 0..1 coerentes
12. [ ] TD: `touches_pN` populando, cursor segue a mão
13. [ ] Toque dispara `/touch/N` no Max (som do cubo certo)
14. [ ] Tocar os 4 cantos → partículas respondem no lugar certo
15. [ ] Teste de reboot: cortar energia do Pi → volta sozinho; relay
        segue de pé no servidor

## 13. Ordem de execução

W1 e W2 andam em paralelo — o simulador de nó (item 12 do §5.2) permite
o W2 avançar sem hardware.

**W1 — nó Pi (Claude Code):**
1. Porte V2 + parsing vetorizado (§5.0/§5.1), ~1–1,5 dia.
2. Gate provisório no Pi 5 (tabela do §10).
3. Gate definitivo em 1× 3B+ do estúdio — aprova a frota.

**W2 — middleware de servidor (Claude Code):**
4. `server_relay.py` + `config_server.yaml` + `test_node_sim.py`
   (~1 dia; validável 100% com o simulador).
5. `calibrate.py` multi-painel com as duas fontes de alvo (§7)
   (~1 dia; fonte `local` testável na bancada com monitor; fonte `td`
   valida quando a cadeia de vídeo estiver definida).
6. `ui.py` → painel do servidor (~meio dia, pode ser depois do primeiro
   bring-up).

**Integração:**
7. TD: 4 UDP In DATs + callback V1 do zip (minutos, é o setup do
   `TOUCHDESIGNER.md`).
8. Ponta a ponta com 1 painel real: Pi (3B+ de preferência) → relay →
   calibração → TD + Max. Validação cruzada da homografia contra o
   `calibrate.py` original no mesmo setup físico.
9. **Golden image** do lidar-01 validado.
10. Clonar + bring-up painel a painel pelo checklist.
11. **Fase 2:** endpoint `/status` + `/rebaseline` FastAPI por nó e
    dashboard central no ui do servidor.

## 14. Organização do repositório e processo de desenvolvimento

**Monorepo único**, dois workstreams em pastas separadas, protocolo
compartilhado:

```
lidarmapper/
├── GUIA_LIDARMAPPER_DISTRIBUIDO.md   # este spec — fonte de verdade
├── CLAUDE.md                          # regras pro Claude Code (curto)
├── shared/
│   └── protocol.py                    # pack/unpack V1 e V2 — ÚNICA fonte
│                                      # (structs "<BBIdH", "<IdH", "<Ifff")
├── node/                              # W1 — roda no Raspberry Pi
│   ├── main.py                        # pipeline: reader→proc→tracker→pub
│   ├── lidar_reader.py                # + parsing vetorizado (§5.0)
│   ├── processing.py
│   ├── tracker.py
│   ├── publisher.py                   # importa shared/protocol
│   ├── config.py / config.yaml
│   ├── requirements-pi.txt
│   └── test_e2e.py, test_lidar.py
└── server/                            # W2 — roda no Windows do servidor
    ├── server_relay.py                # V2 in → H → V1 out + OSC (§5.2)
    ├── calibrate.py                   # multi-painel, 2 fontes de alvo (§7)
    ├── homography.py                  # DLT/SVD do zip, intacto
    ├── ui.py                          # painel do servidor
    ├── config_server.yaml
    ├── requirements-server.txt
    ├── test_node_sim.py               # simulador V2 — dev sem Pi
    └── test_udp_receiver.py           # modos --v1 / --v2
```

Regras que valem em qualquer sessão de Claude Code (vão no `CLAUDE.md`):

1. **Nenhum format string de struct fora de `shared/protocol.py`.**
   node/ e server/ importam; test_node_sim idem — fidelidade por
   construção.
2. **Cada sessão trabalha em um workstream só** e não toca na pasta do
   outro (shared/ é editável apenas se o §3 mudar — e o §3 não muda sem
   atualização do guia).
3. **node/ não pode importar pygame/customtkinter/tkinter** (não existem
   nos nós). server/ pode tudo.
4. O guia (§3) é o contrato: em dúvida de formato, o guia vence o código.

**Ordem das sessões:** W2 primeiro (100% testável com `test_node_sim.py`,
sem hardware; deixa o receptor pronto), W1 depois (valida contra o
receptor real). Gates de CPU do §10 após o W1.

**Origem do código:** a base é o repositório fonte do LidarMapper
single-node (o "zip" é o build PyInstaller dele — o .exe não se edita).
Os `.py` originais são copiados para node/ e server/ conforme a tabela do
§2 e adaptados. O single-node continua existindo como ferramenta de
bancada (ajuste de ROI/mirror com o sensor no notebook).
