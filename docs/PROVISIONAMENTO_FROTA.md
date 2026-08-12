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

**Frota mista (confirmada 08/2026):** 3× Pi 3B+, 4× Pi 4, 1× Pi 5.

### Topologia (definida 11/08/2026)

A máquina de trabalho agora é um **MacBook Pro (arm64)**; a bancada e a rede
definitiva diferem, e a diferença dita o cronograma:

| | Bancada (provisionamento) | Rede definitiva |
|---|---|---|
| DHCP + gateway | **o Mac**, via Compartilhamento de Internet (Wi-Fi → adaptador Ethernet no switch) | **roteador Wi-Fi** no switch, sem saída para a internet |
| Internet nos Pis | **sim** (NAT do Mac) — `apt`/`pip`/`git clone` funcionam | **não** — atualização vira `deploy/push_repo.sh` |
| Faixa | `192.168.2.x` (Mac = `192.168.2.1`) | a do roteador — decisão: adotá-la, não forçar `10.10.0.x` ([INSTALACAO.md §3](INSTALACAO.md)) |

Além do switch e do roteador, a rede definitiva tem as 2 máquinas Windows
(server-a: painéis 1–4, server-b: 5–8), cada uma com TD e uma tela em 4
setores. Os relays são independentes — nada aqui depende de uma falar com a outra.

**Consequências:**

- **A internet só existe na bancada** → os 8 nós precisam ser provisionados
  nessa janela. Não há modo offline no script — de propósito (§2.3 revisto).
- **`git pull` morre na rede definitiva** → [deploy/push_repo.sh](../deploy/push_repo.sh)
  empurra o HEAD por SSH ([INSTALACAO_PI.md §11](INSTALACAO_PI.md)).
- ⚠️ **Nunca** o Compartilhamento do Mac e o roteador no switch **ao mesmo
  tempo** — dois DHCP na mesma L2 = IP intermitente nos Pis.

### O que já foi validado

| Item | Estado |
|---|---|
| Lado servidor no Windows | `w2_validate.py` 12/12 (relay, homografia, hot-reload, OSC) |
| Um nó completo (`lidar-01`, Pi 4) | S3 a 9,8 Hz, `desync=0 recon=0`; V2 chegando; relay entregando V1 em `0..1` na 6001 |
| Gate de CPU do §10 no Pi 4 | **2,3 %** de um core para 30k amostras/s (limite: 30 %) |
| Guard de degeneração da calibração | `degenerate_reason` em [server/calibrate.py](../server/calibrate.py) |

### Ferramentas de bancada já no repo

Promovidas do ambiente temporário da primeira sessão, para não se perderem na
troca de máquina:

| Ferramenta | Para quê |
|---|---|
| [node/diag_bg.py](../node/diag_bg.py) | setores angulares cegos + fantasmas na ROI. **Rode antes de calibrar cada painel** |
| [server/bench_dhcp.py](../server/bench_dhcp.py) | DHCP mínimo para Pi ligado direto no PC; reporta o MAC para a reserva |
| [server/test_osc_receiver.py](../server/test_osc_receiver.py) | stub do Max/MSP: mostra os `/touch/N` |
| [server/make_test_calib.py](../server/make_test_calib.py) | calibração **sintética**, destrava o teste do TD sem painel físico |

### O que o `lidar-01` tem de diferente (e precisa ser desfeito)

Ele foi provisionado **à mão, por um caminho que diverge do §4** do
[INSTALACAO_PI.md](INSTALACAO_PI.md): não havia internet no cabo direto, então o
repo foi transferido por `scp` e o `rplidar-roboticia` instalado a partir de um
wheel pré-construído, com o venv criado com `--system-site-packages` para
reaproveitar o numpy do sistema. Ele também está **sem chrony e sem systemd**.

Funciona, mas é um caso especial. Ele deve ser **reprovisionado pelo script**
para que os 8 nós fiquem idênticos.

---

## 2. Quatro problemas a resolver antes de provisionar — ✅ todos resolvidos

### 2.1 `node/config.yaml` é versionado e conflita com o update da frota ✅

[node/config.yaml](../node/config.yaml) está sob controle de versão, e cada nó
precisa editá-lo (`panel_id`, `udp.host`, `roi`). O loop de atualização do §11
do doc do Pi **conflitaria nos 8**.

**Resolvido:** o config por nó saiu da árvore git —
[deploy/render_node_config.py](../deploy/render_node_config.py) gera
`/home/pi/node-config.yaml` (comentários preservados via `ruamel.yaml`) e a
unit passa `--config`. O modo `--update` troca `panel_id`/`udp.host` num
arquivo existente sem perder ROI/mirror ajustados à mão.

### 2.2 Automação não digita senha ✅

[deploy/sync_authorized_keys.sh](../deploy/sync_authorized_keys.sh) usa
`BatchMode=yes` e **pula** nós sem chave — serve para *propagar* chaves, não
para o primeiro acesso.

**Resolvido:** [deploy/bootstrap_keys.sh](../deploy/bootstrap_keys.sh) roda
`ssh-copy-id` por host (aceita um nó por vez — o fluxo real da bancada). Nó que
já aceita a chave é pulado sem pedir senha.

### 2.3 Internet nos Pis ✅ (confirmada NA BANCADA, e só nela)

O Mac compartilhando internet no switch dá `apt`/`pip`/`git clone` durante o
provisionamento (§1). A rede definitiva não terá saída — por isso
[deploy/provision_node.sh](../deploy/provision_node.sh) **exige** internet e
aborta com instrução em vez de cair num fallback offline: foi um caminho
manual divergente que criou o caso especial do `lidar-01`.

### 2.4 `baseline.duration_s` inconsistente ✅

**Resolvido:** `6.0` é o default do repo desde 08/2026, com o porquê no
comentário ([node/config.yaml](../node/config.yaml), medição no
[§8.6](INSTALACAO_PI.md): 66/720 bins cegos com 2 s → track fantasma imóvel).

---

## 3. Plano

### Fase 0 — Máquina nova ✅ (11/08/2026, MacBook Pro arm64)

Executada com desvios deliberados do plano original (que assumia Windows):

1. ~~Instalar Python 3.13~~ → **mantido o Python 3.14.4** do `.venv` já
   existente: `w2_validate.py` **12/12** e `w1_validate.py` **21/21** passam
   inteiros no macOS/3.14. (O 3.13 era por causa de wheel do pygame no
   Windows; aqui o pygame 2.6.1 já estava instalado.)
2. Repo já clonado; árvore limpa.
3. ~~Gerar chave nova~~ → a `~/.ssh/id_ed25519` da máquina **já era** a única
   linha de [deploy/authorized_keys](../deploy/authorized_keys) (fingerprint
   conferida: `SHA256:8WHB57FUuniNT35ULkcZuPNrqy5GtHi79wMh7rmKY/E`).
4. `bench_parse` de referência neste Mac: **0,2 %** de um core p/ 30k
   amostras/s.

### Fase 1 — Preparar o repo ✅ (11/08/2026)

| Arquivo | Estado |
|---|---|
| [deploy/provision_node.sh](../deploy/provision_node.sh) | ✅ criado — provisionamento idempotente, um nó por vez |
| [deploy/render_node_config.py](../deploy/render_node_config.py) | ✅ criado — gera/atualiza/valida o `node-config.yaml` (comentários preservados) |
| [deploy/bootstrap_keys.sh](../deploy/bootstrap_keys.sh) | ✅ criado — primeiro acesso com senha, por host |
| [deploy/push_repo.sh](../deploy/push_repo.sh) | ✅ criado — update da frota sem internet (`git archive` por SSH) |
| [deploy/verify_node.sh](../deploy/verify_node.sh) | ✅ criado — Fase 4 automatizada por SSH |
| [deploy/lidarmapper.service](../deploy/lidarmapper.service) | ✅ `ExecStart` com `--config /home/pi/node-config.yaml` |
| [node/config.yaml](../node/config.yaml) | ✅ `baseline.duration_s: 6.0`, com o porquê no comentário |
| ~~`node/diag_bg.py`~~ | ✅ já estava — diagnóstico de bins cegos e fantasmas |
| [deploy/authorized_keys](../deploy/authorized_keys) | ✅ sem mudança — a chave do Mac já estava lá |
| [docs/INSTALACAO_PI.md](INSTALACAO_PI.md) | ✅ bancada macOS (§1), `node-config.yaml` (§7/§9/§10), update sem internet (§11) |

`provision_node.sh <host> <panel_id>` deriva `udp.host` do `panel_id`
(1–4 → `10.10.0.10`, 5–8 → `10.10.0.11`; na bancada, `--udp-host 192.168.2.1`).
Etapas (todas idempotentes): pré-checks + **MAC de `eth0`** → teste de
internet (sem ela, aborta com instrução) → apt → dialout → cortes do §3 →
clone/pull → venv (**sem** `--system-site-packages`) → udev → chrony →
`node-config.yaml` (preserva o existente; `--rewrite-config` força) → systemd
→ reboot se preciso → resumo. `--recreate-venv` existe para reprovisionar o
`lidar-01` e eliminar o caso especial do §1.

### Fase 2 — Chave, um nó por vez (na bancada)

```bash
deploy/bootstrap_keys.sh lidar-01      # digita pi123 uma vez; repete por nó
```

### Estado da frota

| Nó | Modelo | MAC eth0 | panel_id | Provisionado | verify | diag_bg |
|---|---|---|---|---|---|---|
| lidar-01 | Pi 4 B r1.5 | `d8:3a:dd:9c:22:21` | 1 | ✅ 12/08 | ✅ (throttled só no boot) | ✅ limpo |
| lidar-03 | Pi 4 B r1.5 | `88:a2:9e:70:ea:53` | 3 | ✅ 12/08 (1 run, zero intervenção) | ✅ 6/6, `0x0` | ⬜ |
| lidar-02, 04..08 | — | — | 2, 4–8 | ⬜ | ⬜ | ⬜ |

Com os dois no ar, o agregado no receiver: `panels=p1:30,p3:30`, 0 inválidos —
demux por `panel_id` validado com nós reais.

Aprendizados do lidar-01 (valem para os próximos 7):

- **Chave SSH do Mac tem passphrase** — está na Keychain; o bloco `Host lidar-*`
  no `~/.ssh/config` (com `UseKeychain yes`) resolve. Sem ele, `BatchMode` falha
  com `Permission denied` mesmo com a chave instalada no nó.
- **O Compartilhamento de Internet do macOS subiu em `192.168.3.1`** (bridge100),
  não no `192.168.2.1` previsto — confira o IP real antes do `--udp-host`.
- **Todo restart do serviço refaz o baseline** — scripts/testes que dão
  `systemctl start|restart` com alguém na frente do sensor "engolem" a pessoa
  no fundo e o tracking silencia (fg=0 com a área ocupada). Área livre SEMPRE.
- **Subtensão transiente na partida do motor do S3** (`0x50000` com bits atuais
  limpos): dois dips no boot, zero em operação. Não bloqueia; se aparecer dip
  *durante* operação, trocar o cabo USB-C primeiro.
- **Na bancada o S3 ficou de costas** — `angle_offset_deg: 180` no
  `node-config.yaml` resolveu; medir a direção real com a mão antes de fechar a
  ROI de cada painel (o plano de varredura é horizontal, na altura da torre).

### Fase 3 — Provisionar (um nó por vez, conferindo antes do próximo)

```bash
deploy/provision_node.sh lidar-01 1 --udp-host 192.168.2.1 --recreate-venv
deploy/provision_node.sh lidar-02 2 --udp-host 192.168.2.1
# ... até lidar-08 / panel_id 8
```

O `--udp-host 192.168.2.1` aponta o V2 para o Mac **durante a bancada**; a
migração para os IPs definitivos é o bloco do
[§3 de INSTALACAO.md](INSTALACAO.md) (modo `--update`, preserva ROI).

**Ordem sugerida:** `lidar-01` (Pi 4, reprovisionado) primeiro, e um **Pi 3B+
em segundo** — é o gate de CPU do §10 em hardware nunca medido; se reprovar, é
problema de projeto e é melhor saber no segundo nó, não no oitavo.

### Fase 4 — Validar cada nó

```bash
deploy/verify_node.sh lidar-01 --with-sensor
```

Roda test_e2e, `bench_parse` 30k/40k (**critério definitivo nos 3B+**),
test_lidar (com `--with-sensor`), `get_throttled` e o estado do serviço.
Ficam manuais, e o script lembra ao final:

1. **Área livre → `fg=0 tracks=0`** via `node/diag_bg.py` ([§8.6](INSTALACAO_PI.md))
   — o critério que impede calibrar em cima de um fantasma
2. No Mac: `.venv/bin/python server/test_udp_receiver.py --v2 --port 5555` —
   ~30 pkts/s com o `panel_id` certo, `bad=0`

### Runbook da bancada — quem liga o quê, em ordem

1. Ligar o **switch**; adaptador Ethernet do **Mac** numa porta dele.
   **Roteador fica FORA do switch** durante toda a bancada.
2. Mac: Ajustes do Sistema → Geral → Compartilhamento → **Compartilhamento de
   Internet** (Wi-Fi → adaptador Ethernet). O Mac vira `192.168.2.1`.
3. Por nó: plugar **S3 no USB**, cabo de rede no switch, **por último** a
   energia (fonte oficial; Pi 5 = 27 W). ~40 s até `ping lidar-0N.local`.
4. `bootstrap_keys.sh` → `provision_node.sh` → `verify_node.sh` (acima).
5. Nós validados podem ficar ligados; repetir o passo 3 para o próximo.
6. Ao final dos 8: **desligar o Compartilhamento de Internet** e só então
   plugar o roteador no switch.

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

**Janela única de internet.** Depois que o roteador (sem saída) assumir o
switch, não há mais `apt`/`pip` na rede. Nó que ficar de fora da bancada exige
trazer o Mac de volta com o Compartilhamento ligado — e o roteador desplugado.

**NTP pendente até o server-a existir.** O
[deploy/chrony-node.conf](../deploy/chrony-node.conf) aponta para `10.10.0.10`,
que não existe na bancada nem terá esse IP na faixa do roteador. O sistema
funciona sem NTP, mas o timestamp do header V2 fica incomparável entre nós
(~900 ms de desvio medido). Quando os Windows entrarem: server-a como master
NTP e atualizar o `server` do conf para o IP real
([INSTALACAO.md §3](INSTALACAO.md)).

**`udp.host` de bancada ≠ definitivo.** Os nós saem da bancada apontando para o
Mac (`192.168.2.1`). A migração é o loop com `--update` do
[§3 de INSTALACAO.md](INSTALACAO.md) — sem ele o relay real fica com `in=0` nos 8.

**Os 3 Pi 3B+ são o gate do §10 e nunca foram medidos em hardware real.** Se o
`bench_parse.py` reprovar neles, é problema de projeto, não de instalação —
melhor descobrir no segundo nó provisionado, não no oitavo.

**O Pi 5 exige fonte oficial de 27 W.** Carregador GaN de 100 W entrega apenas
3 A em 5 V; o Pi 5 negocia 5 A por USB-C PD e, sem esse perfil, corta a corrente
total de USB para 600 mA e trava. Ver [§2 de INSTALACAO_PI.md](INSTALACAO_PI.md).

**ROI, `angle_offset_deg` e `mirror` dependem da montagem física** e não podem
ser definidos por script. O provisionamento entrega o nó publicando; o ajuste
por painel é o §7 do doc do Pi — direto no `/home/pi/node-config.yaml`.

**Os limiares do guard de calibração** (150 mm de lado, 0,05 m² de área) foram
escolhidos na bancada, não vêm da spec. Se algum painel real for pequeno,
revisar em [server/calibrate.py](../server/calibrate.py).
