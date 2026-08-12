#!/usr/bin/env python3
"""Gera o config por nó (`/home/pi/node-config.yaml`) a partir do
`node/config.yaml` versionado, aplicando `udp.panel_id` e `udp.host`.

POR QUE ESTE ARQUIVO EXISTE
---------------------------
`node/config.yaml` está sob controle de versão e cada nó precisa de valores
distintos nele. Editar o arquivo versionado em cada Pi faz o `git pull` de
atualização da frota conflitar nos 8 nós. A saída daqui vive FORA da árvore
git, e a unit do systemd a passa por `--config`.

Roda NO PI, com o Python do venv do nó (é lá que o `ruamel.yaml` está — ver o
comentário em `node/requirements-pi.txt`: a dep existe justamente para as
ferramentas de bancada). `ruamel` preserva comentários e ordem, então o arquivo
gerado continua legível e explicando os próprios campos.

Uso:
    render_node_config.py --panel 3 --udp-host 10.10.0.10 \
        --in node/config.yaml --out /home/pi/node-config.yaml

    render_node_config.py --panel 3 --check --out /home/pi/node-config.yaml
        # não escreve: só confere se o arquivo existente bate com o esperado
        # (exit 0 = bate, 3 = diverge, 4 = não existe)

    render_node_config.py --panel 3 --udp-host 192.168.0.42 --update \
        --out /home/pi/node-config.yaml
        # edita o arquivo EXISTENTE: só troca udp.panel_id/udp.host, preserva
        # ROI/mirror/offset ajustados à mão. Para migrar a frota de servidor
        # sem perder a configuração de montagem.

Sem `--udp-host`, o destino é derivado do painel: 1–4 → 10.10.0.10 (server-a),
5–8 → 10.10.0.11 (server-b), conforme o §3 de docs/INSTALACAO.md.
"""

import argparse
import os
import sys

EXIT_DIVERGE = 3
EXIT_MISSING = 4

# §3 de docs/INSTALACAO.md. Painéis 1-4 no server-a, 5-8 no server-b.
SERVER_A = "10.10.0.10"
SERVER_B = "10.10.0.11"


def default_udp_host(panel_id: int) -> str:
    return SERVER_A if panel_id <= 4 else SERVER_B


def _yaml():
    try:
        from ruamel.yaml import YAML
    except ImportError:
        sys.exit("ERRO: ruamel.yaml não encontrado. Rode com o Python do venv "
                 "do nó (~/lidarmapper/.venv/bin/python).")
    y = YAML()
    y.preserve_quotes = True
    # O config do nó é raso; 2 espaços mantém o arquivo igual ao versionado.
    y.indent(mapping=2, sequence=4, offset=2)
    return y


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--panel", type=int, required=True, help="panel_id em 1..8")
    ap.add_argument("--udp-host", default=None,
                    help="destino V2; default derivado do painel")
    ap.add_argument("--in", dest="src", default=None,
                    help="config base (default: node/config.yaml ao lado do repo)")
    ap.add_argument("--out", required=True, help="arquivo a gerar")
    ap.add_argument("--check", action="store_true",
                    help="não escreve; só valida o --out existente")
    ap.add_argument("--update", action="store_true",
                    help="edita o --out existente em vez de gerar do template "
                         "(preserva ROI/mirror/offset ajustados à mão)")
    args = ap.parse_args()

    if not (1 <= args.panel <= 8):
        sys.exit(f"ERRO: --panel deve estar em 1..8 (veio {args.panel}).")

    udp_host = args.udp_host or default_udp_host(args.panel)
    yaml = _yaml()

    # --- modo checagem: o arquivo do nó já existe, só confirmar os 2 campos ---
    if args.check:
        if not os.path.exists(args.out):
            print(f"AUSENTE  {args.out}")
            return EXIT_MISSING
        with open(args.out, encoding="utf-8") as f:
            cur = yaml.load(f)
        got_panel = cur.get("udp", {}).get("panel_id")
        got_host = cur.get("udp", {}).get("host")
        ok = (got_panel == args.panel and got_host == udp_host)
        print(f"{'OK      ' if ok else 'DIVERGE '}{args.out}: "
              f"panel_id={got_panel} (esperado {args.panel}), "
              f"host={got_host} (esperado {udp_host})")
        return 0 if ok else EXIT_DIVERGE

    # --- modo update: parte do arquivo do nó, não do template ---
    if args.update:
        if not os.path.exists(args.out):
            sys.exit(f"ERRO: --update exige que {args.out} exista "
                     "(sem ele, gere do template, sem --update).")
        src = args.out
    else:
        # --- modo geração: parte do template versionado ---
        src = args.src
        if src is None:
            here = os.path.dirname(os.path.abspath(__file__))
            src = os.path.join(os.path.dirname(here), "node", "config.yaml")
    if not os.path.exists(src):
        sys.exit(f"ERRO: config base não encontrado: {src}")

    with open(src, encoding="utf-8") as f:
        cfg = yaml.load(f)

    cfg["udp"]["panel_id"] = args.panel
    cfg["udp"]["host"] = udp_host

    # Escreve num temporário e move: se algo falhar no meio, o config em uso
    # do nó continua intacto (o serviço pode estar rodando com ele).
    tmp = args.out + ".new"
    with open(tmp, "w", encoding="utf-8") as f:
        if not args.update:
            # No --update o cabeçalho já está no arquivo (ruamel preserva os
            # comentários do topo) — só o modo geração o acrescenta.
            f.write("# GERADO por deploy/render_node_config.py — NÃO está no git.\n"
                    "# Editável à mão para ROI / angle_offset_deg / mirror (§7 de\n"
                    "# docs/INSTALACAO_PI.md). O provisionamento não sobrescreve\n"
                    "# este arquivo sem --rewrite-config.\n")
        yaml.dump(cfg, f)
    os.replace(tmp, args.out)

    print(f"OK       {args.out}: panel_id={args.panel} host={udp_host}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
