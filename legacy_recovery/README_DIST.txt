LidarMapper — distribuição standalone
=====================================

Rodar: dê dois cliques em LidarMapper.exe (abre o Control Panel).

Modos CLI (terminal):
  LidarMapper.exe                  -> UI (default)
  LidarMapper.exe main             -> pipeline UDP em background
  LidarMapper.exe calibrate        -> calibração interativa
  LidarMapper.exe test_viz         -> viz dos pontos do sensor
  LidarMapper.exe test_tracker     -> tracking ao vivo
  LidarMapper.exe test_calib       -> overlay de validação
  LidarMapper.exe test_udp_receiver -> SUB de debug
  LidarMapper.exe --help           -> lista de modos

Arquivos editáveis (ao lado do exe):
  config.yaml         -> sensor, processing, ROI, tracker, screen, UDP
  calibration.json    -> gerado pela calibração

Documentação:
  UI.md               -> Control Panel
  TOUCHDESIGNER.md    -> setup do UDP In DAT no TD
