# Validação da Sessão W1

Escopo: nó Pi (`node/`) — parser vetorizado + pipeline até publicação V2.

## 1. Automatizada (sem hardware)

```bash
.venv/bin/python w1_validate.py
```

Cobre:

1. **`config.load` valida `udp.panel_id` obrigatório** (1..8, sem
   default silencioso — §14 do guia). Sem `panel_id` no YAML → `ValueError`
   com mensagem clara.
2. **Parser vetorizado** (`_parse_legacy_scan`): decodifica 200 amostras
   sintéticas em Legacy Scan Mode, distâncias com erro 0 (bits inteiros),
   ângulo com erro < 0.02° (Q6), quality preservado, `new_scan` só no
   primeiro sample, sample corrompido detectado pelo check bit.
3. **`bench_parse.py`**: throughput ≥ 100k samples/s na máquina dev.
   Última run mediu **~18 M samples/s no dev → 0.2 % de core projetado
   para 30 kHz**; folga confortável.
4. **`node/test_e2e.py`**: pipeline com medidas sintéticas + 2 cursores
   → publicação V2 via loopback → receptor com `shared/protocol.unpack_v2`
   → asserts em version, panel_id, coords em mm, id persistente, frame
   crescente.
5. **Cross-check W1↔W2**: simulador V2 → `server_relay` (do W2) →
   listener V1 recebendo pacotes em `[0..1]`. Confirma que o contrato
   §3 do guia (V2 na entrada do relay) casa nos dois lados.

Última execução: **21/21 PASS**.

## 2. Bench do §10 (gate de CPU no 3B+)

```bash
python node/bench_parse.py --hz 30000        # requisito nominal do S3
python node/bench_parse.py --hz 40000        # margem de segurança
```

Interpretação (§10 do guia):

| Máquina | %core p/ 30k amostras/s | Veredicto |
|---|---|---|
| dev x86 típico | ~0.2 % | referência de sanidade |
| Pi 5 (1 core) | < 12 % | quase certo que cabe no 3B+ |
| Pi 5 (1 core) | 12–20 % | validar em 3B+ real |
| Pi 5 (1 core) | > 20 % | otimizar mais antes de escalar |
| **3B+ real** | **≤ 30 %** | **critério definitivo** |

O bench mede o hot path INTEIRO (parse → project → BG mask → ROI),
não só o parser — reflete o que o `main.py` faz por chunk.

## 3. Roteiro com o RPLIDAR S3 na USB (só com hardware)

Só pode rodar na bancada com o sensor conectado.

### Bring-up passo a passo (checklist do §12 do guia, itens do node)

```bash
# 1. sanity da porta
ls -l /dev/rplidar     # symlink udev (§6)

# 2. smoke sem hardware (não precisa do sensor)
.venv/bin/python node/test_e2e.py

# 3. teste do sensor: meas/s nominal, scans/s, estabilidade
.venv/bin/python node/test_lidar.py --duration 30
# esperado: scans/s ≈ 8-15 Hz, meas/s alto (vetorizado),
# reconnects=0, desyncs baixo/estável

# 4. gate do §10 no HARDWARE de destino:
python3 node/bench_parse.py
# no 3B+, projetado deve ficar <= 30% de um core

# 5. main.py publicando ao servidor
#    - edite config.yaml: udp.panel_id = N, udp.host = server-a/b, ROI
.venv/bin/python node/main.py
# esperado no log:
#   meas/s=~xxxx  scans/s=~10  fg=~N  tracks=K  pub/s=30  +30 frames

# 6. servidor recebendo (num outro shell):
.venv/bin/python server/test_udp_receiver.py --v2 --port 5555
# esperado: 30 pkts/s desse nó, panel_id correto, tamanhos coerentes
```

### Critérios de aprovação do painel

- `main.py` no 3B+ consumindo ≤ 70 % de um core (§10 do guia)
- pub/s estável em 30 Hz
- sem `vcgencmd get_throttled` != 0x0 por 1 h
- `test_udp_receiver.py --v2` no servidor mostra `bad=0`, `panel_id` certo

## 4. NÃO coberto aqui

- Resiliência real (desconectar/reconectar USB do S3 durante um run) —
  requer sensor físico.
- `journalctl -u lidarmapper -f` (systemd unit no §6 do guia) — precisa
  de deploy no Pi real.
- Interferência entre S3 vizinhos coplanares (§10) — só aparece no
  bring-up do segundo painel.
- Sync NTP entre nós — precisa de `chrony` no Pi (§6). O `timestamp`
  do V2 depende disso pra medição de latência no TD ter sentido.
