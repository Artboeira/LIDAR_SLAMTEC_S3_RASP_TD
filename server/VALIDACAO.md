# Validação da Sessão W2

Escopo: middleware de servidor (`server/`) + protocolo compartilhado
(`shared/protocol.py`). Alvo: Windows dos servidores; roda em Linux/macOS
para desenvolvimento.

## 1. Automatizada (sem hardware, sem TD, sem Max)

```bash
.venv/bin/python w2_validate.py
```

Cobre:

1. `shared/test_protocol.py` — 24 asserts de round-trip V1/V2 (0 pts,
   max_points, id > uint32, version errada strict/non-strict, tamanho
   inconsistente, panel_id fora de uint8, byte-identidade com o
   `TOUCHDESIGNER.md`).
2. Simulador V2: 4 painéis × 30 Hz por 1 s → ≥100 pacotes, 4 panel_ids
   distintos, demux correto.
3. Relay ponta a ponta: `test_node_sim` (2 painéis) → `server_relay.py`
   → listeners V1 (portas out_port) + OSC (porta osc.port):
   - ≥25 pacotes V1 por painel em 1 s
   - último frame com coords em `[0..1]` (clip funcionou)
   - `/touch/N` disparou (down)
   - debounce: ≤ 4 hits por painel com `pattern=circle` (2 ids persistentes)
   - **hot-reload por mtime**: regravar `calib_pN.json` durante o run
     não interrompe o fluxo; segunda rodada acumula pacotes.
4. Coletor do `calibrate.py`: filtro por `--panel N` preciso (painel 5
   recebe seus 20 pontos; ignora painel 3 na mesma porta) e mediana
   exata em toque estático.

Última execução: **12/12 PASS**.

## 2. Roteiro manual — ponta a ponta com o simulador

Cenário completo do prompt W2, item 6 do guia. Requer 5 shells na mesma
máquina (ou 4 + o pygame). Sem Pi ligado, sem TD, sem Max.

### Setup (uma vez)

```bash
python3 -m venv .venv
.venv/bin/pip install -r server/requirements-server.txt
```

### Shell 1 — simulador dos 4 nós

```bash
.venv/bin/python server/test_node_sim.py --panels 1,2,3,4 --pattern circle
```

Cada painel manda 30 pkts/s ao relay em `127.0.0.1:5555`.

### Shell 2 — calibração do painel 1 (fonte local)

Antes de rodar, apague `server/calib_p*.json` se existirem, para o relay
ignorar os painéis (segunda linha de defesa: sem calibração, não repassa).

```bash
.venv/bin/python server/calibrate.py --panel 1 --target-source local --no-fullscreen
```

Uma janela pygame abre com o alvo do TOP-LEFT aceso. Como o simulador
está mandando toques em círculo, os cantos coletados não vão ficar
perfeitamente nos alvos — o objetivo aqui é ver o fluxo funcionando.
Aperte ESPAÇO 4x (uma por canto). Ao final, `server/calib_p1.json` é
gravado e o erro por canto aparece no log.

Para um erro pequeno de verdade, use `--target-source td` OU calibre
com um objeto real fixo em cada canto. O modo `local` com o simulador
serve para exercitar a UX.

### Shell 3 — relay

Rode DEPOIS da calibração (para o painel 1 ter H):

```bash
.venv/bin/python server/server_relay.py
```

Você verá no log a cada 1 s:

```
p1[C] in=30 out=30 drop=0 down=2 age=0.0s   p2[-] in=30 out=0 drop=0 down=0 age=0.0s   ...
```

- `[C]` = painel calibrado (repassando); `[-]` = sem calib.
- `out` só cresce em painéis com calib.
- `down` é o total de `/touch/N` enviados.

### Shell 4 — receptor V1 (o que o TD vai receber)

```bash
.venv/bin/python server/test_udp_receiver.py --v1 --port 6001
```

Coords devem ficar em `[0..1]`. Se sair fora, o `clip_out_of_range: true`
do `config_server.yaml` está descartando. Use `--raw` para ver frame a
frame.

### Shell 5 — sniffer OSC (opcional, no lugar do Max)

Não temos um receptor OSC pronto no repo; o sniffer no `w2_validate.py`
serve. Para observar `/touch/N` batendo, o log do relay já reporta
`down=N` — se dois cursores ficam ativos em círculo, esperamos `down=2`
depois do início e nada mais (debounce).

### Testes de robustez

- **Painel sem calibração**: apague `server/calib_p2.json` e reinicie
  o relay. Ele loga uma vez `[p2] sem calibração em ... — pacotes
  ignorados` e nunca envia V1 pro painel 2.
- **Hot-reload**: com o relay rodando, execute a calibração do painel 2.
  Assim que `calib_p2.json` é gravado, o próximo pacote do painel 2 é
  repassado. Sem reiniciar o relay.
- **Version mismatch**: o relay descarta silenciosamente (loga uma vez)
  pacotes com `version != 2`. Simule com um script que altere o byte 0.
- **Painel fora do config**: `--panels 9` no sim → relay loga uma vez
  `panel_id=9 fora do config_server.yaml — pacotes ignorados`.

## 3. NÃO coberto aqui (requer infra externa)

- Pygame fullscreen no `display_index` mapeado a cada painel (só faz
  sentido nos servidores de produção com múltiplas saídas de vídeo).
- TouchDesigner consumindo V1 pelo callback do `TOUCHDESIGNER.md`.
- Max recebendo `/touch/N` na `:7500`.
- Latência real entre máquinas (requer NTP sincronizado — §6 do guia).
- Modo `--target-source td`: precisa da contraparte no TD que consome
  `/calib/target <panel_id> <corner> <u> <v>` (fora do escopo desta
  sessão, conforme o prompt).
- Windows: paths, sockets e serviço (NSSM/Task Scheduler). O código foi
  escrito com Windows em mente (SO_REUSEADDR, sem systemd, `os.path.join`
  em vez de `/`), mas o smoke rodou em macOS/Linux. Validar no bring-up
  do server-a/server-b.

## 4. Cross-check com o legacy (herança de bytes)

- `pack_v1` do `shared/protocol.py` é **byte-idêntico** ao `pack_frame`
  do `legacy/publisher.py` (mesmo `<IdH` header e `<Ifff` ponto).
  Verificado no `shared/test_protocol.py`.
- `homography.py` do server/ é cópia intacta do legacy/.
- Insets 0.06/0.94 e ordem TL→TR→BR→BL da calibração vêm do
  `legacy/calibrate.py`.
