# Configuração do TouchDesigner

O TouchDesigner é **consumidor puro** no v3: recebe cursores já em coordenadas
normalizadas `0..1`, um painel por porta, e preenche uma tabela. Sem
homografia, sem demux, sem calibração, sem `struct` além do callback abaixo.

Base: §8 da [spec](../GUIA_LIDARMAPPER_DISTRIBUIDO_1.md) e o
`TOUCHDESIGNER.md` do sistema single-node (em `legacy_recovery/`).

Cada servidor tem seu TD, e os dois projetos são idênticos: **4 UDP In DATs nas
portas 6001–6004**. O que muda é qual painel físico está do outro lado — isso é
problema do relay, não do TD.

---

## 1. O que chega na porta

Protocolo V1 (§3.1), pacote binário sem padding, little-endian:

| Bloco | Formato | Bytes | Campos |
|---|---|---|---|
| Header | `<IdH` | 14 | `frame` (uint32), `timestamp` (float64, epoch Unix), `num_points` (uint16) |
| Ponto × N | `<Ifff` | 16 cada | `id` (uint32), `x` (float32 `0..1`), `y` (float32 `0..1`), `confidence` (float32) |

Tamanho total = `14 + 16 × N`. Com o cap de 10 tracks, 174 B; nunca fragmenta.

`x`/`y` já são a posição **no painel**: `(0,0)` é o canto superior esquerdo,
`(1,1)` o inferior direito, conforme a calibração feita no servidor.

A definição canônica desses formatos é [shared/protocol.py](../shared/protocol.py).

---

## 2. Montagem — 4 canais por servidor

Para cada painel `N` de 1 a 4, crie:

**a) Um Table DAT** chamado `touches_p1` (…`p2`, `p3`, `p4`), com **uma única
linha de cabeçalho**:

```
id    x    y    confidence
```

**b) Um Text DAT** com o conteúdo de
[deploy/td/udp_callback_v1.py](../deploy/td/udp_callback_v1.py), mudando só a
constante do topo:

```python
TABLE = 'touches_p1'      # 'touches_p2' / 'touches_p3' / 'touches_p4'
```

**c) Um UDP In DAT** com estes parâmetros:

| Parâmetro | Valor |
|---|---|
| Active | On |
| Network Address | `127.0.0.1` |
| Network Port | `6001` (p2 → 6002, p3 → 6003, p4 → 6004) |
| **Format** | **Binary** |
| Callbacks DAT | o Text DAT do item (b) |

> ⚠️ `Format: Binary` não é opcional — é o que dá acesso aos bytes crus no
> argumento `bytes_` do callback. Em `Text` o pacote chega mutilado.

O callback é o mesmo do sistema single-node, byte por byte; a única adição é a
constante `TABLE`.

### Tabela de referência

| Painel (server-a) | Painel (server-b) | Porta | Table DAT |
|---|---|---|---|
| 1 | 5 | 6001 | `touches_p1` |
| 2 | 6 | 6002 | `touches_p2` |
| 3 | 7 | 6003 | `touches_p3` |
| 4 | 8 | 6004 | `touches_p4` |

O TD do server-b usa os mesmos nomes `touches_p1..p4` — o `panel_id` real já
foi resolvido pelo relay.

---

## 3. Teste sem hardware

Com o relay e o simulador rodando no servidor
([INSTALACAO_SERVIDOR.md §6](INSTALACAO_SERVIDOR.md#6-smoke-test-sem-nenhum-pi)),
e o painel 1 calibrado, o `touches_p1` deve encher com 2 linhas girando a
30 Hz. É o teste que valida o TD antes de qualquer Pi existir.

Se o simulador estiver rodando mas nada aparecer, valide fora do TD primeiro:

```
.venv\Scripts\python server\test_udp_receiver.py --v1 --port 6001 --raw
```

Se esse comando mostra frames e o TD não, o problema está no DAT (porta,
`Format`, callback). Se ele também não mostra nada, o problema está antes do TD.

> Enquanto o `test_udp_receiver.py` estiver escutando a 6001, **ele e o TD
> disputam a porta**. Feche-o antes de testar no TD.

---

## 4. Consumindo os cursores

Padrões herdados do sistema single-node, válidos como estão:

- **Replicator COMP por `id`** — a coluna `id` é estável entre frames enquanto
  o cursor vive, então as instâncias não são recriadas a cada frame. É o que dá
  continuidade visual.
- **Conversão para pixels** — multiplique `x` por `1920` e `y` por `1080`, ou
  leia `screen_width_px`/`screen_height_px` do `calib_pN.json` via File In DAT
  se a resolução do painel variar.
- **Suavização extra** — `Filter CHOP` sobre o CHOP gerado do Table DAT. O nó
  já suaviza (`tracker.smoothing: 0.35`), mas o Filter ajuda em projeções
  grandes.
- **IDs entre painéis** — os `id` são únicos **por nó**, não globalmente. Se
  algum efeito precisar de identidade global, componha na hora de consumir:
  `N * 100000 + id`.

---

## 5. Monitor de saúde

`touches_pN` parado por mais de 1 s **com o relay vivo** significa que o
problema é do nó daquele painel, não do TD nem do servidor. Um Timer CHOP
comparando o `frame` do último pacote resolve como indicador em cena.

A fonte de verdade do estado do sistema é o console do relay (`in`/`out`/`age`
por painel) — ver
[INSTALACAO_SERVIDOR.md §8](INSTALACAO_SERVIDOR.md#8-operação-diária).

---

## 6. Troubleshooting

| Sintoma | Causa | O que fazer |
|---|---|---|
| Nenhum pacote chega | relay parado, porta errada, painel sem calibração | conferir o console do relay: `out` daquele painel precisa estar subindo |
| `bytes_` vazio no callback | a build do TD não passa binário nesse argumento | garantir `Format: Binary`; se persistir, trocar a primeira linha do callback por `buf = message.encode('latin-1')` |
| Pacotes chegando, tabela vazia | `num_points = 0` — nada é foreground no nó | conferir o campo `fg=` no log do Pi; refazer o baseline com a área livre |
| Tabela popula, mas o cursor pula | pacote inconsistente sendo descartado pelo `return` de tamanho | conferir se o relay e o Pi estão na mesma versão do repo |
| Coordenadas espelhadas ou giradas | calibração feita fora de ordem, ou montagem do sensor | recalibrar na ordem TL → TR → BR → BL; conferir `mirror`/`angle_offset_deg` no nó |
| Cursor responde no painel errado | `out_port` trocado no `config_server.yaml` | conferir a tabela do §2 |
| Instâncias do Replicator piscando | replicando por índice de linha em vez de `id` | usar a coluna `id` como chave |

---

## 7. Pendência: alvos de calibração desenhados pelo TD

O calibrador do servidor tem um modo `--target-source td` que envia, por OSC:

```
/calib/target  <panel_id:int>  <corner_idx:int>  <u:float>  <v:float>
```

- `corner_idx` de `0` a `3` na ordem TL → TR → BR → BL;
- `u`/`v` em coordenadas normalizadas do painel (por padrão `0.06` / `0.94`);
- ao final, `/calib/target 0 -1 0.0 0.0` significa **apagar tudo**.

**A contraparte no TD ainda não existe.** Quem for construí-la precisa de um
COMP que: escute OSC na porta configurada em `osc.port`, acenda um alvo
(círculo + cruz, bem visível) na posição `(u,v)` do painel `panel_id`, apague
os demais, e apague todos ao receber `corner_idx = -1`.

Esse modo é o que permite calibrar com processador de vídeo (Novastar,
Colorlight) ou canvas único, onde o pygame do modo `local` não consegue
desenhar direto no painel. Enquanto ele não existir, a calibração depende de
saída de vídeo direta da GPU — ver
[INSTALACAO_SERVIDOR.md §7](INSTALACAO_SERVIDOR.md#7-calibração-de-um-painel).
