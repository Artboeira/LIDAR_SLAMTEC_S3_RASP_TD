#!/usr/bin/env bash
#
# Primeiro acesso aos nós: instala a chave pública desta máquina via senha.
#
# É o passo que o sync_authorized_keys.sh NÃO cobre — aquele usa BatchMode e
# pula nós onde esta máquina ainda não entra. Este roda ssh-copy-id, que pede
# a senha (pi123, a mesma nos 8 nós) uma vez por host. Depois disso, tudo
# (provision_node.sh, push_repo.sh, sync_authorized_keys.sh) entra sem senha.
#
# Uso:
#   deploy/bootstrap_keys.sh                 # lidar-01 .. lidar-08
#   deploy/bootstrap_keys.sh lidar-01        # um nó só (bancada: um Pi por vez)
#   deploy/bootstrap_keys.sh 192.168.2.7     # por IP, se o mDNS falhar
#   SSH_USER=outro deploy/bootstrap_keys.sh  # usuário != pi
#
# Idempotente: nó que já aceita a chave é pulado sem pedir senha.

set -euo pipefail

SSH_USER="${SSH_USER:-pi}"
PUBKEY="${PUBKEY:-$HOME/.ssh/id_ed25519.pub}"

if [ ! -f "$PUBKEY" ]; then
    echo "ERRO: $PUBKEY não existe. Gere com: ssh-keygen -t ed25519" >&2
    exit 1
fi

if [ "$#" -gt 0 ]; then
    HOSTS=("$@")
else
    HOSTS=(lidar-01 lidar-02 lidar-03 lidar-04 lidar-05 lidar-06 lidar-07 lidar-08)
fi

echo "chave: $PUBKEY ($(ssh-keygen -lf "$PUBKEY" | awk '{print $2}'))"
echo "alvos: ${HOSTS[*]}"
echo

ok=0; skip=0; fail=0
for h in "${HOSTS[@]}"; do
    target="$SSH_USER@$h"

    # Já entra por chave? Então não há o que fazer (e não pedimos senha à toa).
    if ssh -o BatchMode=yes -o ConnectTimeout=5 "$target" true 2>/dev/null; then
        echo "  SKIP  $h — esta máquina já entra por chave"
        skip=$((skip + 1))
        continue
    fi

    echo "  >>    $h — digite a senha ($SSH_USER / pi123) quando pedir:"
    if ssh-copy-id -i "$PUBKEY" -o ConnectTimeout=8 "$target"; then
        echo "  OK    $h"
        ok=$((ok + 1))
    else
        echo "  FALHA $h — nó offline, hostname errado, ou senha incorreta"
        fail=$((fail + 1))
    fi
done

echo
echo "resumo: $ok instaladas, $skip já ok, $fail falhas"
[ "$fail" -eq 0 ]
