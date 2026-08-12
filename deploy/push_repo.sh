#!/usr/bin/env bash
#
# Atualiza o código de um nó SEM internet na rede: empurra o HEAD commitado
# desta máquina por SSH e reinicia o serviço.
#
# É o substituto do `git pull` do §11 de docs/INSTALACAO_PI.md para a rede
# definitiva (roteador sem saída para fora, sem GitHub). Usa `git archive`,
# então só vai o que está COMMITADO — árvore suja não viaja. Vai o repo
# inteiro (~2 MB, incluindo legacy/ — o mesmo layout do git clone); o .venv
# do nó não é tocado (não é rastreado).
#
# NÃO toca no /home/pi/node-config.yaml — ele vive fora do repo de propósito.
#
# Uso:
#   deploy/push_repo.sh lidar-03               # um nó
#   deploy/push_repo.sh lidar-0{1..8}          # a frota
#   NO_RESTART=1 deploy/push_repo.sh lidar-03  # só copia, não reinicia
#
# ⚠️ O restart refaz o baseline — rode com a área dos painéis LIVRE, nunca
#    durante operação com público (§9 de docs/INSTALACAO_PI.md).

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$HERE")"
SSH_USER="${SSH_USER:-pi}"
NO_RESTART="${NO_RESTART:-}"
REMOTE_REPO="/home/$SSH_USER/lidarmapper"

[ "$#" -ge 1 ] || { echo "uso: deploy/push_repo.sh <host> [host...]" >&2; exit 1; }

HEAD_SHA="$(git -C "$REPO_ROOT" rev-parse --short HEAD)"
if ! git -C "$REPO_ROOT" diff --quiet HEAD -- ':!*.md' 2>/dev/null; then
    echo "AVISO: há mudanças NÃO COMMITADAS (fora .md) — elas NÃO serão enviadas." >&2
fi

# requirements mudou desde o commit anterior? pip install exigiria internet.
if git -C "$REPO_ROOT" diff --quiet HEAD~1 HEAD -- node/requirements-pi.txt 2>/dev/null; then
    REQ_CHANGED=""
else
    REQ_CHANGED=1
fi

echo "enviando HEAD $HEAD_SHA para: $*"
echo

ok=0; fail=0
for h in "$@"; do
    target="$SSH_USER@$h"

    if ! ssh -o BatchMode=yes -o ConnectTimeout=5 "$target" true 2>/dev/null; then
        echo "  FALHA $h — inacessível"
        fail=$((fail + 1))
        continue
    fi

    # tar do HEAD direto no destino; extração atômica o suficiente para o
    # nosso caso (o serviço só relê os .py no restart).
    if git -C "$REPO_ROOT" archive HEAD \
        | ssh "$target" "mkdir -p $REMOTE_REPO && tar -x -C $REMOTE_REPO"; then
        if [ -n "$NO_RESTART" ]; then
            echo "  OK    $h — código em $HEAD_SHA (sem restart)"
        else
            ssh "$target" 'sudo systemctl restart lidarmapper' \
                && echo "  OK    $h — código em $HEAD_SHA, serviço reiniciado (baseline refeito!)" \
                || { echo "  FALHA $h — código copiado mas restart falhou"; fail=$((fail + 1)); continue; }
        fi
        [ -n "$REQ_CHANGED" ] \
            && echo "        ⚠️  node/requirements-pi.txt mudou no último commit — 'pip install' no nó exige internet"
        ok=$((ok + 1))
    else
        echo "  FALHA $h — transferência não concluída"
        fail=$((fail + 1))
    fi
done

echo
echo "resumo: $ok ok, $fail falhas"
[ "$fail" -eq 0 ]
