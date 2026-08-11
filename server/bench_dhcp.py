"""
LidarMapper — servidor DHCP mínimo de BANCADA.

Para o cenário em que um Pi é ligado **direto** na ethernet do PC, sem switch e
sem DHCP: nesse caso o nó fica sem IPv4, `ssh pi@lidar-0N` não resolve, e o
IPv6 link-local muda a cada boot (ver `docs/INSTALACAO_PI.md` §1, "Bancada").
Este script entrega um IPv4 fixo e acaba com o problema.

NÃO É UM DHCP DE PRODUÇÃO: um cliente só, sem pool, sem persistência de lease,
sem checagem de conflito. Na instalação real o DHCP é do switch/roteador, com
reserva por MAC conforme o §3 de `docs/INSTALACAO.md`.

Uso (a partir da raiz do repo, com o PC em IP fixo no mesmo /24):

    .venv\\Scripts\\python server\\bench_dhcp.py
    .venv\\Scripts\\python server\\bench_dhcp.py --server 192.168.0.10 --offer 192.168.0.50

Deixe rodando enquanto trabalha no nó. Ele imprime o MAC e o hostname de quem
pedir — que é justamente o dado necessário para a reserva de DHCP depois.

Windows: a porta 67 não exige privilégio de administrador. Se der "endereço já
em uso", provavelmente existe outro DHCP (ou o Internet Connection Sharing)
ativo na mesma interface.

Não usa `struct`: os poucos campos numéricos vão por `int.to_bytes`, o que
mantém a regra do CLAUDE.md (format strings de struct só em shared/protocol.py).
"""
from __future__ import annotations

import argparse
import socket
import sys
import time

MAGIC = bytes([99, 130, 83, 99])

# tipos de mensagem DHCP (RFC 2131)
DISCOVER, OFFER, REQUEST, ACK = 1, 2, 3, 5
_NOMES = {1: "DISCOVER", 3: "REQUEST", 4: "DECLINE", 7: "RELEASE", 8: "INFORM"}


def _opcoes(msg_type: int, servidor: str, mascara: str, lease_s: int) -> bytes:
    o = bytearray()
    o += bytes([53, 1, msg_type])                                # message type
    o += bytes([1, 4]) + socket.inet_aton(mascara)               # subnet mask
    o += bytes([3, 4]) + socket.inet_aton(servidor)              # router
    o += bytes([51, 4]) + lease_s.to_bytes(4, "big")             # lease time
    o += bytes([54, 4]) + socket.inet_aton(servidor)             # server id
    o += bytes([255])                                            # end
    return bytes(o)


def _resposta(req: bytes, msg_type: int, servidor: str, oferta: str,
              mascara: str, lease_s: int) -> bytes:
    """Monta o BOOTREPLY ecoando xid, flags e chaddr do pedido."""
    p = bytearray()
    p += bytes([2, 1, 6, 0])                    # op=BOOTREPLY, ethernet, hlen 6
    p += req[4:8]                               # xid
    p += b"\x00\x00" + req[10:12]               # secs, flags
    p += socket.inet_aton("0.0.0.0")            # ciaddr
    p += socket.inet_aton(oferta)               # yiaddr
    p += socket.inet_aton(servidor)             # siaddr
    p += socket.inet_aton("0.0.0.0")            # giaddr
    p += req[28:44]                             # chaddr (16 B)
    p += b"\x00" * 64                           # sname
    p += b"\x00" * 128                          # file
    p += MAGIC
    p += _opcoes(msg_type, servidor, mascara, lease_s)
    return bytes(p)


def _le_opcao(req: bytes, alvo: int) -> bytes | None:
    i = 240
    while i + 2 <= len(req):
        opt, ln = req[i], req[i + 1]
        if opt == 255:
            break
        if opt == alvo:
            return req[i + 2:i + 2 + ln]
        i += 2 + ln
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description="DHCP de bancada (um cliente)")
    ap.add_argument("--server", default="192.168.0.10",
                    help="IP fixo deste PC na interface do cabo")
    ap.add_argument("--offer", default="192.168.0.50",
                    help="IP a entregar ao Pi")
    ap.add_argument("--mask", default="255.255.255.0")
    ap.add_argument("--lease", type=int, default=3600, help="lease em segundos")
    args = ap.parse_args()

    rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    rx.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    rx.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    try:
        rx.bind(("0.0.0.0", 67))
    except OSError as exc:
        print(f"ERRO: não consegui abrir a porta 67: {exc}", file=sys.stderr)
        print("Há outro DHCP ativo? (Internet Connection Sharing conta.)",
              file=sys.stderr)
        return 1

    tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    tx.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    tx.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    tx.bind((args.server, 0))

    print(f"DHCP de bancada em {args.server}:67 — oferecendo {args.offer}")
    print("aguardando o Pi... (Ctrl+C para parar)", flush=True)

    rx.settimeout(2.0)
    try:
        while True:
            try:
                data, _ = rx.recvfrom(2048)
            except socket.timeout:
                continue
            if len(data) < 240 or data[236:240] != MAGIC:
                continue
            tipo_raw = _le_opcao(data, 53)
            tipo = tipo_raw[0] if tipo_raw else 0
            mac = ":".join(f"{b:02x}" for b in data[28:34])
            host_raw = _le_opcao(data, 12)
            host = host_raw.decode("utf-8", "replace") if host_raw else "(sem)"
            agora = time.strftime("%H:%M:%S")
            print(f"{agora}  {_NOMES.get(tipo, tipo)} de {mac}  host={host}",
                  flush=True)

            if tipo == DISCOVER:
                tx.sendto(_resposta(data, OFFER, args.server, args.offer,
                                    args.mask, args.lease),
                          ("255.255.255.255", 68))
                print(f"{agora}    -> OFFER {args.offer}", flush=True)
            elif tipo == REQUEST:
                tx.sendto(_resposta(data, ACK, args.server, args.offer,
                                    args.mask, args.lease),
                          ("255.255.255.255", 68))
                print(f"{agora}    -> ACK {args.offer}   ***  ssh pi@{args.offer}"
                      f"  ***", flush=True)
                print(f"{agora}    MAC para a reserva de DHCP: {mac}", flush=True)
    except KeyboardInterrupt:
        print("\nencerrado.")
    finally:
        rx.close()
        tx.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
