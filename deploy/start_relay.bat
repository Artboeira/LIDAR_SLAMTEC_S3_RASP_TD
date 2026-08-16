@echo off
REM ---------------------------------------------------------------------------
REM LidarMapper - sobe o relay (modo alternativo V1 binario pro TD).
REM
REM O MODO NORMAL DO EVENTO E OUTRO: start_fleet.bat (fleet_bridge, saida
REM OSC) - gerado por deploy\install_server.ps1. Este .bat serve para o
REM modo relay+V1 da spec. Os dois ocupam a porta 5555: um por vez.
REM
REM Rode ANTES de abrir o TouchDesigner, e deixe esta janela aberta: o console
REM mostra o status de 1x/s (in/out/drop/down por painel).
REM
REM Fechar a janela (ou Ctrl+C) encerra o relay.
REM
REM Pre-requisitos: repo em C:\lidarmapper com .venv criado
REM   py -3.13 -m venv .venv
REM   .venv\Scripts\pip install -r server\requirements-server.txt
REM ---------------------------------------------------------------------------

REM Sobe um nivel a partir de deploy\ - o relay PRECISA rodar da raiz do repo
REM (server_relay.py faz `from server import config_server`).
cd /d "%~dp0.."

title LidarMapper relay

if not exist ".venv\Scripts\python.exe" (
    echo [ERRO] .venv nao encontrado em %CD%\.venv
    echo Crie com:  py -3.13 -m venv .venv
    echo E instale: .venv\Scripts\pip install -r server\requirements-server.txt
    pause
    exit /b 1
)

".venv\Scripts\python.exe" server\server_relay.py %*

echo.
echo [relay encerrado com codigo %ERRORLEVEL%]
pause
