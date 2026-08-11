# Provisionamento da frota — os 8 nós Pi via SSH

Plano de trabalho para levar os 8 nós de "cartão gravado" a "publicando V2 no
servidor". **Este documento é um plano em execução, não um procedimento
consolidado** — quando os 8 estiverem no ar, o conteúdo útil daqui migra para
[INSTALACAO_PI.md](INSTALACAO_PI.md) e este arquivo pode sair.

Escrito para ser retomado em outra máquina: assume apenas o repositório clonado.

---

## 1. Situação de partida

Os 8 cartões SD já foram gravados individualmente — hostnames `lidar-01` a
`lidar-08`, usuário `pi`, senha `pi123`, **autenticação por senha** (nenhuma
chave instalada). Os Pis vão para um switch, com cabo.

**Frota mista:** 3× Pi 3B+, 4× Pi 4, 1× Pi 5.

### O que já foi validado

| Item | Estado |
|---|---|
| Lado servidor no Windows | `w2_validate.py` 12/12 (relay, homografia, hot-reload, OSC) |
| Um nó completo (`lidar-01`, Pi 4) | S3 a 9,8 Hz, `desync=0 recon=0`; V2 chegando; relay entregando V1 em `0..1` na 6001 |
| Gate de CPU do §10 no Pi 4 | **2,3 %** de um core para 30k amostras/s (limite: 30 %) |
| Guard de degeneração da calibração | `degenerate_reason` em [server/calibrate.py](../server/calibrate.py) |

### O que o `lidar-01` tem de diferente (e precisa ser desfeito)

Ele foi provisionado **à mão, por um caminho que diverge do §4** do
[INSTALACAO_PI.md](INSTALACAO_PI.md): não havia internet no cabo direto, então o
repo foi transferido por `scp` e o `rplidar-roboticia` instalado a partir de um
wheel pré-construído, com o venv criado com `--system-site-packages` para
reaproveitar o numpy do sistema. Ele também está **sem chrony e sem systemd**.

Funciona, mas é um caso especial. Ele deve ser **reprovisionado pelo script**
para que os 8 nós fiquem idênticos.

---

## 2. Quatro problemas a resolver antes de provisionar

### 2.1 `node/config.yaml` é versionado e conflita com o update da frota

[node/config.yaml](../node/config.yaml) está sob controle de versão, e cada nó
precisa editá-lo (`panel_id`, `udp.host`, `roi`). O loop de atualização do §11
do doc do Pi (`git pull` em cada nó) **vai conflitar nos 8**.

**Decisão:** o config por nó sai da árvore git. O script grava
`/home/pi/node-config.yaml` e o systemd passa `--config`. Não exige mudança de
código — [node/main.py](../node/main.py) já aceita `--config` e
`node/config.py:load(path)` já resolve caminho arbitrário. Só a unit muda.

### 2.2 Automação não digita senha

Os 7 nós restantes só aceitam senha.
[deploy/sync_authorized_keys.sh](../deploy/sync_authorized_keys.sh) usa
`BatchMode=yes` e **pula** nós sem chave — ele serve para *propagar* chaves a
partir de uma máquina já autorizada, não para o primeiro acesso.

**Decisão:** um passo único de bootstrap, com o operador digitando `pi123` oito
vezes num loop só. Depois disso tudo é automatizado, e o
`sync_authorized_keys.sh` passa a funcionar normalmente.

### 2.3 Internet nos Pis não confirmada

Não se sabe se o switch terá saída para a internet. O script tenta o caminho
documentado (§4: `apt` + `pip`) e cai para o método offline já provado na
bancada (tarball do repo por `scp` + wheel pré-construído + venv com
`--system-site-packages`).

### 2.4 `baseline.duration_s` inconsistente

O repo tem `2.0`; o [§8.6](INSTALACAO_PI.md) recomenda `6.0` com base em
medição — com 2 s ficaram **66 de 720 bins angulares sem aprender**, e bin sem
baseline vira foreground permanente ([node/processing.py](../node/processing.py),
`foreground_mask`), gerando track fantasma imóvel. O ajuste foi feito só no Pi
da bancada, não no repo.

---

## 3. Plano

### Fase 0 — Máquina nova

1. Instalar **Python 3.13** (não 3.14 — ver [INSTALACAO_SERVIDOR.md §1](INSTALACAO_SERVIDOR.md)) e Git.
2. `git clone` do repositório.
3. `py -3.13 -m venv .venv` e `.venv\Scripts\pip install -r server\requirements-server.txt`.
4. **Gerar uma chave SSH nova nessa máquina** (`ssh-keygen -t ed25519`) — não
   copiar a privada da máquina antiga. A pública entra em
   [deploy/authorized_keys](../deploy/authorized_keys).
5. `python w2_validate.py` — deve dar 12/12.

### Fase 1 — Preparar o repo

| Arquivo | Ação |
|---|---|
| `deploy/provision_node.sh` | **criar** — provisionamento idempotente (detalhe abaixo) |
| `deploy/bootstrap_keys.sh` | **criar** — distribuição inicial da chave, com senha |
| `deploy/lidarmapper.service` | **modificar** — `ExecStart` ganha `--config /home/pi/node-config.yaml` |
| `node/config.yaml` | **modificar** — `baseline.duration_s: 2.0 → 6.0`, com o porquê no comentário |
| `node/diag_bg.py` | **criar** — diagnóstico de bins cegos (foi o que achou o fantasma na bancada) |
| `deploy/authorized_keys` | **modificar** — acrescentar a chave da máquina nova |
| `docs/INSTALACAO_PI.md` | **modificar** — seção de provisionamento em lote + método offline |

**`deploy/provision_node.sh`** — idempotente, roda da máquina de trabalho contra
um nó por vez. Assinatura: `provision_node.sh <host> <panel_id>`. Deriva
`udp.host` do `panel_id` (1–4 → `10.10.0.10`, 5–8 → `10.10.0.11`). Etapas, todas
seguras para reexecutar:

1. `apt install -y git python3-venv chrony` — detecta ausência de internet e
   avisa para usar o modo offline
2. `usermod -aG dialout pi`
3. Cortes do §3: `disable-wifi` / `disable-bt` no `config.txt`, `dphys-swapfile` off
4. Clone ou `git pull` de `~/lidarmapper`
5. venv + `pip install -r node/requirements-pi.txt`
6. [deploy/99-rplidar.rules](../deploy/99-rplidar.rules) → udev, reload, verifica `/dev/rplidar`
7. [deploy/chrony-node.conf](../deploy/chrony-node.conf) → `/etc/chrony/conf.d/`, restart
8. Gera `/home/pi/node-config.yaml` a partir de `node/config.yaml`, aplicando
   `panel_id` e `udp.host`
9. [deploy/lidarmapper.service](../deploy/lidarmapper.service) → systemd,
   `daemon-reload`, `enable --now`
10. **Reporta o MAC de `eth0`**, necessário para a reserva de DHCP do
    [§3 de INSTALACAO.md](INSTALACAO.md)

Reutiliza os arquivos que já existem em [deploy/](../deploy/) — não recria nada.

### Fase 2 — Distribuir a chave (uma vez, com senha)

`deploy/bootstrap_keys.sh` roda `ssh-copy-id` nos 8 hosts em sequência. O
operador digita `pi123` oito vezes; daí em diante é tudo sem senha.

### Fase 3 — Provisionar

Um nó por vez, conferindo cada um antes do próximo:

```bash
deploy/provision_node.sh lidar-01 1     # ... até lidar-08 / panel_id 8
```

O `lidar-01` entra nessa lista: reprovisioná-lo elimina o caso especial do §1.

### Fase 4 — Validar cada nó

Na ordem do §8 do [INSTALACAO_PI.md](INSTALACAO_PI.md):

1. `node/test_e2e.py` — ambiente ARM, sem sensor
2. `node/bench_parse.py --hz 30000` — **obrigatório nos 3 Pi 3B+**
3. `node/test_lidar.py --duration 30` — `scans/s` 8–15, `desync=0 recon=0`
4. `vcgencmd get_throttled` = `0x0` — **crítico no Pi 5**
5. **Área livre → `fg=0 tracks=0`** ([§8.6](INSTALACAO_PI.md)) — o critério que
   impede calibrar em cima de um fantasma
6. No servidor: `server/test_udp_receiver.py --v2 --port 5555` com o `panel_id` certo

---

## 4. Verificação de ponta a ponta

Com os 8 provisionados e o relay rodando no servidor:

```
python server/server_relay.py
```

O status 1×/s deve mostrar os 8 painéis com `in` subindo. Painéis ainda sem
`calib_pN.json` aparecem como `[-]` — correto: a calibração é por painel e vem
depois da montagem física.

**Teste de reboot** (item 15 do [checklist do §5](INSTALACAO.md)): cortar a
energia de um nó e confirmar que volta publicando sozinho em ~40 s, com o `age`
do relay voltando a `0.0s`.

---

## 5. Riscos e pontos em aberto

**Internet no switch não confirmada.** Sem rota para fora, o `apt install chrony`
falha e o NTP fica pendente. O sistema funciona, mas o timestamp do header V2
deixa de ser comparável entre nós — na bancada, com o relógio sincronizado à
mão, o desvio medido foi de ~900 ms.

**Os 3 Pi 3B+ são o gate do §10 e nunca foram medidos em hardware real.** Se o
`bench_parse.py` reprovar neles, é problema de projeto, não de instalação —
melhor descobrir no primeiro 3B+ provisionado, não no oitavo.

**O Pi 5 exige fonte oficial de 27 W.** Carregador GaN de 100 W entrega apenas
3 A em 5 V; o Pi 5 negocia 5 A por USB-C PD e, sem esse perfil, corta a corrente
total de USB para 600 mA e trava. Ver [§2 de INSTALACAO_PI.md](INSTALACAO_PI.md).

**ROI, `angle_offset_deg` e `mirror` dependem da montagem física** e não podem
ser definidos por script. O provisionamento entrega o nó publicando; o ajuste
por painel é o §7 do doc do Pi.

**Os limiares do guard de calibração** (150 mm de lado, 0,05 m² de área) foram
escolhidos na bancada, não vêm da spec. Se algum painel real for pequeno,
revisar em [server/calibrate.py](../server/calibrate.py).
