# Kickoff Claude Code — LidarMapper Distribuído v3

> **HISTÓRICO.** Prompts das sessões de construção W0/W1/W2 (concluídas em
> 08/2026). O `CLAUDE.md` embutido abaixo é a versão da época e diverge do
> real. Nada aqui é operacional — para o sistema instalado, ver
> [../MANUAL_DE_CAMPO.md](../MANUAL_DE_CAMPO.md).

Quatro blocos: (0) preparação manual, (1) o `CLAUDE.md` pronto pra colar,
(2) o prompt da **Sessão W0 — reconstrução do legado** (nova, obrigatória,
primeira), (3) o prompt da Sessão W2, (4) o prompt da Sessão W1.

---

## 0. Preparação (você, antes de abrir o Claude Code)

> **Situação do fonte original:** o repositório dos `.py` se perdeu — só
> restou o build PyInstaller (o .exe). Os fontes foram extraídos do
> binário e estão no **`legacy_recovery.zip`** (bytecode `.pyc` +
> disassembly completo + descompilação parcial + toda a documentação).
> Por isso existe a **Sessão W0**: reconstruir os `.py` antes de tudo.

```bash
mkdir lidarmapper && cd lidarmapper
git init
mkdir shared node server legacy
# descompacte legacy_recovery.zip -> pasta legacy_recovery/ na raiz
# copie GUIA_LIDARMAPPER_DISTRIBUIDO.md pra raiz
# cole o CLAUDE.md (bloco 1 abaixo) na raiz
git add -A && git commit -m "scaffold + spec + kit de recuperação do legado"
```

A pasta `legacy/` começa vazia — é a Sessão W0 que a preenche com os
`.py` reconstruídos a partir do `legacy_recovery/`. Depois de validada,
`legacy/` vira read-only pras sessões seguintes, como planejado.

---

## 1. CLAUDE.md (cole na raiz do repo, tal qual)

```markdown
# LidarMapper Distribuído v3

Instalação interativa: 8 painéis LED, 8 nós Raspberry Pi (3B+/4/5) com
RPLIDAR S3, 2 servidores Windows com TouchDesigner + relay Python.

**Spec / fonte de verdade: `GUIA_LIDARMAPPER_DISTRIBUIDO.md`.** Leia
antes de qualquer implementação. Em conflito entre guia e código, o guia
vence. O §3 (protocolos) não muda sem atualizar o guia primeiro.

## Estrutura
- `shared/protocol.py` — pack/unpack dos protocolos V1 e V2. ÚNICO lugar
  do repo onde format strings de struct podem existir.
- `node/` — W1, roda nos Raspberry Pi (headless). PROIBIDO importar
  pygame, tkinter, customtkinter. Deps: rplidar-roboticia, numpy, pyyaml,
  ruamel.yaml, stdlib.
- `server/` — W2, roda no Windows dos servidores. Pode usar pygame,
  customtkinter, python-osc.
- `legacy/` — fonte do LidarMapper single-node, RECONSTRUÍDO do build
  PyInstaller na Sessão W0 (o repo original se perdeu). Após o W0:
  READ-ONLY — copiar de lá, nunca editar lá.
- `legacy_recovery/` — kit extraído do .exe (bytecode .pyc, disassembly
  pycdas, descompilação parcial, docs). Fonte de verdade da Sessão W0;
  intocável depois.

## Regras
1. Cada sessão trabalha em UM workstream (node/ ou server/) e não edita
   a pasta do outro.
2. Todo teste que envolva bytes de rede importa `shared/protocol.py`.
3. Worst case de performance do node/: Raspberry Pi 3B+ (1 core fraco).
   Parsing serial vetorizado com numpy é requisito (§5.0 do guia).
4. Preferir copiar+adaptar de `legacy/` a reescrever.
5. Comentários e docstrings em pt-BR, nomes de código em inglês.
```

---

## 2. Prompt — Sessão W0 (reconstrução do legado) — PRIMEIRA, OBRIGATÓRIA

```text
Leia CLAUDE.md, GUIA_LIDARMAPPER_DISTRIBUIDO.md e
legacy_recovery/LEIA-ME.md antes de qualquer código. Esta sessão
reconstrói os 15 módulos .py do LidarMapper single-node na pasta
legacy/, a partir do kit em legacy_recovery/. Não toque em shared/,
node/ ou server/.

Material por módulo em legacy_recovery/:
- dis/<mod>.pycdas.txt — disassembly completo (fonte primária: estrutura,
  nomes, constantes, docstrings e bytecode de cada função);
- partial_decompile/<mod>.py — esqueleto com docstring de módulo e
  imports (a descompilação automática falhou nos opcodes do 3.13);
- pyc/<mod>.pyc — bytecode original (verdade absoluta; use
  `dis.dis` em Python 3.13 se o pycdas deixar dúvida);
- PLAN.md, UI.md, TOUCHDESIGNER.md, config.yaml, calibration.json —
  documentação e artefatos reais que revelam schemas e comportamento.

Método, módulo a módulo (um commit por módulo):
1. Ordem de dependência: config → homography → processing → tracker →
   publisher → lidar_reader → main → calibrate → test_udp_receiver →
   test_e2e → test_lidar → test_viz → test_tracker → test_calib → ui.
2. Para cada módulo: ler o disassembly inteiro, reconstruir o .py
   preservando nomes, assinaturas, defaults, constantes, docstrings e
   lógica. Fidelidade > estilo — NÃO refatorar, NÃO melhorar, NÃO
   renomear. Divergência inevitável = comentário `# RECONSTRUÇÃO:`.
3. Verificação por módulo: compilar com py_compile (Python 3.13 se
   disponível; senão 3.12+ e anotar), importar sem erro, e conferir
   contra o disassembly: mesmas funções/classes/constantes/defaults.

Validação global ao final:
- test_e2e.py reconstruído roda e passa sem hardware;
- pack_frame do publisher.py gera datagrama V1 byte-idêntico ao formato
  do TOUCHDESIGNER.md (montar um teste com valores fixos);
- save/load do homography.py lê o calibration.json real do kit e
  round-tripa sem perda; compute_homography sobre os corners do JSON
  reproduz a matriz H salva (tolerância float);
- config.py carrega o config.yaml real do kit com todos os campos;
- gerar legacy/RECONSTRUCAO.md: status por módulo, divergências
  anotadas, e o que só é verificável com o sensor físico (ex.: timing
  real do lidar_reader — vai pro checklist de bancada).

Os módulos de viz/UI (test_viz, test_tracker, test_calib, ui) podem ter
fidelidade "best effort" — são ferramentas de bancada, não entram em
produção. O núcleo (config, homography, processing, tracker, publisher,
lidar_reader, main, calibrate) exige fidelidade máxima.

Pergunte antes de decidir qualquer coisa ambígua no disassembly.
```

---

## 3. Prompt — Sessão W2 (middleware de servidor) — APÓS A W0

```text
Leia CLAUDE.md e GUIA_LIDARMAPPER_DISTRIBUIDO.md por completo antes de
escrever qualquer código. Esta sessão implementa APENAS o W2 (§5.2 do
guia) — as pastas shared/ e server/. Não toque em node/.

Contexto: legacy/ contém o LidarMapper single-node validado em produção
(leia PLAN.md e TOUCHDESIGNER.md de lá). Vamos evoluí-lo para o papel de
middleware de servidor conforme o guia.

Ordem de implementação (commits separados por etapa):

1. shared/protocol.py — dataclasses + pack/unpack de V2 (§3) e V1 (§3.1),
   portando o pack/unpack do legacy/publisher.py como base do V1.
   Testes unitários de round-trip (pack→unpack) para os dois formatos,
   incluindo casos de borda: 0 pontos, max_points, version errada,
   tamanho inconsistente.

2. server/test_node_sim.py — simulador de nós: gera tráfego V2 sintético
   para panel_ids configuráveis (toques fake em círculo, toque parado,
   down/up com ids persistentes), 30 Hz. É a ferramenta de dev de tudo
   que segue — sem Pi ligado.

3. server/server_relay.py — conforme §5.2 item 7 e §3.2: escuta V2 na
   :5555, demux por panel_id, aplica H de calib_pN.json (copiar
   homography.py de legacy/ para server/), descarta fora de 0..1,
   detecta down/up por diff de ids, envia OSC /touch/N (python-osc) ao
   Max, reempacota V1 e envia a 127.0.0.1:600N. Hot-reload dos JSONs por
   mtime. Sem calibração de um painel → não repassa e loga uma vez.
   config_server.yaml conforme §5.2 item 9.

4. server/test_udp_receiver.py — evoluir o do legacy com modos --v1 e
   --v2 (§5.2 item 11).

5. server/calibrate.py — multi-painel, duas fontes de alvo (§7):
   coletor único (UDP V2 filtrado por --panel N, mediana ~2s por canto,
   DLT/SVD do homography.py, salva calib_pN.json, reporta erro de
   reprojeção) + fonte de alvo plugável: --target-source local (pygame
   fullscreen, portar a UX do legacy/calibrate.py: mesmos alvos, insets
   ~0.06/0.94, ordem TL→TR→BR→BL) e --target-source td (stub que envia
   comandos OSC /calib/target ao TD; a contraparte no TD fica fora desta
   sessão).

6. Validação final: roteiro em server/VALIDACAO.md descrevendo o teste
   ponta a ponta com o simulador: test_node_sim (4 painéis) → calibrate
   --panel 1 --target-source local em janela → relay → test_udp_receiver
   --v1 mostrando 0..1 coerentes + OSC /touch/N observável.

Restrições: Windows é o alvo do server/ (paths, sockets, sem systemd).
Python 3.11+. Não gerar build PyInstaller nesta sessão.

Pergunte antes de decidir qualquer coisa que o guia deixe em aberto.
```

---

## 4. Prompt — Sessão W1 (nó Pi) — APÓS A W2

```text
Leia CLAUDE.md e GUIA_LIDARMAPPER_DISTRIBUIDO.md por completo antes de
escrever qualquer código. Esta sessão implementa APENAS o W1 (§5.0 e
§5.1 do guia) — a pasta node/. Não toque em server/ nem em
shared/protocol.py (já implementado e testado pela sessão W2 — importe).

Contexto: legacy/ contém o single-node validado. node/ é a versão
enxuta para Raspberry Pi headless (worst case: 3B+), que publica
cursores em mm via V2 usando shared/protocol.py.

Ordem de implementação (commits separados por etapa):

1. Copiar de legacy/ para node/: lidar_reader.py, processing.py,
   tracker.py, config.py, config.yaml, test_e2e.py, test_lidar.py.
   Remover imports/campos de viz, screen e calibração. Adicionar
   udp.panel_id (1..8, obrigatório, validado, sem default) ao config.

2. node/publisher.py — reescrever sobre shared/protocol.py: pack V2 com
   version=2 e panel_id do config. Manter RateLimiter do legacy.

3. node/main.py — pipeline reader→processing→tracker→publisher, SEM
   homografia e SEM calibration.json. Logs de meas/s, fg=N, pub/s.

4. §5.0 — parsing serial vetorizado (a etapa mais importante):
   leitura em blocos grandes, numpy.frombuffer + máscaras vetorizadas
   para quality/ângulo/distância, ROI em mm como máscara numpy antes de
   criar objetos Python. Se rplidar-roboticia não expor o stream cru de
   forma utilizável, implementar o protocolo dense/express scan do S3
   diretamente. Meta: parsing+filtros ≤ 30% de um core no 3B+.
   Criar node/bench_parse.py que mede amostras/s e %CPU do parsing com
   dados sintéticos (roda em qualquer máquina) para o gate do §10.

5. test_e2e.py — pipeline com medidas sintéticas, asserts no formato V2
   via shared/protocol.py. Deve passar sem hardware (é o smoke do ARM).

6. Validação final: roteiro node/VALIDACAO.md — test_e2e, depois com o
   S3 na USB: test_lidar (meas/s nominal), main.py com
   server/test_udp_receiver.py --v2 recebendo do outro lado.

Restrições: alvo é Linux arm64 headless (mas deve rodar no dev x86
também). PROIBIDO pygame/tkinter/customtkinter em node/. Deps mínimas
(§5.1 item 4). Nenhum format string de struct fora de shared/.

Pergunte antes de decidir qualquer coisa que o guia deixe em aberto.
```
