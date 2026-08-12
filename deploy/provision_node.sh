#!/usr/bin/env bash
#
# Provisiona UM nó da frota, do primeiro SSH ao serviço rodando no boot.
#
# Roda na máquina de trabalho (macOS/Linux), contra um Pi já acessível por
# chave (rode deploy/bootstrap_keys.sh antes, uma vez). Idempotente: cada
# etapa detecta o estado antes de agir — reexecutar é seguro e é o jeito
# oficial de "consertar" um nó meio-provisionado.
#
# Uso:
#   deploy/provision_node.sh <host> <panel_id> [opções]
#
#   deploy/provision_node.sh lidar-01 1
#   deploy/provision_node.sh lidar-01 1 --udp-host 192.168.2.1   # bancada: relay no Mac
#   deploy/provision_node.sh lidar-01 1 --recreate-venv          # refaz o venv do zero
#   deploy/provision_node.sh lidar-03 3 --rewrite-config         # força node-config.yaml novo
#   DRY_RUN=1 deploy/provision_node.sh lidar-01 1                # só mostra o que faria
#
# Opções:
#   --udp-host <ip>     destino V2 do nó. Default: derivado do panel_id
#                       (1-4 → 10.10.0.10, 5-8 → 10.10.0.11, §3 de INSTALACAO.md)
#   --rewrite-config    sobrescreve /home/pi/node-config.yaml existente
#                       (padrão: preserva, porque ROI/mirror são ajuste manual)
#   --recreate-venv     apaga e recria o venv (necessário no lidar-01 original,
#                       que foi criado com --system-site-packages)
#   --no-reboot         não reinicia mesmo que alguma etapa peça
#
# Exige internet NO PI (apt + pip + git clone). Sem internet o script aborta
# com instrução — nada de fallback offline silencioso: foi um provisionamento
# manual divergente que criou o caso especial do lidar-01 (docs/PROVISIONAMENTO_FROTA.md §1).

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$HERE")"
SSH_USER="${SSH_USER:-pi}"
DRY_RUN="${DRY_RUN:-}"
REMOTE_REPO="/home/$SSH_USER/lidarmapper"
NODE_CONFIG="/home/$SSH_USER/node-config.yaml"

# ---------- argumentos ----------

usage() { sed -n '2,30p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 1; }

[ "$#" -ge 2 ] || usage
HOST="$1"; PANEL_ID="$2"; shift 2

UDP_HOST=""
REWRITE_CONFIG=""
RECREATE_VENV=""
NO_REBOOT=""
while [ "$#" -gt 0 ]; do
    case "$1" in
        --udp-host)       UDP_HOST="$2"; shift 2 ;;
        --rewrite-config) REWRITE_CONFIG=1; shift ;;
        --recreate-venv)  RECREATE_VENV=1; shift ;;
        --no-reboot)      NO_REBOOT=1; shift ;;
        *) echo "ERRO: opção desconhecida: $1" >&2; usage ;;
    esac
done

case "$PANEL_ID" in
    [1-8]) ;;
    *) echo "ERRO: panel_id deve estar em 1..8 (veio '$PANEL_ID')." >&2; exit 1 ;;
esac

# Default do §3 de docs/INSTALACAO.md: painéis 1-4 → server-a, 5-8 → server-b.
if [ -z "$UDP_HOST" ]; then
    if [ "$PANEL_ID" -le 4 ]; then UDP_HOST="10.10.0.10"; else UDP_HOST="10.10.0.11"; fi
fi

TARGET="$SSH_USER@$HOST"
SSH_OPTS=(-o BatchMode=yes -o ConnectTimeout=8)
NEEDS_REBOOT=""

# ---------- helpers ----------

say()  { printf '%s\n' "$*"; }
ok()   { printf '  OK    %s\n' "$*"; }
skip() { printf '  SKIP  %s\n' "$*"; }
warn() { printf '  AVISO %s\n' "$*"; }
die()  { printf '  FALHA %s\n' "$*" >&2; exit 1; }

# Executa no nó. Em DRY_RUN, só imprime.
run() {
    if [ -n "$DRY_RUN" ]; then
        printf '  DRY   ssh %s %q\n' "$TARGET" "$*"
        return 0
    fi
    ssh "${SSH_OPTS[@]}" "$TARGET" "$@"
}

# Checagem read-only: roda de verdade mesmo em DRY_RUN (não altera nada,
# e é o que permite ao dry-run dizer o que SERIA feito).
probe() { ssh "${SSH_OPTS[@]}" "$TARGET" "$@"; }

# ---------- etapa 1: pré-checks ----------

say "== provisionando $HOST (panel_id=$PANEL_ID, udp.host=$UDP_HOST) =="
[ -n "$DRY_RUN" ] && say "   (DRY_RUN: nenhuma alteração será feita no nó)"
say ""
say "-- 1. pré-checks"

probe true 2>/dev/null \
    || die "$HOST não responde a SSH por chave. Rode: deploy/bootstrap_keys.sh $HOST"
ok "SSH por chave"

probe sudo -n true 2>/dev/null \
    || die "sudo sem senha não disponível para $SSH_USER@$HOST (imagem fora do padrão Raspberry Pi OS?)"
ok "sudo sem senha"

MODEL="$(probe cat /proc/device-tree/model 2>/dev/null | tr -d '\0' || echo '?')"
MAC="$(probe cat /sys/class/net/eth0/address 2>/dev/null || echo '?')"
ok "modelo: $MODEL"
ok "MAC eth0: $MAC   <-- anote para a reserva de DHCP (§3 de docs/INSTALACAO.md)"

# ---------- etapa 2: internet ----------

say "-- 2. internet no nó"
if probe 'timeout 8 curl -fsI https://deb.debian.org >/dev/null 2>&1 || timeout 8 wget -q --spider https://deb.debian.org 2>/dev/null'; then
    ok "nó com saída para a internet"
else
    die "sem internet no nó. Ligue o Compartilhamento de Internet do Mac
        (Wi-Fi → adaptador Ethernet) e confirme que o roteador NÃO está no
        switch — dois DHCP na mesma rede quebram tudo. Ver runbook em
        docs/PROVISIONAMENTO_FROTA.md."
fi

# ---------- etapa 3: pacotes ----------

say "-- 3. apt: git, python3-venv, chrony"
if probe 'dpkg -s git python3-venv chrony >/dev/null 2>&1'; then
    skip "já instalados"
else
    run 'sudo apt-get update -qq && sudo apt-get install -y -qq git python3-venv chrony' \
        || die "apt install falhou"
    ok "instalados"
fi

# ---------- etapa 4: grupo dialout ----------

say "-- 4. grupo dialout (porta serial do S3)"
if probe "id -nG $SSH_USER | tr ' ' '\n' | grep -qx dialout"; then
    skip "$SSH_USER já está em dialout"
else
    run "sudo usermod -aG dialout $SSH_USER" || die "usermod falhou"
    ok "adicionado (vale a partir do próximo login/boot)"
    NEEDS_REBOOT=1
fi

# ---------- etapa 5: cortes do §3 (INSTALACAO_PI.md) ----------

say "-- 5. cortes de sistema: wifi/bt off, swap off"
# Imagens novas usam /boot/firmware/config.txt; antigas, /boot/config.txt.
BOOTCFG="$(probe 'if [ -f /boot/firmware/config.txt ]; then echo /boot/firmware/config.txt; else echo /boot/config.txt; fi')"
for OVERLAY in disable-wifi disable-bt; do
    if probe "grep -q '^dtoverlay=$OVERLAY' $BOOTCFG"; then
        skip "dtoverlay=$OVERLAY já em $BOOTCFG"
    else
        run "printf '\ndtoverlay=%s\n' $OVERLAY | sudo tee -a $BOOTCFG >/dev/null" \
            || die "não consegui editar $BOOTCFG"
        ok "dtoverlay=$OVERLAY acrescentado"
        NEEDS_REBOOT=1
    fi
done
if probe 'systemctl is-enabled dphys-swapfile >/dev/null 2>&1'; then
    run 'sudo systemctl disable --now dphys-swapfile' || die "disable dphys-swapfile falhou"
    ok "swap desabilitado"
else
    skip "swap já desabilitado"
fi

# ---------- etapa 6: repositório ----------

say "-- 6. repositório em $REMOTE_REPO"
REPO_URL="$(git -C "$REPO_ROOT" remote get-url origin)"
if probe "[ -d $REMOTE_REPO/.git ]"; then
    run "git -C $REMOTE_REPO pull --ff-only" || die "git pull falhou"
    ok "atualizado (git pull)"
else
    run "git clone --depth 1 $REPO_URL $REMOTE_REPO" || die "git clone falhou"
    ok "clonado de $REPO_URL"
fi

# ---------- etapa 7: venv ----------

say "-- 7. venv + requirements-pi"
if [ -n "$RECREATE_VENV" ] && probe "[ -d $REMOTE_REPO/.venv ]"; then
    run "rm -rf $REMOTE_REPO/.venv"
    ok "venv antigo removido (--recreate-venv)"
fi
if probe "[ -x $REMOTE_REPO/.venv/bin/python ]"; then
    skip "venv já existe"
else
    # SEM --system-site-packages: o venv do lidar-01 original tinha, e é
    # exatamente o tipo de divergência que este script existe para eliminar.
    run "python3 -m venv $REMOTE_REPO/.venv" || die "criação do venv falhou"
    ok "venv criado"
fi
run "$REMOTE_REPO/.venv/bin/pip install -q -r $REMOTE_REPO/node/requirements-pi.txt" \
    || die "pip install falhou"
ok "requirements-pi instalados"
if [ -z "$DRY_RUN" ]; then
    probe "$REMOTE_REPO/.venv/bin/python -c 'import numpy, serial, yaml, ruamel.yaml'" \
        || die "imports básicos falharam no venv do nó"
    ok "imports numpy/serial/yaml/ruamel ok"
fi

# ---------- etapa 8: udev ----------

say "-- 8. udev: /dev/rplidar estável"
if probe "sudo cmp -s $REMOTE_REPO/deploy/99-rplidar.rules /etc/udev/rules.d/99-rplidar.rules"; then
    skip "regra já instalada e idêntica"
else
    run "sudo cp $REMOTE_REPO/deploy/99-rplidar.rules /etc/udev/rules.d/99-rplidar.rules && sudo udevadm control --reload-rules && sudo udevadm trigger" \
        || die "instalação da regra udev falhou"
    ok "regra instalada e recarregada"
fi
if probe "[ -e /dev/rplidar ]"; then
    ok "/dev/rplidar presente"
else
    warn "/dev/rplidar ausente — S3 desplugado? O serviço só publica com o sensor."
fi

# ---------- etapa 9: chrony ----------

say "-- 9. chrony (NTP contra o server-a)"
if probe "sudo cmp -s $REMOTE_REPO/deploy/chrony-node.conf /etc/chrony/conf.d/lidarmapper.conf"; then
    skip "conf já instalada e idêntica"
else
    run "sudo mkdir -p /etc/chrony/conf.d && sudo cp $REMOTE_REPO/deploy/chrony-node.conf /etc/chrony/conf.d/lidarmapper.conf && sudo systemctl restart chrony" \
        || die "instalação do chrony-node.conf falhou"
    ok "conf instalada, chrony reiniciado"
fi
warn "na bancada o master NTP (10.10.0.10) não existe ainda — sem sincronia até o server-a entrar; o nó funciona normalmente."

# ---------- etapa 10: node-config.yaml (FORA da árvore git) ----------

say "-- 10. $NODE_CONFIG"
RENDER="$REMOTE_REPO/.venv/bin/python $REMOTE_REPO/deploy/render_node_config.py"
if [ -z "$DRY_RUN" ] && probe "[ -f $NODE_CONFIG ]" && [ -z "$REWRITE_CONFIG" ]; then
    # Existe e não é para sobrescrever: só confere os 2 campos gerenciados.
    if probe "$RENDER --panel $PANEL_ID --udp-host $UDP_HOST --check --out $NODE_CONFIG"; then
        skip "já existe e bate com panel_id/udp.host (ROI etc. preservados)"
    else
        warn "já existe mas DIVERGE do esperado — revise, ou rode com --rewrite-config"
    fi
else
    run "$RENDER --panel $PANEL_ID --udp-host $UDP_HOST --out $NODE_CONFIG" \
        || die "geração do node-config.yaml falhou"
    ok "gerado (panel_id=$PANEL_ID, udp.host=$UDP_HOST)"
fi

# ---------- etapa 11: systemd ----------

say "-- 11. systemd: lidarmapper.service"
if probe "sudo cmp -s $REMOTE_REPO/deploy/lidarmapper.service /etc/systemd/system/lidarmapper.service"; then
    skip "unit já instalada e idêntica"
else
    run "sudo cp $REMOTE_REPO/deploy/lidarmapper.service /etc/systemd/system/lidarmapper.service && sudo systemctl daemon-reload" \
        || die "instalação da unit falhou"
    ok "unit instalada"
fi
run "sudo systemctl enable --now lidarmapper" || die "enable --now falhou"
ok "serviço habilitado e iniciado"

# ---------- etapa 12: reboot, se necessário ----------

if [ -n "$NEEDS_REBOOT" ] && [ -z "$NO_REBOOT" ] && [ -z "$DRY_RUN" ]; then
    say "-- 12. reboot (dialout/overlays só valem após reiniciar)"
    run "sudo reboot" || true
    printf '  ...   esperando %s voltar' "$HOST"
    for _ in $(seq 1 30); do
        sleep 5
        if probe true 2>/dev/null; then break; fi
        printf '.'
    done
    printf '\n'
    probe true 2>/dev/null || die "$HOST não voltou do reboot em ~150 s"
    ok "nó de volta"
elif [ -n "$NEEDS_REBOOT" ]; then
    warn "reboot pendente (--no-reboot/DRY_RUN) — dialout e overlays só valem após reiniciar"
fi

# ---------- resumo ----------

say ""
say "== resumo: $HOST =="
if [ -z "$DRY_RUN" ]; then
    say "  modelo   : $MODEL"
    say "  MAC eth0 : $MAC"
    say "  panel_id : $PANEL_ID   udp.host: $UDP_HOST"
    say "  rplidar  : $(probe '[ -e /dev/rplidar ] && echo presente || echo AUSENTE')"
    say "  serviço  : $(probe systemctl is-active lidarmapper || true)"
    say "  throttled: $(probe vcgencmd get_throttled 2>/dev/null || echo '?')"
    say ""
    say "próximos passos:"
    say "  deploy/verify_node.sh $HOST --with-sensor"
    say "  (com a área livre) ssh $TARGET '$REMOTE_REPO/.venv/bin/python -u $REMOTE_REPO/node/diag_bg.py'"
else
    say "  DRY_RUN concluído — nada foi alterado."
fi
