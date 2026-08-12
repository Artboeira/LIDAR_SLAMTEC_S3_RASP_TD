#!/usr/bin/env bash
#
# Valida um nó provisionado, na ordem do §8 de docs/INSTALACAO_PI.md
# (Fase 4 de docs/PROVISIONAMENTO_FROTA.md). Roda da máquina de trabalho,
# tudo por SSH. Read-only: não altera nada no nó.
#
# Uso:
#   deploy/verify_node.sh <host>                 # sem sensor: e2e + CPU + serviço
#   deploy/verify_node.sh <host> --with-sensor   # + test_lidar (precisa do S3)
#
# O que fica de FORA (manual, exige área livre e julgamento):
#   - node/diag_bg.py e o critério fg=0 tracks=0 do §8.6 — rode antes de calibrar
#   - test_udp_receiver.py --v2 no servidor — é do outro lado do fio
#
# Critérios (§8 / §10 da spec):
#   test_e2e            PASS, exit 0
#   bench_parse 30k     ≤ 30% de um core — CRITÉRIO DEFINITIVO nos 3 Pi 3B+
#   test_lidar          scans/s 8–15, desync=0, reconnects=0
#   get_throttled       0x0 (≠ 0x0 = subtensão/térmica → §2 do doc do Pi)
#   lidarmapper         active

set -uo pipefail   # sem -e: queremos rodar TODOS os testes e somar falhas

SSH_USER="${SSH_USER:-pi}"
REMOTE_REPO="/home/${SSH_USER}/lidarmapper"
PY="$REMOTE_REPO/.venv/bin/python"

[ "$#" -ge 1 ] || { echo "uso: deploy/verify_node.sh <host> [--with-sensor]" >&2; exit 1; }
HOST="$1"; shift
WITH_SENSOR=""
[ "${1:-}" = "--with-sensor" ] && WITH_SENSOR=1

TARGET="$SSH_USER@$HOST"
SSH=(ssh -o BatchMode=yes -o ConnectTimeout=8 "$TARGET")

pass=0; fail=0
ok()   { printf '  PASS  %s\n' "$*"; pass=$((pass + 1)); }
bad()  { printf '  FALHA %s\n' "$*"; fail=$((fail + 1)); }

"${SSH[@]}" true 2>/dev/null || { echo "ERRO: $HOST inacessível por chave." >&2; exit 1; }

MODEL="$("${SSH[@]}" cat /proc/device-tree/model 2>/dev/null | tr -d '\0')"
echo "== verificando $HOST — $MODEL =="
case "$MODEL" in
    *"3 Model B"*) echo "   (Pi 3B+: o bench de CPU aqui é o CRITÉRIO DEFINITIVO do §10)" ;;
esac
echo

# -- 8.1: pipeline sem hardware (ambiente ARM ok) --
echo "-- test_e2e.py (pipeline sem hardware)"
if "${SSH[@]}" "cd $REMOTE_REPO && $PY node/test_e2e.py" >/dev/null 2>&1; then
    ok "test_e2e"
else
    bad "test_e2e — ambiente Python do nó quebrado? rode provision_node.sh de novo"
fi

# -- 8.3: gate de CPU (§10) --
echo "-- bench_parse (gate de CPU do §10)"
for hz in 30000 40000; do
    OUT="$("${SSH[@]}" "cd $REMOTE_REPO && $PY node/bench_parse.py --hz $hz" 2>&1 | tail -3)"
    echo "$OUT" | sed 's/^/        /'
    if echo "$OUT" | grep -q '\[bench\] OK'; then
        ok "bench_parse --hz $hz"
    else
        bad "bench_parse --hz $hz — REPROVOU no gate (problema de projeto, não de instalação)"
    fi
done

# -- 8.2: sensor de verdade (opcional) --
if [ -n "$WITH_SENSOR" ]; then
    echo "-- test_lidar.py --duration 30 (precisa do S3; ~35 s)"
    if ! "${SSH[@]}" "[ -e /dev/rplidar ]"; then
        bad "test_lidar — /dev/rplidar ausente (S3 desplugado? regra udev?)"
    else
        # O serviço segura a porta serial; para, testa, devolve.
        "${SSH[@]}" 'sudo systemctl stop lidarmapper' 2>/dev/null
        OUT="$("${SSH[@]}" "cd $REMOTE_REPO && $PY node/test_lidar.py --duration 30" 2>&1)"
        RC=$?
        "${SSH[@]}" 'sudo systemctl start lidarmapper' 2>/dev/null
        # A saída é uma linha de status reescrita com \r; pega o último frame dela.
        LAST="$(printf '%s' "$OUT" | tr '\r' '\n' | grep 'desync=' | tail -1)"
        TOTAL="$(printf '%s' "$OUT" | tr '\r' '\n' | grep -o 'total medidas: [0-9]*' | grep -o '[0-9]*' || echo 0)"
        printf '        %s\n        total medidas: %s\n' "${LAST:-sem medidas}" "$TOTAL"
        # test_lidar não emite veredicto (sempre exit 0): o critério do §8.2 é
        # desync=0 recon=0 e fluxo real. O meas/s conta só amostras COM eco —
        # em ambiente aberto a maioria das direções não retorna (na bancada:
        # ~6k/s de ~15k/s crus) — então o piso é conservador: 30 s ≥ 100k.
        if [ "$RC" -eq 0 ] && [ "${TOTAL:-0}" -ge 100000 ] \
           && printf '%s' "$LAST" | grep -q 'desync=0 recon=0'; then
            ok "test_lidar (confira acima: scans/s deve estar em 8-15)"
        else
            bad "test_lidar — desync/recon ≠ 0 ou poucas medidas: sensor/cabo/alimentação (§12)"
        fi
    fi
fi

# -- §2: alimentação/térmica --
echo "-- vcgencmd get_throttled"
THR="$("${SSH[@]}" vcgencmd get_throttled 2>/dev/null || echo 'indisponível')"
if [ "$THR" = "throttled=0x0" ]; then
    ok "throttled=0x0"
else
    bad "throttled: $THR — subtensão ou térmica (§2; no Pi 5, fonte de 27 W)"
fi

# -- §9: serviço --
echo "-- serviço lidarmapper"
STATE="$("${SSH[@]}" systemctl is-active lidarmapper 2>/dev/null || true)"
if [ "$STATE" = "active" ]; then
    ok "serviço active"
else
    bad "serviço: $STATE"
fi
echo "        últimas linhas do journal:"
"${SSH[@]}" 'journalctl -u lidarmapper -n 5 --no-pager -o cat' 2>/dev/null | sed 's/^/        /'

# -- resumo --
echo
echo "== $HOST: $pass PASS, $fail FALHA =="
if [ "$fail" -eq 0 ]; then
    cat <<EOF

passos manuais restantes deste nó:
  1. (área LIVRE) ssh $TARGET '$PY -u $REMOTE_REPO/node/diag_bg.py'
     → esperado: ZERO pontos foreground na ROI, exit 0  (§8.6)
  2. no servidor de destino: python server/test_udp_receiver.py --v2 --port 5555
     → ~30 pkts/s com o panel_id deste nó, bad=0
EOF
fi
[ "$fail" -eq 0 ]
