# Manual de Campo — LidarMapper Distribuído v3

**Comece por aqui.** Este é o documento operacional do sistema instalado:
o que ele é, como operar no dia a dia, como diagnosticar e como refazer
qualquer parte da instalação. Foi escrito depois da primeira temporada de
evento (08/2026), consolidando tudo o que aconteceu em campo — trocas de
servidor, quedas de energia, sensores travados, redes hostis.

Público: o operador do evento, a próxima pessoa que herdar o sistema, ou a
próxima IA que abrir este repo. Não assume que você participou da instalação.

Onde está cada verdade:

| Assunto | Documento |
|---|---|
| **Operação, troubleshooting, runbooks** | este manual |
| Protocolos V1/V2/OSC (formato dos bytes) | [GUIA §3](../GUIA_LIDARMAPPER_DISTRIBUIDO_1.md) (normativo) + [shared/protocol.py](../shared/protocol.py) (canônico em código) |
| Regras para trabalhar no código | [CLAUDE.md](../CLAUDE.md) |
| Instalar um nó Pi do zero | [INSTALACAO_PI.md](INSTALACAO_PI.md) |
| Instalar o servidor Windows | [INSTALACAO_SERVIDOR.md](INSTALACAO_SERVIDOR.md) + [OPERACAO_EVENTO_WINDOWS.md](OPERACAO_EVENTO_WINDOWS.md) |
| Configurar o TouchDesigner | [INSTALACAO_TOUCHDESIGNER.md](INSTALACAO_TOUCHDESIGNER.md) |

---

## 1. O que o sistema é

8 painéis LED, cada um com um sensor RPLIDAR S3 + Raspberry Pi na frente,
virando uma superfície de toque. Um servidor Windows central recebe tudo,
converte para coordenadas de tela e alimenta o TouchDesigner (visual) e o
Max/MSP (som).

```
 8× [ S3 → Pi (node/main.py) ]                  1× Windows                      saídas
     lê o sensor, subtrai fundo,          server/fleet_bridge.py
     rastreia mãos/pessoas          UDP   grade de status + radar +      OSC :7000 → TouchDesigner
     e publica cursores em mm      :5555  calibração + homografia   →    OSC :7500 → Max/MSP
     (protocolo V2, panel_id N)     (V2)  por painel (calib_pN.json)
```

Três decisões definem tudo:

1. **O Pi é um sender burro.** Publica cursores em milímetros no referencial
   do sensor. Não conhece calibração nem sabe onde o painel está. Config vivo:
   `/home/pi/node-config.yaml` (fora do git).
2. **A calibração vive só no servidor.** Um `calib_pN.json` por painel em
   `server/`, com hot-reload por mtime — recalibrar não derruba nada.
3. **Boot não assistido.** Tudo volta sozinho de queda de energia: os Pis
   sobem por systemd, o S3 travado se auto-cura (auto-RESET no reader), o
   Windows abre o `start_fleet.bat` no auto-start.

### O fleet_bridge (a central de operação)

`server/fleet_bridge.py` é UM processo com duas telas:

- **GRADE**: um cartão por painel — bolinha ON/OFF (idade do último pacote),
  taxa de entrada, estado da calibração e minimapa dos 2 toques.
- **RADAR de um painel**: a nuvem do sensor em mm, a ROI e os cursores ao
  vivo — é onde se calibra e se diagnostica um painel.

Saídas simultâneas: OSC pro TD (porta 7000) e pro Max (porta 7500) — ver §3.

**Regra de ouro: só um programa pode ocupar a porta 5555 por vez.** Feche o
fleet_bridge antes de abrir `radar_view.py`, `server_relay.py` ou
`calibrate.py` (e vice-versa).

---

## 2. Mapa da frota (TABELA CANÔNICA)

O `panel_id` segue a **posição física da tela**, não o número do hostname.
Esta tabela é a referência oficial — `server/fleet_bridge.py` e
`deploy/baseline.ps1` carregam esse mapa em código.

| Painel | Hostname | Modelo | MAC eth0 | Observações de campo |
|---|---|---|---|---|
| **p1** | lidar-01 | Pi 4 B r1.5 | `d8:3a:dd:9c:22:21` | subtensão só no boot (ok) |
| **p2** | lidar-03 | Pi 4 B r1.5 | `88:a2:9e:70:ea:53` | fantasma a 1,8 m resolvido com baseline 10 s |
| **p3** | lidar-08 | **Pi 5** r1.0 | `2c:cf:67:4c:82:5e` | `usb_max_current_enable=1` + fonte auxiliar no S3; **exige fonte 27 W** |
| **p4** | lidar-06 | **Pi 3B** r1.2 | `b8:27:eb:d1:86:93` | CPU medida: 6,8 %/core ✅; fonte fraca (dips); S3 já travou 1× |
| **p5** | lidar-07 | Pi 3B r1.2 | `b8:27:eb:92:88:1e` | inclinação corrigida com calço; **some do mDNS às vezes** — usar IP |
| **p6** | lidar-02 | Pi 4 B r1.5 | `d8:3a:dd:9c:1f:06` | S3 já travou 1× |
| **p7** | lidar-04 | Pi 4 B r1.5 | `88:a2:9e:70:ea:3b` | era rotulada "tela 5" antes da troca; inclinação corrigida com calço |
| **p8** | lidar-05 | Pi 3B r1.2 | `b8:27:eb:a8:e0:e8` | "boot falho" uma vez era só cabo de rede |

Config de frota aplicado em todos (`/home/pi/node-config.yaml`):
`angle_offset_deg: 180` (a frente física fica ~270° no referencial cru),
`roi.y_max: 4000` (mm), `baseline.duration_s: 10`.

Acesso: usuário `pi`, senha `pi123`, SSH por chave (as máquinas de operação
têm a chave instalada — ver [OPERACAO_EVENTO_WINDOWS.md §2](OPERACAO_EVENTO_WINDOWS.md)).
Nomes mDNS: `lidar-0N.local`.

Rede do evento: roteador com DHCP na faixa `192.168.1.x`. O IP do servidor
**deve ter reserva DHCP por MAC no roteador** — sem isso, o IP muda num
reboot e os 8 nós ficam publicando para o vazio (aconteceu; ver R2). Reservar
também os 8 Pis evita caça a IP quando o mDNS falha.

---

## 3. Operação normal

### Subir o show

No servidor Windows (`C:\lidarmapper`): clique no `start_fleet.bat`, ou:

```powershell
.venv\Scripts\python server\fleet_bridge.py --panels 1,2,3,4,5,6,7,8 --dest 127.0.0.1
```

`--dest` = IP(s) do TouchDesigner, separados por vírgula (`127.0.0.1` se o TD
roda na mesma máquina). Critério de sucesso: **8 cartões verdes com
`in=30/s`** em até 30 segundos.

### Teclas do fleet_bridge

| Tela | Tecla | Ação |
|---|---|---|
| Grade | `1`–`8` ou clique no cartão | abre o radar daquele painel |
| Grade | `A` | refaz o baseline de TODOS os nós (área de TODAS as telas livre!) |
| Grade | `Q` / `ESC` | sai |
| Radar | `1` `2` `3` `4` | captura canto TL / TR / BR / BL (mão parada ~2 s) |
| Radar | `S` | valida e salva `calib_pN.json` (hot-reload — vale na hora) |
| Radar | `X` | descarta os cantos capturados |
| Radar | `B` | refaz o baseline daquele nó via SSH (área da tela livre!) |
| Radar | `ESC` | volta pra grade |

### Contrato com o TouchDesigner

OSC In CHOP, porta **7000**. 6 canais por painel, 48 no total:

```
/pN_x1  /pN_y1  /pN_active1  /pN_x2  /pN_y2  /pN_active2      (N = 1..8)
```

- Coordenadas `0..1`, **origem embaixo-esquerda**, 30 Hz por painel.
- `active` = 1 enquanto há toque; ao soltar vai a 0 mas **x/y seguram o
  último valor** (sem salto pra origem).
- 2 slots estáveis por painel: um toque que continua não muda de slot.

### Contrato com o Max/MSP

OSC na porta **7500** (destino `--max-dest`, default `127.0.0.1`):

```
/touch/N 1      quando o painel N passa a ter alguém tocando
/touch/N 0      quando o último toque do painel N termina
```

Só transições — não é stream contínuo. Desligável com `--no-max`.

### A disciplina do baseline (a regra mais importante da operação)

Todo start/restart do serviço `lidarmapper` de um nó recaptura o "fundo" do
sensor por **10 s**. O que estiver PARADO na frente da tela nesse momento
vira fundo — e toque naquela região morre até o próximo baseline.

- Restart/baseline ⇒ **área da tela livre por ~15 s**. Gente circulando é
  tolerável; gente parada, não.
- Sintoma de baseline poluído: toque morto numa região, ou `fg=0` no journal
  com alguém na frente do sensor.
- Cura: refazer o baseline daquele painel com a área livre (R5).

---

## 4. Runbooks

### R1 — Instalar/trocar o servidor Windows

Pré-requisitos: Python 3.11+ instalado ("Add to PATH" marcado) e **internet**
na máquina (o pip baixa as dependências).

1. Leve o repo: zip da máquina anterior descompactado em `C:\lidarmapper`,
   ou `git clone` se o roteador tiver internet.
2. **Se o zip veio com `.venv\` de outra máquina, apague antes de tudo** —
   venv não é portátil (os paths do Python ficam gravados dentro). No CMD:
   `rmdir /s /q .venv` (no PowerShell: `Remove-Item -Recurse -Force .venv`).
3. Um comando faz o resto:

   ```powershell
   cd C:\lidarmapper
   powershell -ExecutionPolicy Bypass -File deploy\install_server.ps1
   ```

   Cria o venv, instala dependências, roda `w2_validate.py` (12/12 = ok),
   libera o firewall (UDP 5555/7000), gera o `start_fleet.bat` (+ auto-start
   opcional) e oferece gerar/instalar a chave SSH nos 8 nós.
4. **Fixe o IP da máquina por MAC no roteador** (reserva DHCP).
5. Re-aponte os 8 nós para o IP novo (R2).
6. `start_fleet.bat` → 8 cartões verdes.

**Sem internet na máquina** (pip falha com `getaddrinfo failed`): ou conecte
um hotspot de celular só para a instalação, ou gere um bundle offline em
outra máquina com internet:
`pip download -r server/requirements-server.txt -d wheels --platform win_amd64 --only-binary=:all: --python-version <3XX>`
e instale com `pip install --no-index --find-links wheels -r server\requirements-server.txt`.

### R2 — Re-apontar os 8 nós para um IP de servidor novo

Cada nó envia o V2 para UM IP (`udp.host` no `node-config.yaml`). Servidor
mudou de IP ⇒ os 8 precisam ser re-apontados. O `--update` preserva
ROI/offset/baseline; **o número do painel vem da tabela do §2 e nunca muda**.

Do Mac/Linux (bash) — troque só o `NEW`:

```bash
NEW=192.168.1.XXX
for pair in "1:lidar-01" "2:lidar-03" "3:lidar-08" "4:lidar-06" \
            "5:lidar-07" "6:lidar-02" "7:lidar-04" "8:lidar-05"; do
  p=${pair%%:*}; h=${pair##*:}
  ssh -o ConnectTimeout=6 pi@$h.local \
    "~/lidarmapper/.venv/bin/python ~/lidarmapper/deploy/render_node_config.py \
       --panel $p --udp-host $NEW --update --out /home/pi/node-config.yaml \
     && sudo systemctl restart lidarmapper && echo REAPONTADO"
done
```

Do Windows (PowerShell) — mesmo efeito:

```powershell
$IP = "192.168.1.XXX"
$Mapa = @{ "1"="lidar-01"; "2"="lidar-03"; "3"="lidar-08"; "4"="lidar-06";
           "5"="lidar-07"; "6"="lidar-02"; "7"="lidar-04"; "8"="lidar-05" }
foreach ($p in 1..8) {
  $h = $Mapa["$p"]
  ssh -o ConnectTimeout=8 "pi@$h.local" "~/lidarmapper/.venv/bin/python ~/lidarmapper/deploy/render_node_config.py --panel $p --udp-host $IP --update --out /home/pi/node-config.yaml && sudo systemctl restart lidarmapper && echo REAPONTADO"
}
```

Cada nó responde `OK ... host=<IP novo>` e `REAPONTADO`. Os restarts refazem
o baseline ⇒ **áreas das telas livres** durante o loop. Se um `.local` não
resolver, use o IP do nó (R4) mantendo o `--panel` certo.

### R3 — Health-check da frota (read-only, não interfere no show)

Nenhum restart — pode rodar com o evento aberto:

```bash
for pair in "1:lidar-01" "2:lidar-03" "3:lidar-08" "4:lidar-06" \
            "5:lidar-07" "6:lidar-02" "7:lidar-04" "8:lidar-05"; do
  p=${pair%%:*}; h=${pair##*:}
  echo "=== painel $p ($h) ==="
  ssh -o BatchMode=yes -o ConnectTimeout=6 pi@$h.local '
    echo "servico: $(systemctl is-active lidarmapper)  restarts: $(systemctl show lidarmapper -p NRestarts --value)"
    echo "sensor:  $(test -e /dev/rplidar && echo OK || echo AUSENTE)"
    echo "energia: $(vcgencmd get_throttled)"
    journalctl -u lidarmapper -n 1 --no-pager -o cat
  ' 2>&1 || echo "OFFLINE"
done
```

Como ler a linha do journal (impressa 1×/s por cada nó):

```
meas/s= 3500  scans/s= 9.8  fg= 0  tracks= 0  pub/s= 30.0  desync=0  recon=0
```

| Campo | Saudável | Significado / quando preocupar |
|---|---|---|
| `meas/s` | 3000–9000 | amostras do S3 por segundo. **0 = sensor mudo** (ver troubleshooting) |
| `scans/s` | ~9,8 | rotações do sensor. **0.0 com meas/s>0 = versão antiga do reader**; 0.0 com meas/s=0 = sensor mudo |
| `fg` | 0 com área livre | pontos acima do fundo. >0 constante com área livre = fantasma ⇒ baseline |
| `tracks` | 0 com área livre | pessoas/mãos rastreadas agora |
| `pub/s` | ~30 | frames V2 enviados ao servidor |
| `desync` | 0 ou estável | ressincronizações do serial. **Crescendo continuamente** = cabo USB/energia |
| `vcgencmd get_throttled` | `0x0` | `0x50000` = subtensão **no passado** (ok após dia de quedas); `0x50005` ou bits baixos ≠ 0 = subtensão **AGORA** ⇒ fonte/cabo |

### R4 — Achar os Pis na rede (quando os nomes falham)

Ordem de tentativa:

1. **mDNS**: `ping lidar-0N.local`. Atenção: `dscacheutil` pode responder um
   IP **de cache, morto** — sempre confirme com ping.
2. **Varredura da faixa + ARP pelos MACs da tabela do §2** (é o método que
   sempre funciona):

   ```bash
   for i in $(seq 1 254); do ping -c 1 -W 200 192.168.1.$i >/dev/null 2>&1 & done; wait
   arp -an | grep -iE "(d8:3a:dd|b8:27:eb|2c:cf:67|88:a2:9e)"
   ```

   Cada linha `(IP) at MAC` identifica um Pi — cruze o MAC com a tabela.
   MAC ausente = o nó **não está na rede** (energia/cabo — problema físico).
3. **IPv6 link-local** (funciona mesmo com a faixa IPv4 errada/desconhecida):

   ```bash
   ping6 -c 3 -I en0 ff02::1     # multicast "todos os nós" na interface
   ndp -an | grep -iE "(d8:3a:dd|b8:27:eb|2c:cf:67|88:a2:9e)"
   ssh pi@fe80::XXXX%en0         # dá para entrar direto pelo link-local
   ```

Armadilhas de rede já vividas:

- **Wi-Fi isolado do cabeado**: roteadores de evento costumam isolar os
  clientes Wi-Fi da LAN cabeada. Notebook no Wi-Fi "não vê" nenhum Pi ⇒
  **plugue um cabo** no switch.
- O `lidar-07` (p5) some do mDNS de tempos em tempos — use o IP dele.

### R5 — Refazer o baseline ("calibragem de espaço vazio")

Quando: ponto fantasma, toque morto numa região, ou depois de mover qualquer
objeto na frente de uma tela. **Área da tela livre por ~15 s.**

- Pelo fleet_bridge: radar do painel → tecla `B`. Ou `A` na grade (todos).
- Pelo Windows: `powershell -ExecutionPolicy Bypass -File deploy\baseline.ps1 2`
  (painel 2) ou `... baseline.ps1 all`.
- Na unha: `ssh pi@<hostname>.local "sudo systemctl restart lidarmapper"`.

Critério de sucesso: journal do nó com `fg=0 tracks=0` com a área livre.

### R6 — Recalibrar uma tela (homografia, 4 cantos)

A calibração converte mm do sensor → `0..1` da tela. Refazer quando o toque
"escorrega" (aparece deslocado) ou depois de mover sensor/painel.

1. No fleet_bridge, abra o radar do painel (tecla do número).
2. Uma pessoa encosta a mão no **canto físico** da tela e fica parada ~2 s;
   você aperta a tecla do canto: `1`=sup-esquerdo, `2`=sup-direito,
   `3`=inf-direito, `4`=inf-esquerdo.
3. Canto ruim? Aperte o mesmo número de novo (recaptura).
4. `S` salva `server/calib_pN.json`. O hot-reload aplica na hora — teste
   tocando os 4 cantos e vendo o cursor no TD.

As 8 calibrações da instalação estão **versionadas no git** (`server/
calib_p1..8.json`) — um servidor novo já chega calibrado com o zip/clone.

### R7 — Trocar um Pi quebrado pelo reserva

**O cartão SD é a identidade do nó** — hostname, panel_id, config, serviço,
tudo. Trocar a placa é plug-and-play:

1. Mova o cartão SD, o cabo de rede e o USB do S3 para a placa nova.
2. **Mantenha a alimentação auxiliar do S3** (o USB do Pi sozinho não segura
   o motor — vale para Pi 3 e Pi 5). Fonte adequada à placa (Pi 5: 27 W).
3. Ligue. Primeiro minuto com a **área da tela livre** (baseline).
4. O MAC muda ⇒ IP novo e reserva DHCP a refazer. Funcionalmente nada muda
   (o nó publica para o servidor; o nome mDNS continua o mesmo).
5. Confirme com o health-check (R3). Config de Pi 5 no `config.txt`
   (`usb_max_current_enable=1`) é ignorada por outros modelos sem erro.

### R8 — Rotina diária do evento (gerador liga/desliga)

**Ao ligar (manhã):**

1. Roteador e switch (podem subir junto com tudo; os Pis re-tentam DHCP).
2. Pis e telas ligam com a energia. **Primeiro minuto com as áreas livres**
   (baseline). O S3 que acordar travado se auto-cura no boot (auto-RESET).
3. Servidor Windows: liga, `start_fleet.bat` (ou auto-start). TD por último.
4. Conferir: 8 cartões verdes, `in=30/s`. Cartão vermelho → R3/R4; fantasma
   → R5.

**Ao desligar (noite):** só cortar a energia. Os Pis aguentam corte seco
(risco de corrupção do SD é baixo, não zero — tenha 1–2 cartões reserva
gravados; reprovisionar leva ~8 min com internet, ver
[INSTALACAO_PI.md](INSTALACAO_PI.md)).

---

## 5. Troubleshooting por sintoma

| Sintoma | Causa | Cura |
|---|---|---|
| Cartão do painel vermelho no fleet_bridge, nó responde SSH | serviço parado ou sensor mudo | R3 no nó; `sudo systemctl restart lidarmapper` (área livre) |
| Nó não responde SSH nem aparece na varredura ARP (R4) | **físico**: sem energia ou sem cabo | LED vermelho no Pi? LED na porta do switch? Fonte, cabo, tomada |
| `meas/s=0 scans/s=0.0` com serviço `active` | S3 travou (típico após corte de energia) | `sudo systemctl restart lidarmapper` — o auto-RESET (STOP+RESET A5 40, 3 tentativas) destrava. Área livre |
| Toque morto em parte/toda a tela, `fg=0` com gente na frente | baseline poluído (alguém parado durante a captura) | R5 com a área livre |
| Ponto fantasma fixo (track que não morre) | objeto novo na cena, ou baseline velho | tirar o objeto OU R5 |
| Toque deslocado ("escorrega") | calibração desatualizada / sensor mexido | R6 |
| "Parede" a ~0,5 m no radar cobrindo a tela | sensor inclinado — o plano de varredura bate na superfície do painel | calço físico sob o sensor até a "parede" sumir do radar |
| `desync` crescendo sem parar | serial instável: cabo USB ruim ou subtensão | trocar cabo USB do S3; conferir `get_throttled` |
| `throttled=0x50000` | subtensão **no passado** (boot/queda) | ok se os bits atuais estão limpos; `0x5000**5**` ou similar AGORA ⇒ fonte |
| Pi 5 em loop de desconexão do S3 (erro de over-current) | USB do Pi 5 limitado a 600 mA sem fonte 27 W | `usb_max_current_enable=1` no config.txt + fonte auxiliar no S3 (paliativo); fonte oficial 27 W (definitivo) |
| Sistema inteiro mudo (8 cartões vermelhos, nós saudáveis) | nós apontando para IP velho do servidor | R2 — e reserva DHCP por MAC pro servidor |
| `lidar-0N.local` não resolve | mDNS falhou (acontece; p5 é reincidente) ou Windows sem Bonjour | usar IP via R4; no Windows, instalar Bonjour Print Services |
| Notebook não vê nenhum Pi | Wi-Fi isolado da LAN cabeada no roteador | cabo de rede no switch |
| `Permission denied (publickey)` com chave instalada (macOS) | ssh-agent esvaziou (chave tem passphrase) | `ssh-add --apple-load-keychain`; bloco `Host lidar-*` com `UseKeychain yes` no `~/.ssh/config` |
| pip: `getaddrinfo failed` na instalação Windows | máquina sem internet/DNS | hotspot ou bundle offline (R1) |
| `Remove-Item` não reconhecido | você está no CMD, não no PowerShell | `rmdir /s /q .venv` no CMD |
| venv copiado de outra máquina não acha o Python | venv embute paths absolutos — não é portátil | apagar `.venv` e rodar o installer (R1) |
| `install_server.ps1` com erro de parse bizarro | .ps1 com acentos: PowerShell 5.1 lê sem BOM como ANSI | os `.ps1` do repo são ASCII puro **de propósito** — manter assim |
| `w2_validate` falha no Windows com filho em erro Unicode | pipe do Windows é cp1252, mata prints com ✓/§ | já mitigado (`PYTHONUTF8=1` nos filhos); em script novo, repetir o padrão |
| Cursor 27 Hz em vez de 30 | (histórico) RateLimiter com drift | corrigido em `node/publisher.py` (deadline acumulada) — referência se regredir |

---

## 6. Cheat sheet

Do Mac/Linux (com chave SSH instalada):

```bash
# saúde de um nó, uma linha
ssh pi@lidar-03.local "systemctl is-active lidarmapper; vcgencmd get_throttled; journalctl -u lidarmapper -n 3 --no-pager -o cat"

# baseline de um nó (área livre!)
ssh pi@lidar-03.local "sudo systemctl restart lidarmapper"

# ver / editar o config vivo de um nó
ssh pi@lidar-03.local "cat /home/pi/node-config.yaml"
ssh pi@lidar-03.local "sudo sed -i 's/^  y_max: .*/  y_max: 4500.0/' /home/pi/node-config.yaml && sudo systemctl restart lidarmapper"

# achar todos os Pis (faixa 192.168.1.x)
for i in $(seq 1 254); do ping -c 1 -W 200 192.168.1.$i >/dev/null 2>&1 & done; wait
arp -an | grep -iE "(d8:3a:dd|b8:27:eb|2c:cf:67|88:a2:9e)"

# diagnóstico profundo de fantasmas/setores cegos (PARA o serviço; área livre)
ssh pi@lidar-03.local "sudo systemctl stop lidarmapper; cd ~/lidarmapper && .venv/bin/python -u node/diag_bg.py --config /home/pi/node-config.yaml; sudo systemctl start lidarmapper"

# atualizar o código de um nó sem internet (empurra o HEAD commitado)
deploy/push_repo.sh lidar-03
```

Do Windows (`C:\lidarmapper`):

```powershell
start_fleet.bat                                                          # o show
powershell -ExecutionPolicy Bypass -File deploy\baseline.ps1 2           # baseline painel 2
powershell -ExecutionPolicy Bypass -File deploy\baseline.ps1 all         # baseline todos
powershell -ExecutionPolicy Bypass -File deploy\install_server.ps1       # (re)instalar
.venv\Scripts\python server\radar_view.py --panel 2 --dest 127.0.0.1     # radar avulso (feche o fleet antes!)
.venv\Scripts\python w2_validate.py                                      # validar o servidor (12/12)
```

Testes sem hardware (qualquer máquina, a partir da raiz):

```bash
python server/test_node_sim.py --panels 1,2,3,4 --pattern circle   # simula nós
python server/test_udp_receiver.py --v2 --port 5555                # vê o V2 chegar
python server/test_osc_receiver.py                                 # stub do Max
```

---

## 7. Para a próxima IA (e para humanos revisando o repo)

- **Hierarquia de verdade**: este manual (operação e topologia real) →
  [CLAUDE.md](../CLAUDE.md) (regras de código) →
  [GUIA §3](../GUIA_LIDARMAPPER_DISTRIBUIDO_1.md) (protocolos, normativo).
  O restante do GUIA é a spec original do projeto — arquitetura de referência,
  não descrição do sistema instalado (a topologia `10.10.0.x` e o consumo V1
  no TD **não** foram adotados).
- **Nunca**: editar `legacy/` ou `legacy_recovery/`; declarar format string
  de `struct` fora de [shared/protocol.py](../shared/protocol.py); importar
  pygame/tkinter em `node/`; introduzir threads no loop do relay/fleet_bridge
  sem repensar o desenho single-thread.
- Os scripts `.ps1` de `deploy/` são **ASCII puro de propósito** (PowerShell
  5.1 + sem BOM = ANSI). Subprocessos Python no Windows precisam de
  `PYTHONUTF8=1` se o stdout for pipe.
- O config real de cada nó é `/home/pi/node-config.yaml` — **fora do git**.
  `node/config.yaml` é só o template. Nunca edite o template achando que
  muda um nó.
- Validação antes de declarar qualquer mudança pronta: `w2_validate.py`
  (12/12), `w1_validate.py` (21/21), `shared/test_protocol.py`.
- Pendências conhecidas (08/2026): fonte 27 W para o p3 (Pi 5); fonte melhor
  para o p4 (Pi 3B com dips); reserva DHCP dos 8 Pis no roteador.

---

*Consolidado em 15/08/2026, ao fim da primeira semana de evento — 4 trocas de
servidor, 1 temporal e 3 sensores travados depois. Tudo acima aconteceu.*
