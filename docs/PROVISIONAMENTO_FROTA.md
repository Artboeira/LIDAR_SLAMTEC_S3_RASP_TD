# Provisionamento da frota — CONCLUÍDO (08/2026)

Este documento era o plano de trabalho para levar os 8 nós de "cartão SD
gravado" a "publicando V2 no servidor". **A frota está completa: 8/8
provisionados, orientados e calibrados** (madrugada de 12→13/08/2026), e o
conteúdo útil daqui migrou, como prometido:

- **Tabela canônica da frota** (painel ↔ hostname ↔ MAC ↔ hardware ↔
  observações): [MANUAL_DE_CAMPO.md §2](MANUAL_DE_CAMPO.md) — é a referência
  que `server/fleet_bridge.py` e `deploy/baseline.ps1` seguem em código.
- **Procedimento e ferramentas de provisionamento** (`bootstrap_keys.sh`,
  `provision_node.sh`, `verify_node.sh`, `push_repo.sh`,
  `render_node_config.py`) e os aprendizados de alimentação/hardware:
  [INSTALACAO_PI.md](INSTALACAO_PI.md).
- **Operação, runbooks e troubleshooting** do sistema em produção:
  [MANUAL_DE_CAMPO.md](MANUAL_DE_CAMPO.md).

O plano original completo (fases, topologia de bancada com o Mac
compartilhando internet, riscos da época) permanece no histórico do git deste
arquivo — `git log --follow docs/PROVISIONAMENTO_FROTA.md`.
