# Configuração TouchDesigner — receber dados do LidarMapper

O `main.py` envia datagramas **UDP binários** pra `udp.host:udp.port`
(default `127.0.0.1:5555`). Não usa JSON — cada pacote é um buffer
empacotado com `struct.pack`, pronto pra `struct.unpack` no TD.

## Formato do datagrama

Tudo little-endian, sem padding.

```
Header  (14 bytes):
  uint32   frame
  float64  timestamp           (segundos, time.time() — Unix epoch)
  uint16   num_points          (N)

Por ponto (16 bytes, repetido N vezes):
  uint32   id
  float32  x                   (0..1, espaço normalizado da tela)
  float32  y                   (0..1)
  float32  confidence          (0..1)

Datagrama total = 14 + 16 * N bytes.
```

Struct format strings:
- header: `"<IdH"`
- ponto: `"<Ifff"`

Com `tracker.max_tracks = 10` o pacote chega a 174 bytes — bem abaixo de
qualquer MTU, sem fragmentação.

## Setup no TouchDesigner

1. **Adicione um UDP In DAT** (`+` → `DAT` → `UDP In`).
2. **Parameters**:
   - `Active`: On
   - `Network Port`: `5555` (ou o que estiver em `config.yaml > udp.port`)
   - `Network Address`: deixe vazio (escuta em todas as interfaces)
   - `Format`: `Binary` (importante — é o que dá acesso aos bytes brutos)
3. **Callbacks**: aponte pra um Text DAT com o handler abaixo.

> Se o LidarMapper estiver em outra máquina, configure `udp.host` no
> `config.yaml` pro IP da máquina do TD (e abra a porta no firewall).

## Callback (Text DAT)

```python
# udp_callback.py — anexar como callback do UDP In DAT
import struct

_HEADER = struct.Struct("<IdH")     # frame, timestamp, num_points  = 14 B
_POINT  = struct.Struct("<Ifff")    # id, x, y, confidence          = 16 B


def onReceive(dat, rowIndex, message, bytes_, peer):
    # `bytes_` é o pacote binário cru. Em algumas versões do TD o argumento
    # vem como bytes-like (`bytes` ou `bytearray`); o struct.unpack aceita
    # ambos. Em outras o pacote chega no parâmetro `message` como string.
    # Pega o que tiver conteúdo binário coerente.
    buf = bytes_ if bytes_ else (message.encode('latin-1') if message else None)
    if not buf or len(buf) < _HEADER.size:
        return

    frame, ts, n = _HEADER.unpack_from(buf, 0)
    expected = _HEADER.size + n * _POINT.size
    if len(buf) != expected:
        return   # pacote inconsistente / fragmentado, ignora

    table = op('touches')
    table.clear(keepFirstRow=True)
    for i in range(n):
        pid, x, y, conf = _POINT.unpack_from(buf, _HEADER.size + i * _POINT.size)
        table.appendRow([pid, x, y, conf])
```

Crie um **Table DAT** chamado `touches` com a primeira linha de cabeçalho:

```
id  x  y  confidence
```

A cada pacote UDP, o callback substitui o conteúdo do Table DAT. Outros
operadores (`DAT to CHOP`, `Replicator COMP`, etc.) consomem direto.

## Padrões úteis no TD

- **Instâncias por toque (Replicator COMP):** o `id` é estável entre
  frames, então o Replicator não recria as instâncias enquanto o mesmo
  cursor estiver vivo. Bom pra preservar estado por toque.
- **Coords em pixels:** multiplique `x` por `1920` e `y` por `1080` (ou
  pelo seu output). `calibration.json` salva `screen_width_px` e
  `screen_height_px` caso queira ler do mesmo arquivo via File In DAT.
- **Suavização extra:** o tracker já aplica suavização exponencial
  (`tracker.smoothing`). Se quiser mais filtro, jogue o CHOP gerado
  pelo Table DAT num `Filter CHOP`.

## Debug rápido

- **Sem pacotes chegando:** confirma que `main.py` está rodando
  (`meas/s=...  pub/s=...`). Confirma `udp.host` e `udp.port` no
  `config.yaml` batem com o UDP In DAT. Confirma firewall.
- **Pacotes chegando mas `points: []` (Table DAT vazio):** o sensor não
  vê nada como foreground. Mexa a mão em frente do LIDAR; no terminal,
  o log do `main.py` deve mostrar `fg=N` subindo. Se ficar zero,
  reinicie o `main.py` com a área *realmente* livre durante o baseline.
- **Coords espelhadas/erradas:** recalibre com `python calibrate.py`
  (ordem dos cantos: TL → TR → BR → BL).
- **`bytes_` chega vazio no callback:** sua versão de TD não passa
  binário nesse argumento. Mude o UDP In DAT pra `Format: Binary` e,
  se necessário, troque a primeira linha do callback por
  `buf = message.encode('latin-1')` (TD passa string com bytes brutos
  em algumas builds).

## Validação fora do TD

```powershell
python test_udp_receiver.py             # resumo (fps, latência, count)
python test_udp_receiver.py --raw       # decoded por pacote em uma linha
```
