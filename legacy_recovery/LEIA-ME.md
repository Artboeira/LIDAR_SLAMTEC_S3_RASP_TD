# Kit de recuperação do LidarMapper single-node

O repositório fonte original se perdeu; só restou o build PyInstaller
(LidarMapper.exe, Python 3.13). Este kit contém tudo que foi extraído
dele para reconstruir os .py na Sessão W0 do Claude Code.

## Conteúdo

- `pyc/` — bytecode compilado dos 15 módulos do projeto (extraídos do
  PYZ com pyinstxtractor + Python 3.13.13). É a VERDADE ABSOLUTA sobre o
  comportamento do código.
- `dis/` — disassembly completo de cada módulo (pycdas/Decompyle++):
  estrutura, nomes, constantes, docstrings e bytecode por função.
  É a fonte primária da reconstrução.
- `partial_decompile/` — tentativa de descompilação direta (pycdc).
  Incompleta (opcodes 3.13 não suportados), mas os docstrings de módulo
  e imports estão íntegros — úteis como esqueleto.
- `PLAN.md`, `UI.md`, `TOUCHDESIGNER.md`, `README_DIST.txt` — a
  documentação completa do projeto, escrita junto com o código.
- `config.yaml` — config real com comentários (revela defaults e schema).
- `calibration.json` — exemplo real de calibração salva (revela o schema
  exato de persistência do homography.py).

## Regra da reconstrução

Fidelidade > estilo. O objetivo é recuperar o comportamento validado em
produção, não melhorar o código. Toda divergência intencional deve ser
anotada. Validação: os testes reconstruídos devem passar e o formato de
todo artefato persistido/transmitido (calibration.json, datagrama V1)
deve bater byte a byte com os exemplos deste kit.
