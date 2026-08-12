# Instalação do nó — Raspberry Pi + RPLIDAR S3

Procedimento completo de um nó (`lidar-0N`), da gravação do cartão SD ao
serviço rodando sozinho no boot. Base: §6, §9, §10, §11 e §12 da
[spec](../GUIA_LIDARMAPPER_DISTRIBUIDO_1.md) + o roteiro de
[node/VALIDACAO.md](../node/VALIDACAO.md).

**Faça este documento inteiro uma vez, no lidar-01, na bancada.** Os outros 7
nós saem da golden image (§10) e só precisam das seções 7, 10 e 11.

Índice:

1. [Imagem e primeiro boot](#1-imagem-e-primeiro-boot)
2. [Alimentação e térmica](#2-alimentação-e-térmica)
3. [Cortes de sistema](#3-cortes-de-sistema)
4. [Repositório e ambiente Python](#4-repositório-e-ambiente-python)
5. [udev — nome estável do S3](#5-udev--nome-estável-do-s3)
6. [chrony — relógio sincronizado](#6-chrony--relógio-sincronizado)
7. [config.yaml — o que muda por nó](#7-configyaml--o-que-muda-por-nó)
8. [Testes antes de virar serviço](#8-testes-antes-de-virar-serviço)
9. [systemd — subir no boot](#9-systemd--subir-no-boot)
10. [Golden image e replicação](#10-golden-image-e-replicação)
11. [Atualizar a frota](#11-atualizar-a-frota)
12. [Troubleshooting](#12-troubleshooting)

---

## 1. Imagem e primeiro boot

**Sistema: Raspberry Pi OS Lite 64-bit (arm64), headless.** A mesma imagem
serve 3B+, 4 e 5 — é o que preserva a golden image única.

No Raspberry Pi Imager, antes de gravar, abra as opções avançadas (engrenagem)
e configure:

| Campo | Valor |
|---|---|
| Hostname | `lidar-01` (depois `lidar-02`… nos clones) |
| Utilizador | `pi` — se usar outro nome, ajuste `User=` e os paths `/home/pi` em [deploy/lidarmapper.service](../deploy/lidarmapper.service) |
| Palavra-passe | **`pi123`** — a mesma nos 8 nós (obrigatória: não existe mais o `pi`/`raspberry` padrão) |
| Acesso remoto | SSH habilitado, **autenticação por palavra-passe** (ver abaixo) |
| Wi-Fi | **não configure** — a rede é cabeada e o rádio é desligado no §3 |
| Locale / timezone | conforme a instalação |

A golden image (§10) clona essa conta para os 8 nós, então senha diferente por
nó só cria dor operacional — a rede é isolada e cabeada.

### Senha ou chave pública?

**Padrão da instalação: autenticação por palavra-passe.** Qualquer computador
da equipe entra com `ssh pi@lidar-0N` e a senha `pi123`, sem cadastrar nada,
sem gerenciar chaves. Numa rede cabeada isolada, com 8 nós num galpão e acesso
físico restrito, a complexidade de gerenciar chaves não se paga.

> ⚠️ **Isso depende inteiramente da rede ser isolada.** A senha é fraca e está
> documentada em repositório público — a escolha é deliberada, e o que a
> sustenta é o switch não ter rota para a internet. Se alguém plugar a rede
> num roteador com saída ("só para baixar uma atualização"), a porta 22 desses
> Pis é varrida e quebrada em horas: faça de forma pontual, com o cabo
> desconectado depois. Se a instalação um dia ganhar rota permanente para fora,
> troque a senha nos 8 nós (`passwd`) e reveja esta seção.

A senha vale de qualquer jeito, mesmo se você optar por chave: é a senha do
`sudo` e do console físico (teclado + monitor no Pi).

### Opcional: entrar sem digitar senha

O loop de atualização da frota (§11) pede a senha 8 vezes. Se isso incomodar,
na máquina que você mais usa, uma vez por nó:

```bash
ssh-copy-id pi@lidar-01     # ... até lidar-08
```

A partir daí essa máquina entra sem senha e as outras continuam entrando com
senha — nada é perdido. Para fazer os 8 de uma vez, com as chaves da equipe
versionadas no repo, existe [deploy/authorized_keys](../deploy/authorized_keys)
(uma linha por máquina) e:

```bash
deploy/sync_authorized_keys.sh            # todos os nós
deploy/sync_authorized_keys.sh lidar-03   # um nó só
DRY_RUN=1 deploy/sync_authorized_keys.sh  # simula, não altera nada
```

O script valida as chaves antes de tocar em qualquer nó, escreve num temporário
e só então substitui, deixando `~/.ssh/authorized_keys.bak` no Pi. Para obter a
chave pública de uma máquina:

```bash
cat ~/.ssh/id_ed25519.pub                 # se não existir, crie:
ssh-keygen -t ed25519 -C "curva-lidar-<maquina>"
```

> Se em vez disso você marcar **"autenticação por chave pública"** no Imager, o
> SSH deixa de aceitar senha: só entram as máquinas cadastradas, e cadastrar
> uma nova exige `ssh-copy-id`/sync a partir de outra já autorizada. Mais
> fechado, mais trabalho. Perdeu todas as chaves? Teclado e monitor no Pi, ou
> monte o cartão SD e edite `home/pi/.ssh/authorized_keys` na partição raiz.

Grave, ponha o cartão no Pi, ligue no cabo de rede e acesse:

```bash
ssh pi@lidar-01
```

Registre o **MAC** da interface ethernet para a reserva de DHCP:

```bash
cat /sys/class/net/eth0/address
```

### Bancada: macOS com Compartilhamento de Internet (o caminho usado na frota)

Com a bancada num Mac, o caminho mais simples cobre DHCP **e** internet de uma
vez: ligue o adaptador Ethernet do Mac no switch dos Pis e ative **Ajustes do
Sistema → Geral → Compartilhamento → Compartilhamento de Internet** (da
interface Wi-Fi para o adaptador Ethernet). O macOS vira DHCP + NAT + gateway:

- o Mac assume `192.168.2.1` no adaptador; os Pis recebem `192.168.2.x`;
- os Pis têm saída para a internet (`apt`, `pip`, `git clone` funcionam);
- cada nó resolve por Bonjour: `ping lidar-01.local`, `ssh pi@lidar-01.local`.

> ⚠️ **Nunca com o roteador da instalação plugado no mesmo switch.** Dois
> servidores DHCP na mesma rede fazem os Pis pegarem IP ora de um, ora de
> outro — falha intermitente, difícil de diagnosticar. Um de cada vez.
>
> ⚠️ Enquanto o compartilhamento está ligado, os Pis (senha `pi123`) estão
> atrás de NAT com saída para fora — vale o aviso da seção anterior. Desligue
> o compartilhamento assim que terminar de provisionar.

### Bancada: Pi ligado direto no PC Windows, sem DHCP

Antes do switch existir, é comum ligar o Pi direto na ethernet do PC. Nesse
cenário **não há servidor DHCP**, o Pi fica sem IPv4 e `ssh pi@lidar-01` não
resolve — nem por mDNS, que é pouco confiável no Windows.

O caminho mais direto é **servir DHCP do próprio PC**, o que resolve o problema
em vez de contorná-lo — o nó ganha um IPv4 fixo e para de depender de endereço
que muda a cada boot:

```
.venv\Scripts\python server\bench_dhcp.py --server 192.168.0.10 --offer 192.168.0.50
```

Deixe rodando e ligue o Pi: ele imprime o `DISCOVER`, entrega o IP, e ainda
**reporta o MAC** — que é exatamente o dado necessário para a reserva de DHCP do
switch depois. O PC precisa estar com IP fixo no mesmo `/24`.

Se preferir não subir um DHCP, o caminho alternativo é o **IPv6 link-local**:
toda interface ethernet ativa tem um endereço `fe80::/64`, independente de DHCP.
No Windows (PowerShell):

```powershell
$idx = (Get-NetAdapter -Name Ethernet).ifIndex
ping -6 -n 2 "ff02::1%$idx"          # acorda todo mundo no cabo
Get-NetNeighbor -InterfaceIndex $idx |
  Where-Object { $_.LinkLayerAddress -match '^(B8-27-EB|DC-A6-32|E4-5F-01|2C-CF-67|D8-3A-DD|28-CD-C1)' }
```

Esses prefixos de MAC são os OUIs da Raspberry Pi (3B+/4/5). Com o endereço em
mãos, o `%$idx` no fim é obrigatório — é o escopo da interface:

```powershell
ssh "pi@fe80::fc51:3c48:c2b7:bf35%13"
```

Duas armadilhas dessa modalidade:

- **O endereço link-local muda a cada boot** (é *stable-privacy* do
  NetworkManager). Serve para entrar e configurar um IPv4 fixo — não para
  automatizar nada. Assim que entrar, fixe o IP conforme o plano de rede
  ([INSTALACAO.md](INSTALACAO.md) §3).
- **`Get-NetNeighbor` vazio não significa Pi desligado.** Para distinguir Pi
  travado de problema de rota ou autenticação, olhe o contador de recepção:

```powershell
Get-NetAdapterStatistics -Name Ethernet | Select-Object ReceivedBytes
```

Se `ReceivedBytes` fica **congelado** enquanto os enviados sobem, nada está
chegando do outro lado do cabo — o Pi está fora, e o problema não é de rede.
`Status: Up` no adaptador só prova que o PHY negociou, o que acontece com a
placa alimentada mesmo sem sistema operacional de pé.

> Um Pi 4 ou 5 negociando **100 Mbps** (ambos são gigabit) denuncia **cabo de 2
> pares**. Não atrapalha o nosso tráfego (~16 kB/s por nó), mas troque antes da
> instalação definitiva.

---

## 2. Alimentação e térmica

| Modelo | Fonte oficial | Conector | Refrigeração (operação de horas) |
|---|---|---|---|
| 3B+ | 5 V / 2,5 A | microUSB | dissipador passivo basta |
| Pi 4 | 5 V / 3 A | USB-C | case ventilado ou dissipador; case fechado sem dissipador entra em throttle |
| Pi 5 | 5 V / 5 A (PSU oficial) | USB-C | active cooler recomendado |

> ⚠️ **Carregador GaN "de 100 W" não alimenta um Pi 5.** A potência nominal
> desses carregadores só existe a 20 V; o perfil típico em **5 V é 3 A (15 W)**.
> O Pi 5 pede 5 V / 5 A e negocia isso por USB-C PD — sem enxergar esse perfil
> ele até liga, mas entra em modo limitado e **corta a corrente total de USB
> para 600 mA**, o que deixa o S3 no limite. Somado à regulação ruim de 5 V que
> esses carregadores costumam ter sob carga transiente, o sintoma é travamento
> duro depois de alguns minutos de operação.
>
> O 3B+ e o Pi 4 não têm esse problema: pedem 2,5 A e 3 A **sem negociação PD**,
> dentro do que qualquer porta de 5 V entrega. Se o travamento aparecer também
> neles, o suspeito **não** é a fonte — vá para o cartão SD (§12).

Confira depois de alguns minutos com o S3 girando:

```bash
vcgencmd get_throttled
```

Resultado esperado: `throttled=0x0`. Qualquer outro valor significa subtensão
(bits 0/16) ou throttle térmico (bits 1–3) — resolva antes de continuar, porque
o sintoma em produção é o sensor caindo no meio do show.

---

## 3. Cortes de sistema

Rádios desligados (não usamos, e o Wi-Fi ligado ainda gera interrupções) e swap
desabilitado (o Pi nunca deve paginar durante o loop):

```bash
sudo sh -c 'printf "\ndtoverlay=disable-wifi\ndtoverlay=disable-bt\n" >> /boot/firmware/config.txt'
sudo systemctl disable --now dphys-swapfile
sudo reboot
```

> Em imagens antigas o arquivo é `/boot/config.txt` em vez de
> `/boot/firmware/config.txt`. Confira qual existe antes de rodar.

Não conecte display ao Pi — o nó é headless por projeto (nada de pygame,
tkinter ou customtkinter em [node/](../node/)).

Use só overlays que valem nos três modelos: a golden image é única.

---

## 4. Repositório e ambiente Python

```bash
sudo apt update && sudo apt full-upgrade -y
sudo apt install -y git python3-venv chrony
sudo usermod -aG dialout $USER
```

> ⚠️ O `usermod` só tem efeito depois de **relogar** (`exit` e `ssh` de novo).
> Sem ele, abrir a porta serial do S3 dá `Permission denied`.

```bash
git clone <URL-DO-REPO> ~/lidarmapper
cd ~/lidarmapper
python3 -m venv .venv
source .venv/bin/activate
pip install -r node/requirements-pi.txt
```

> **Nota de divergência:** o §6 da spec mostra `pip install -r requirements-pi.txt`
> e `python main.py`. Os caminhos reais no repo são `node/requirements-pi.txt` e
> `node/main.py` — use os desta página.

Dependências instaladas ([node/requirements-pi.txt](../node/requirements-pi.txt)):
`rplidar-roboticia`, `pyserial`, `numpy`, `pyyaml`, `ruamel.yaml`. Nada de
pygame/tkinter — se aparecer algum deles no `pip list` do Pi, alguém instalou
o requirements errado.

Verifique que o Python do venv importa numpy compilado para arm64:

```bash
.venv/bin/python -c "import numpy, serial, yaml; print(numpy.__version__)"
```

---

## 5. udev — nome estável do S3

Sem isso o sensor oscila entre `/dev/ttyUSB0` e `/dev/ttyUSB1` conforme a ordem
de enumeração no boot, e o `config.yaml` aponta para um caminho fixo.

```bash
sudo cp ~/lidarmapper/deploy/99-rplidar.rules /etc/udev/rules.d/99-rplidar.rules
sudo udevadm control --reload-rules && sudo udevadm trigger
```

Desconecte e reconecte o USB do S3, e confirme:

```bash
ls -l /dev/rplidar
# lrwxrwxrwx 1 root root 7 ... /dev/rplidar -> ttyUSB0
```

A regra casa o VID/PID do CP210x (`10c4:ea60`) — o mesmo par que
[node/lidar_reader.py](../node/lidar_reader.py) usa no autodetect de fallback.
Confira o que o kernel enumerou, se precisar:

```bash
lsusb | grep -i cp210
dmesg | tail -20
```

---

## 6. chrony — relógio sincronizado

O header do protocolo V2 carrega o `time.time()` do Pi. Sem NTP, o timestamp é
incomparável entre nós e qualquer medição de latência mente.

```bash
sudo cp ~/lidarmapper/deploy/chrony-node.conf /etc/chrony/conf.d/lidarmapper.conf
sudo systemctl restart chrony
chronyc tracking
chronyc sources -v
```

Esperado: `Leap status : Normal`, offset na casa dos milissegundos, e o
`10.10.0.10` marcado com `^*` em `sources`.

Se o diretório `/etc/chrony/conf.d/` não existir nessa imagem, acrescente a
linha `server 10.10.0.10 iburst prefer` ao fim de `/etc/chrony/chrony.conf`.

O server-a precisa estar servindo NTP (ver
[INSTALACAO_SERVIDOR.md](INSTALACAO_SERVIDOR.md) §4).

---

## 7. config.yaml — o que muda por nó

**O config que o serviço lê é `/home/pi/node-config.yaml`** — fora da árvore
git. Ele é gerado no provisionamento por
[deploy/render_node_config.py](../deploy/render_node_config.py) a partir de
[node/config.yaml](../node/config.yaml) (que segue versionado como *template*),
e a unit do systemd o passa via `--config`. Motivo: se cada nó editasse o
arquivo versionado, o `git pull` de atualização da frota (§11) conflitaria nos
8 nós. Edite o `node-config.yaml` à vontade — o provisionamento não o
sobrescreve (só com `--rewrite-config`).

Para rodar à mão com esse config:

```bash
.venv/bin/python node/main.py --config /home/pi/node-config.yaml
```

Campos que variam por nó:

| Campo | O que é | Muda por |
|---|---|---|
| `udp.panel_id` | identidade do painel, `1..8` | **nó** — obrigatório, único |
| `udp.host` | `10.10.0.10` (painéis 1–4) ou `10.10.0.11` (5–8) | servidor de destino |
| `roi.*` | recorte em mm no referencial do sensor | montagem física |
| `processing.angle_offset_deg` | rotação do sensor | montagem física |
| `processing.mirror` | espelhamento do eixo | montagem física |

Todo o resto (`tracker`, `baseline`, `sensor.baud`, `publish_rate_hz`) é igual
em todos os nós e não deve ser mexido sem motivo.

> ⚠️ **`panel_id` vem `0` de fábrica e isso é proposital.** Sem editar, o nó
> aborta no start com:
> ```
> ValueError: udp.panel_id obrigatório em 1..8 no config.yaml (veio 0).
> Cada nó do repo tem panel_id único.
> ```
> Não existe default silencioso: dois nós com o mesmo `panel_id` fariam o relay
> misturar dois painéis, e o erro só apareceria como cursor fantasma.

### Definindo a ROI

A ROI é a primeira linha de defesa contra o LIDAR enxergar o público do painel
vizinho (a segunda é o clip em `0..1` no relay). Origem `(0,0)` no sensor,
valores em mm:

```yaml
roi:
  x_min: -1400    # metade da largura útil, à esquerda
  x_max:  1400    # metade da largura útil, à direita
  y_min:   100    # ignora o que está colado no sensor
  y_max:  2100    # profundidade máxima de interação
```

Ajuste com o pipeline rodando e alguém andando na frente do painel:

```bash
cd ~/lidarmapper
.venv/bin/python node/main.py --config /home/pi/node-config.yaml --no-publish --log-level info
```

O campo `fg=` do log é o número de pontos foreground **dentro da ROI**. Ele
deve subir quando alguém entra na área do painel e voltar para perto de zero
quando a área está livre. Se subir com gente passando **atrás** ou **ao lado**,
aperte a ROI.

`angle_offset_deg` e `mirror` corrigem a montagem: se o cursor anda para a
esquerda quando a mão vai para a direita, `mirror: true`; se o eixo está
girado, ajuste o offset em graus.

---

## 8. Testes antes de virar serviço

Na ordem — cada um isola uma camada. Tudo a partir de `~/lidarmapper`.

**8.1 — Pipeline sem hardware** (valida que o ambiente ARM está ok):

```bash
.venv/bin/python node/test_e2e.py
```

Esperado: `PASS` e código de saída 0.

**8.2 — O sensor de verdade:**

```bash
.venv/bin/python node/test_lidar.py --duration 30
```

Esperado: `scans/s` entre 8 e 15 Hz, `meas/s` alto, `reconnects=0`, `desync`
baixo e estável.

**8.3 — Gate de CPU (§10 da spec)**, no hardware de destino:

```bash
.venv/bin/python node/bench_parse.py --hz 30000
.venv/bin/python node/bench_parse.py --hz 40000    # margem
```

| Máquina | % de um core para 30k amostras/s | Veredicto |
|---|---|---|
| dev x86 típico | ~0,2 % | só referência de sanidade |
| Pi 5 (1 core) | < 12 % | quase certo que cabe no 3B+ — seguir |
| Pi 5 (1 core) | 12–20 % | zona cinzenta — validar num 3B+ real |
| Pi 5 (1 core) | > 20 % | otimizar antes de escalar |
| **3B+ real** | **≤ 30 %** | **critério definitivo** |

**8.4 — Pipeline completo publicando:**

```bash
.venv/bin/python node/main.py
```

O log de 1×/s é o painel de instrumentos do nó:

```
meas/s=  8412  scans/s=10.2  fg=  37  tracks=1  pub/s= 30.0  +  30 frames  desync=0  recon=0
```

- `meas/s` — throughput do sensor. Caiu? cabo/fonte/USB.
- `scans/s` — rotações por segundo, 8–15 Hz.
- `fg` — pontos foreground dentro da ROI (0 com a área livre).
- `tracks` — cursores rastreados.
- `pub/s` — deve ficar cravado em 30.
- `desync` / `recon` — devem ficar parados; crescimento contínuo é problema de cabo ou alimentação.

**8.5 — Confirmação do outro lado**, num shell no servidor de destino:

```bash
.venv/bin/python server/test_udp_receiver.py --v2 --port 5555
```

Esperado: ~30 pacotes/s, `panel_id` correto, `bad=0`.

**Critérios de aprovação do nó** (§10): `main.py` consumindo **< 70 % de um
core** no 3B+, `pub/s` estável em 30, `vcgencmd get_throttled` = `0x0` depois
de 1 h de operação.

### 8.6 — O critério que evita calibrar em cima de um fantasma

Antes de calibrar, **com a área do painel livre, o log tem que mostrar
`fg=0 tracks=0`**. Se mostrar `tracks` maior que zero com ninguém na frente, o
nó está publicando um cursor fantasma — e a calibração vai colher esse fantasma
em vez do operador, sem dar erro nenhum.

A ferramenta que verifica isso e já aponta a causa:

```bash
.venv/bin/python -u node/diag_bg.py
```

Ela mede o baseline, lista os **setores angulares cegos**, e depois observa a
ROI com a área livre. Saída limpa é `ZERO pontos foreground na ROI` e código de
saída 0. Havendo fantasma, ela imprime os clusters com coordenada e ângulo, e a
ordem do que tentar. Rode-a em cada nó antes da calibração daquele painel.

A causa está em [node/processing.py](../node/processing.py), no
`foreground_mask`: bin angular que **não recebeu nenhuma medida válida durante o
baseline** fica `NaN`, e `NaN` é tratado como **foreground incondicional**. O
comportamento é proposital (bin sem retorno = espaço vazio, logo qualquer coisa
que apareça ali é nova), mas tem uma consequência ruim: se um objeto estático
cair num desses setores cegos, ele vira um track permanente e imóvel.

Medido na bancada (Pi 4 + S3, 08/2026): com `baseline.duration_s: 2.0` ficaram
**66 de 720 bins cegos**, e uma superfície a 38 cm virou track fantasma fixo com
`confidence=1.00`. Com **6,0 s** caiu para 58 bins e o fantasma desapareceu.
Por isso `6.0` é o default do [node/config.yaml](../node/config.yaml) desde
08/2026 — se um nó antigo ainda mostrar `2.0` no `node-config.yaml`, corrija.

Como distinguir fantasma de detecção real no `test_udp_receiver.py --v2 --raw`:
o fantasma tem **coordenada congelada** (varia menos de 1 mm entre frames) e
`c=1.00` constante; uma medida real de superfície varia alguns milímetros frame
a frame. Fantasma alinhado em um eixo (`x` constante, `y` variando) é uma
superfície plana — parede, quina de mesa, estrutura.

> ⚠️ **Todo reposicionamento do sensor invalida o baseline.** Mexeu no ângulo,
> na altura ou na fixação? Reinicie o nó com a área livre. Sintoma de baseline
> velho: vários `tracks` parados que não somem, e o `meas/s` diferente do que
> era antes (a cena mudou).

---

## 9. systemd — subir no boot

```bash
sudo cp ~/lidarmapper/deploy/lidarmapper.service /etc/systemd/system/lidarmapper.service
sudo systemctl daemon-reload
sudo systemctl enable --now lidarmapper
journalctl -u lidarmapper -f
```

A unit de [deploy/lidarmapper.service](../deploy/lidarmapper.service) roda
`node/main.py` com o Python do venv, com `Restart=always`, `RestartSec=3`,
`ExecStartPre=/bin/sleep 5` (tempo do udev criar `/dev/rplidar` e da rede
subir) e `Nice=-10`.

### O baseline no boot

No start, o `BackgroundSubtractor` captura o fundo estático por
`baseline.duration_s` (6 s — ver §8.6). **A área precisa estar livre nesse instante.** Com
`Restart=always`, uma queda de energia religa o nó sozinho — se tiver gente
parada na frente do painel nesse momento, essa pessoa vira "fundo" e some do
tracking.

Refazer o fundo, do servidor:

```bash
ssh lidar-0N sudo systemctl restart lidarmapper
```

(A Fase 2 do projeto prevê um endpoint HTTP `POST /rebaseline` por nó — hoje é
o `systemctl restart`.)

### Teste de reboot (item 15 do checklist)

Corte a energia do Pi na tomada e devolva. Em ~40 s o nó deve estar publicando
de novo, sem ninguém tocar em nada, e o relay no servidor deve mostrar `age`
voltando para `0.0s` sozinho.

---

## 10. Golden image e replicação

Só depois do lidar-01 **inteiramente validado** (checklist do §5 de
[INSTALACAO.md](INSTALACAO.md) verde de ponta a ponta).

1. Desligue o Pi, remova o SD e clone a imagem (`dd`, Raspberry Pi Imager, Win32DiskImager).
2. Grave 7 cópias.
3. Em cada nó novo, mude **apenas**:

```bash
# hostname
sudo hostnamectl set-hostname lidar-0N
sudo sed -i "s/lidar-01/lidar-0N/g" /etc/hosts
sudo reboot
```

```bash
# /home/pi/node-config.yaml — regenere com o panel_id do novo nó
# (deriva udp.host sozinho: 1-4 → 10.10.0.10, 5-8 → 10.10.0.11)
~/lidarmapper/.venv/bin/python ~/lidarmapper/deploy/render_node_config.py \
    --panel 5 --out /home/pi/node-config.yaml
# depois ajuste roi / angle_offset_deg / mirror à mão, conforme a montagem
```

> Alternativa sem golden image (a usada nesta frota): cartões gravados
> individualmente no Imager + [deploy/provision_node.sh](../deploy/provision_node.sh)
> por nó — ver [PROVISIONAMENTO_FROTA.md](PROVISIONAMENTO_FROTA.md).

4. Registre o MAC daquele Pi na reserva de DHCP do IP correspondente.
5. Rode o checklist de bring-up do painel (§5 de [INSTALACAO.md](INSTALACAO.md)).

> A frota é mista (3B+/4/5). Nunca coloque na golden image um overlay de
> `config.txt` específico de um modelo — `disable-wifi`/`disable-bt` valem para
> os três.

---

## 11. Atualizar a frota

**A rede definitiva não tem internet** (o roteador só serve a rede local), então
`git pull` nos nós não funciona lá. O caminho é empurrar o HEAD commitado da
máquina de trabalho por SSH:

```bash
deploy/push_repo.sh lidar-0{1..8}      # a frota
deploy/push_repo.sh lidar-03           # um nó só
ssh lidar-03 journalctl -u lidarmapper -n 30
```

O script usa `git archive` (só vai o que está commitado), não toca no
`/home/pi/node-config.yaml` e reinicia o serviço ao final. Cada restart refaz o
baseline — **rode com a área livre**, nunca durante a operação com público.
Se `node/requirements-pi.txt` mudou, o `pip install` no nó exige internet:
volte a bancada com o Mac compartilhando internet (§1).

Numa rede COM internet, o equivalente manual continua valendo:

```bash
ssh lidar-03 'cd lidarmapper && git pull && sudo systemctl restart lidarmapper'
```

---

## 12. Troubleshooting

| Sintoma | Causa provável | O que fazer |
|---|---|---|
| `ValueError: udp.panel_id obrigatório em 1..8` | `config.yaml` não editado | §7 desta página |
| `/dev/rplidar` não existe | regra udev não aplicada, ou S3 sem enumerar | `lsusb \| grep -i cp210`, `sudo udevadm trigger`, replugar o USB |
| `Permission denied` na porta serial | usuário fora do grupo `dialout` | `sudo usermod -aG dialout $USER` e **relogar** |
| `LIDAR não iniciou` no log | porta errada, motor travado, cabo | `ls -l /dev/rplidar`, testar com `--port /dev/ttyUSB0` |
| `throttled` ≠ `0x0` | fonte/cabo insuficiente ou calor | fonte oficial, cabo curto, dissipador (§2) |
| `meas/s` baixo ou oscilando | cabo USB ruim, subtensão | trocar cabo; conferir `throttled` |
| `desync`/`recon` crescendo | ruído na serial, alimentação | mesmo acima; se persistir, trocar o cabo do S3 |
| `pub/s` abaixo de 30 | CPU saturada | rodar `node/bench_parse.py`; conferir se algo mais roda no Pi |
| `fg=0` sempre, mesmo com gente na frente | baseline capturado com a área ocupada | `sudo systemctl restart lidarmapper` com a área livre |
| `tracks>0` com a área **livre**, coordenada congelada | objeto estático num setor sem baseline (§8.6) | aumentar `baseline.duration_s`; conferir `fg=0 tracks=0` antes de calibrar |
| Vários `tracks` parados após mexer no sensor | baseline velho, da posição anterior | reiniciar o nó com a área livre (§8.6) |
| Calibração colhe sempre o mesmo ponto | operador dentro da ROI (a mediana é de **todos** os pontos) | apertar a ROI para excluir onde o operador fica, ou sair do campo antes do ESPAÇO |
| `fg` alto com a área livre | ROI larga demais, ou fundo mudou (algo foi movido) | apertar a ROI (§7) e refazer o baseline |
| Nó publicando, relay com `in=0` | IP/porta errados, firewall do Windows | `udp.host` no config; regra UDP 5555 no servidor |
| Relay com `in>0` e `[-]` | painel sem `calib_pN.json` | calibrar ([SERVIDOR §7](INSTALACAO_SERVIDOR.md)) |
| Cursor espelhado | montagem | `processing.mirror` / `angle_offset_deg` (§7) |
| Pi não aparece na rede, em cabo direto no PC | não há DHCP nesse cenário | achar por IPv6 link-local (§1, "Bancada") |
| `Permission denied (publickey)`, sem pedir senha | cartão gravado com "autenticação por chave pública" e você não tem a chave | ver o fim do §1: teclado+monitor, editar `authorized_keys` na partição raiz do cartão, ou regravar |
| Sobe, responde 1–2 min e trava, **em mais de uma placa** | cartão SD ruim ou falsificado | regravar em **outro** cartão antes de investigar fonte |
| Sobe e trava só no Pi 5 | fonte sem 5 V / 5 A (carregador GaN) | PSU oficial de 27 W (§2) |
| Pi 4 ou 5 negociando 100 Mbps | cabo de rede de 2 pares | trocar o cabo (irrelevante para o tráfego, mas sinaliza cabo ruim) |

Diagnóstico rápido no nó:

```bash
systemctl status lidarmapper
journalctl -u lidarmapper -n 100 --no-pager
journalctl -u lidarmapper -f
```

Para depurar sem interferir no serviço, pare-o e rode em primeiro plano:

```bash
sudo systemctl stop lidarmapper
.venv/bin/python node/main.py --log-level debug
```
