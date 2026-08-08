#!/usr/bin/env bash
#
# Instala deploy/authorized_keys nos nós da frota.
#
# Rode a partir de uma máquina QUE JÁ TENHA ACESSO aos nós — o script usa o
# próprio SSH para se conectar. Depois de um `git pull` que traga chaves novas,
# este é o comando que faz a mudança valer nos Pis.
#
# Uso:
#   deploy/sync_authorized_keys.sh                 # lidar-01 .. lidar-08
#   deploy/sync_authorized_keys.sh lidar-03        # só um nó
#   deploy/sync_authorized_keys.sh 10.10.0.23 10.10.0.24
#   SSH_USER=outro deploy/sync_authorized_keys.sh  # usuário != pi
#   DRY_RUN=1 deploy/sync_authorized_keys.sh       # só mostra o que faria
#
# O arquivo remoto é SUBSTITUÍDO pelo conteúdo do repo (é a fonte de verdade),
# com backup em ~/.ssh/authorized_keys.bak no nó. Chaves adicionadas à mão
# num nó e não commitadas aqui SÃO PERDIDAS no sync — é assim de propósito:
# senão não há como revogar acesso de forma confiável.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
KEYS="$HERE/authorized_keys"
SSH_USER="${SSH_USER:-pi}"
DRY_RUN="${DRY_RUN:-}"

if [ ! -f "$KEYS" ]; then
    echo "ERRO: $KEYS não encontrado." >&2
    exit 1
fi

# Valida cada linha de chave antes de tocar em qualquer nó: um authorized_keys
# corrompido tranca a frota inteira para fora.
n_keys=0
while IFS= read -r line; do
    case "$line" in
        ''|\#*) continue ;;
    esac
    if ! printf '%s\n' "$line" | ssh-keygen -l -f /dev/stdin >/dev/null 2>&1; then
        echo "ERRO: linha inválida em authorized_keys:" >&2
        echo "      ${line:0:60}..." >&2
        exit 1
    fi
    n_keys=$((n_keys + 1))
done < "$KEYS"

if [ "$n_keys" -eq 0 ]; then
    echo "ERRO: nenhuma chave válida em $KEYS — abortando (isso trancaria a frota)." >&2
    exit 1
fi

if [ "$#" -gt 0 ]; then
    HOSTS=("$@")
else
    HOSTS=(lidar-01 lidar-02 lidar-03 lidar-04 lidar-05 lidar-06 lidar-07 lidar-08)
fi

echo "$n_keys chave(s) válida(s) em authorized_keys"
echo "alvos: ${HOSTS[*]}"
echo

ok=0; skip=0; fail=0
for h in "${HOSTS[@]}"; do
    target="$SSH_USER@$h"

    if ! ssh -o BatchMode=yes -o ConnectTimeout=5 "$target" true 2>/dev/null; then
        echo "  SKIP  $h — inacessível (offline, ou esta máquina não está autorizada nele)"
        skip=$((skip + 1))
        continue
    fi

    if [ -n "$DRY_RUN" ]; then
        echo "  DRY   $h — instalaria $n_keys chave(s)"
        ok=$((ok + 1))
        continue
    fi

    # Escreve num temporário e move: se a conexão cair no meio, o
    # authorized_keys em uso continua intacto.
    if ssh "$target" '
            set -e
            umask 077
            mkdir -p ~/.ssh
            cat > ~/.ssh/authorized_keys.new
            [ -f ~/.ssh/authorized_keys ] && cp -f ~/.ssh/authorized_keys ~/.ssh/authorized_keys.bak
            mv ~/.ssh/authorized_keys.new ~/.ssh/authorized_keys
            chmod 600 ~/.ssh/authorized_keys
        ' < "$KEYS"; then
        echo "  OK    $h — $n_keys chave(s) instalada(s)"
        ok=$((ok + 1))
    else
        echo "  FALHA $h — sync não concluído (authorized_keys antigo preservado)"
        fail=$((fail + 1))
    fi
done

echo
echo "resumo: $ok ok, $skip inacessíveis, $fail falhas"
[ "$fail" -eq 0 ]
