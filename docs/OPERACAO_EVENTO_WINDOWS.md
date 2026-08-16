# Operação do evento no Windows — fleet_bridge, calibração e rotina diária

Guia para operar o sistema inteiro a partir de um servidor Windows, sem a
máquina de desenvolvimento: rodar a ponte dos 8 painéis, recalibrar uma tela,
refazer baseline, ajustar config de nó, e a rotina de ligar/desligar do evento.

Pré-requisito de leitura: [MANUAL_DE_CAMPO.md](MANUAL_DE_CAMPO.md) (a visão
geral, os runbooks e a **tabela canônica da frota** — §2 de lá). Este guia
assume a frota já provisionada e calibrada; a instalação base do servidor é
[INSTALACAO_SERVIDOR.md](INSTALACAO_SERVIDOR.md).

---

## 1. Levar o sistema para o Windows

Duas opções — o resultado é o mesmo:

**A. Zip de transferência** (gerado na máquina de dev):
descompacte `lidarmapper_curva.zip` em `C:\lidarmapper`. O zip já contém as
8 calibrações (`server\calib_p1..8.json`).

**B. Git clone** (roteador do evento tem internet):

```powershell
git clone https://github.com/Artboeira/LIDAR_SLAMTEC_S3_RASP_TD.git C:\lidarmapper
```

Depois, em qualquer dos casos, **um comando faz o resto** (Python 3.11+
instalado antes, de python.org, com "Add to PATH"):

```powershell
cd C:\lidarmapper
powershell -ExecutionPolicy Bypass -File deploy\install_server.ps1
```

O instalador cria o venv, instala as dependências, roda o `w2_validate`
(12/12 = ok), libera o firewall (UDP 5555/7000), gera o `start_fleet.bat`
(+ auto-start opcional) e, se você quiser, gera a chave SSH e instala nos 8
nós (pede a senha `pi123` uma vez por nó). Reexecutável — pula o que já fez.

As seções abaixo documentam o que ele faz, para ajuste fino manual.

## 2. SSH do Windows para os nós (o instalador oferece isso)

Manual, se preferir (Windows 10/11 já tem cliente OpenSSH):

```powershell
ssh-keygen -t ed25519          # ENTER nas perguntas (sem passphrase é ok aqui)
# instala em um nó (repita trocando o hostname, lidar-01..08):
type $env:USERPROFILE\.ssh\id_ed25519.pub | ssh pi@lidar-01.local "mkdir -p ~/.ssh && cat >> ~/.ssh/authorized_keys"
```

Teste: `ssh pi@lidar-01.local hostname` → responde sem senha.

Os nós resolvem por mDNS (`lidar-0N.local`). Se o Windows não resolver
`.local`, instale o Bonjour (vem com o iTunes/Bonjour Print Services) ou use
os IPs fixos da reserva DHCP (§6).

## 3. Apontar os nós para o servidor (uma vez, na mudança de máquina)

Cada nó envia o V2 para UM IP (`udp.host`). Descubra o IP do servidor
(`ipconfig`), **fixe-o por reserva DHCP no roteador**, e re-aponte os 8
(o `--update` preserva ROI/offset/baseline; o painel N de cada nó está na
tabela canônica do [MANUAL_DE_CAMPO.md §2](MANUAL_DE_CAMPO.md) — NÃO troque
o número). Loop pronto em bash e PowerShell: runbook R2 do manual.

```powershell
# exemplo: lidar-01 é o painel 1, servidor em 192.168.1.50
ssh pi@lidar-01.local "~/lidarmapper/.venv/bin/python ~/lidarmapper/deploy/render_node_config.py --panel 1 --udp-host 192.168.1.50 --update --out /home/pi/node-config.yaml && sudo systemctl restart lidarmapper"
```

Repita para os 8 (panel_id conforme a tabela da frota). Área da tela livre a
cada restart (§5).

## 4. Operações do dia a dia

Tudo roda a partir de `C:\lidarmapper`, com o venv. **Só um programa pode
ocupar a porta 5555 por vez** — feche um antes de abrir o outro.

### Ponte + monitor dos 8 painéis (o modo normal do evento)

```powershell
.venv\Scripts\python server\fleet_bridge.py --panels 1,2,3,4,5,6,7,8 --dest 127.0.0.1
```

`--dest 127.0.0.1` se o TD roda na mesma máquina; acrescente outros IPs
separados por vírgula se houver um segundo TD. Cartão verde = nó ON.
O TD recebe no OSC In CHOP porta 7000: canais `pN_x1 pN_y1 pN_active1
pN_x2 pN_y2 pN_active2` (0..1, origem embaixo-esquerda, 30 Hz; `active`
segura o último x/y ao soltar). Libere UDP 7000 no firewall do Windows
(regra de entrada) na primeira vez.

### Recalibrar uma tela (radar + teclas)

```powershell
.venv\Scripts\python server\radar_view.py --panel 2 --dest 127.0.0.1 --roi -1400,1400,100,4000
```

Mão no canto físico da tela → tecla **1** (TOP-LEFT), **2** (TOP-RIGHT),
**3** (BOTTOM-RIGHT), **4** (BOTTOM-LEFT) — parado ~2 s em cada — e **S**
salva `server\calib_pN.json`. O fleet_bridge recarrega sozinho (hot-reload),
não precisa reiniciar nada. Canto ruim? Aperte o número de novo antes do S.

### Refazer o baseline de um painel ("calibragem de espaço vazio")

Sempre que aparecer ponto fantasma, ou depois de mover qualquer coisa na
frente de uma tela. **Área da tela LIVRE durante ~15 s**:

```powershell
ssh pi@lidar-03.local "sudo systemctl restart lidarmapper"
```

(hostname conforme a tabela da frota — ex.: painel 2 = lidar-03.)

### Ajustes de config de um nó (ROI, offset, espelho)

O config vivo é `/home/pi/node-config.yaml` no Pi. Ver e editar:

```powershell
ssh pi@lidar-03.local "cat /home/pi/node-config.yaml"
ssh pi@lidar-03.local "sudo sed -i 's/^  y_max: .*/  y_max: 4500.0/' /home/pi/node-config.yaml && sudo systemctl restart lidarmapper"
```

### Diagnóstico rápido de um nó

```powershell
ssh pi@lidar-03.local "systemctl is-active lidarmapper; vcgencmd get_throttled; journalctl -u lidarmapper -n 5 --no-pager -o cat"
```

Linha saudável: `fg=0 tracks=0 pub/s=30 desync=0` (com a área livre).
`diag_bg` completo (setores cegos + fantasmas):

```powershell
ssh pi@lidar-03.local "sudo systemctl stop lidarmapper; cd ~/lidarmapper && .venv/bin/python -u node/diag_bg.py --config /home/pi/node-config.yaml; sudo systemctl start lidarmapper"
```

## 5. Rotina diária do evento (gerador liga/desliga)

O sistema foi desenhado para boot não assistido, mas a ORDEM e a disciplina
importam:

**Ao ligar (manhã):**

1. Roteador e switch primeiro (podem ligar junto com tudo — os Pis
   re-tentam DHCP até conseguir; a ordem só acelera).
2. Pis e telas: ligam sozinhos com a energia. Cada nó captura o **baseline
   nos primeiros ~15 s** — ninguém parado na frente das telas nesse
   primeiro minuto. Gente CIRCULANDO é tolerável; parado vira "fundo".
3. Servidor Windows: ligar, abrir o fleet_bridge (ou deixar no
   Agendador de Tarefas/pasta Iniciar — ver §7). TD por último.
4. Conferir no monitor: 8 cartões verdes, `in=30/s`, calib OK. Cartão
   vermelho → §4 diagnóstico. Fantasma → baseline daquele painel.

**Ao desligar (noite):** só cortar a energia. Os Pis aguentam corte seco
(ext4 com journal; risco baixo mas não zero — ver §6). O S3 que acordar
travado se auto-cura no boot (auto-RESET no reader desde 13/08).

## 6. Riscos do ciclo diário e como já estão mitigados

| Risco | Mitigação |
|---|---|
| **IPs mudarem de um dia pro outro** (DHCP) → nós apontando pro IP velho do servidor = sistema mudo | **Reserva DHCP por MAC no roteador** para o SERVIDOR e para os 8 Pis (MACs na tabela da frota). É a pendência nº 1 — sem isso, um dia o sistema não sobe. |
| S3 acorda travado após corte | Auto-RESET no start do reader (3 tentativas com A5 40). |
| Gente na frente da tela no boot | Disciplina do 1º minuto (§5) — ou refazer baseline do painel (1 comando). |
| Cartão SD corromper com corte seco | Risco baixo; tenha 1–2 cartões reserva gravados — reprovisionar um nó leva ~8 min (`provision_node.sh`) com internet do roteador. |
| Fonte fraca (Pi 5 do p3; 3B do p4) | Pendência de compra: fonte 27 W (Pi 5) e 5V/2,5A+ (3B). Sintoma: `throttled` com bits atuais ≠ 0, quedas. |
| Windows não religa sozinho | BIOS: "Restore on AC Power Loss = Power On"; fleet_bridge no auto-start (§7). |

## 7. Auto-start do fleet_bridge no Windows (opcional, recomendado)

Crie `C:\lidarmapper\start_fleet.bat`:

```bat
cd /d C:\lidarmapper
.venv\Scripts\python server\fleet_bridge.py --panels 1,2,3,4,5,6,7,8 --dest 127.0.0.1
```

e um atalho para ele em
`shell:startup` (Win+R → `shell:startup`). O TD referencia o OSC In na 7000.

---

*Nascido na madrugada de 12→13/08/2026, junto com a calibração das 8 telas.
Em conflito com o guia (`GUIA_LIDARMAPPER_DISTRIBUIDO_1.md`), o guia vence.*
