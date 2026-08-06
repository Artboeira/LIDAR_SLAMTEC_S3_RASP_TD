# udp_callback_v1.py — callback do UDP In DAT no TouchDesigner (§8 do guia).
#
# Cole este conteúdo num Text DAT e aponte o parâmetro `Callbacks` do UDP In
# DAT para ele. UM Text DAT POR PAINEL — a única coisa que muda entre eles é a
# constante TABLE.
#
#   UDP In DAT p1 -> callback com TABLE = 'touches_p1' -> porta 6001
#   UDP In DAT p2 -> callback com TABLE = 'touches_p2' -> porta 6002
#   UDP In DAT p3 -> callback com TABLE = 'touches_p3' -> porta 6003
#   UDP In DAT p4 -> callback com TABLE = 'touches_p4' -> porta 6004
#
# O TD só consome: as coordenadas já chegam em [0..1] com a homografia
# aplicada pelo server_relay.py. Nada de calibração, demux ou matemática aqui.
#
# Os formatos abaixo são o protocolo V1 do §3.1 do guia e são byte-idênticos
# aos de shared/protocol.py — este arquivo é a única exceção à regra do
# CLAUDE.md ("struct só em shared/protocol.py"), porque roda dentro do
# TouchDesigner, que não importa o repo.

import struct

_HEADER = struct.Struct("<IdH")     # frame, timestamp, num_points  = 14 B
_POINT  = struct.Struct("<Ifff")    # id, x, y, confidence          = 16 B

TABLE = 'touches_p1'                # p2/p3/p4 nos outros DATs


def onReceive(dat, rowIndex, message, bytes_, peer):
    # Em algumas builds do TD o pacote chega em `bytes_`; em outras vem como
    # string em `message`. Pega o que tiver conteúdo.
    buf = bytes_ if bytes_ else (message.encode('latin-1') if message else None)
    if not buf or len(buf) < _HEADER.size:
        return
    frame, ts, n = _HEADER.unpack_from(buf, 0)
    if len(buf) != _HEADER.size + n * _POINT.size:
        return   # pacote inconsistente / fragmentado, ignora
    table = op(TABLE)
    table.clear(keepFirstRow=True)
    for i in range(n):
        pid, x, y, conf = _POINT.unpack_from(buf, _HEADER.size + i * _POINT.size)
        table.appendRow([pid, x, y, conf])
