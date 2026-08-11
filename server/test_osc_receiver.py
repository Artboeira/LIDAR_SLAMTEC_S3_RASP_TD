"""
LidarMapper — receptor OSC de teste (faz o papel do Max/MSP).

O relay dispara `/touch/<panel_id>` a cada evento de *down* (§3.2 do guia). Sem
o Max no ar não há como saber se isso está acontecendo — este script escuta a
porta de OSC e imprime cada mensagem, com o intervalo desde a anterior.

Uso (a partir da raiz do repo, com o relay rodando SEM `--no-osc`):

    .venv\\Scripts\\python server\\test_osc_receiver.py
    .venv\\Scripts\\python server\\test_osc_receiver.py --duration 60
    .venv\\Scripts\\python server\\test_osc_receiver.py --port 7500

A porta default vem do `osc.port` do config_server.yaml.

O que esperar: **um** `/touch/N` por toque novo, não um fluxo contínuo. Se
chover mensagem enquanto a mão está parada no painel, o tracker está perdendo e
recriando o track — investigue `tracker.timeout_s` e a densidade de pontos, não
o relay.
"""
from __future__ import annotations

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server import config_server as cfg_mod  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="Receptor OSC de teste (stub do Max)")
    ap.add_argument("--config", default=None, help="path do config_server.yaml")
    ap.add_argument("--host", default=None, help="default: osc.host do config")
    ap.add_argument("--port", type=int, default=None,
                    help="default: osc.port do config")
    ap.add_argument("--duration", type=float, default=0.0,
                    help="segundos; 0 = até Ctrl+C")
    args = ap.parse_args()

    cfg = cfg_mod.load(args.config)
    host = args.host or cfg.osc.host
    port = args.port or cfg.osc.port

    from pythonosc import dispatcher as osc_dispatcher
    from pythonosc import osc_server

    estado = {"n": 0, "ultimo": None}
    t0 = time.monotonic()
    por_endereco: dict[str, int] = {}

    def ao_receber(addr: str, *osc_args) -> None:
        agora = time.monotonic()
        gap = "" if estado["ultimo"] is None else f"  (+{agora - estado['ultimo']:.2f}s)"
        estado["ultimo"] = agora
        estado["n"] += 1
        por_endereco[addr] = por_endereco.get(addr, 0) + 1
        extra = f"  args={osc_args}" if osc_args else ""
        print(f"[{agora - t0:7.1f}s]  {addr}{extra}{gap}   total={estado['n']}",
              flush=True)

    disp = osc_dispatcher.Dispatcher()
    disp.set_default_handler(ao_receber)

    try:
        srv = osc_server.ThreadingOSCUDPServer((host, port), disp)
    except OSError as exc:
        print(f"ERRO: não consegui escutar em {host}:{port}: {exc}",
              file=sys.stderr)
        print("O Max (ou outra instância deste script) já está na porta?",
              file=sys.stderr)
        return 1

    srv.timeout = 0.5
    limite = f"{args.duration:.0f}s" if args.duration else "até Ctrl+C"
    print(f"[osc] escutando {host}:{port} ({limite})")
    print("[osc] esperado: um /touch/<panel_id> por toque novo.\n", flush=True)

    try:
        while not args.duration or (time.monotonic() - t0) < args.duration:
            srv.handle_request()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()

    print(f"\n[osc] total: {estado['n']} mensagens em "
          f"{time.monotonic() - t0:.1f}s")
    for addr, n in sorted(por_endereco.items()):
        print(f"       {addr}: {n}")
    if estado["n"] == 0:
        print("[osc] nenhuma mensagem. O relay está sem --no-osc? Algum painel"
              " com calibração ([C] no status) e cursor entrando?")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
