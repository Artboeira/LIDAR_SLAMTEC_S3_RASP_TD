# LidarMapper — instalador do servidor Windows (evento CURVA).
#
# Faz em um comando o que o §1-2 e §7 de docs/OPERACAO_EVENTO_WINDOWS.md
# descrevem à mão:
#   1. confere Python 3.11+
#   2. cria .venv e instala server/requirements-server.txt
#   3. roda w2_validate.py (12/12 = servidor ok)
#   4. libera UDP 5555 e 7000 no firewall (se admin; senão imprime o comando)
#   5. gera start_fleet.bat e (opcional) coloca no auto-start do Windows
#   6. (opcional) gera chave SSH e instala nos 8 nós (pede a senha pi123 1x por nó)
#
# Uso (PowerShell, na raiz do repo — C:\lidarmapper):
#   powershell -ExecutionPolicy Bypass -File deploy\install_server.ps1
#
# Reexecutável: pula o que já está feito.

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot
Write-Host "== LidarMapper: instalando servidor em $RepoRoot ==" -ForegroundColor Cyan

# --- 1. Python ---------------------------------------------------------------
$py = $null
foreach ($cand in @("py -3", "python")) {
    try {
        $v = Invoke-Expression "$cand -c `"import sys;print('%d.%d'%sys.version_info[:2])`"" 2>$null
        if ($v -and [version]$v -ge [version]"3.11") { $py = $cand; break }
    } catch {}
}
if (-not $py) {
    Write-Host "FALHA: Python 3.11+ nao encontrado. Instale de python.org (marque 'Add to PATH') e rode de novo." -ForegroundColor Red
    exit 1
}
Write-Host "  OK  Python $v ($py)"

# --- 2. venv + dependencias ---------------------------------------------------
if (-not (Test-Path ".venv\Scripts\python.exe")) {
    Write-Host "  ... criando .venv"
    Invoke-Expression "$py -m venv .venv"
}
Write-Host "  ... instalando dependencias (pode levar 1-2 min)"
& .venv\Scripts\python.exe -m pip install --quiet --upgrade pip
& .venv\Scripts\pip.exe install --quiet -r server\requirements-server.txt
Write-Host "  OK  venv + requirements"

# --- 3. validacao -------------------------------------------------------------
Write-Host "  ... rodando w2_validate.py"
& .venv\Scripts\python.exe w2_validate.py | Select-Object -Last 1
if ($LASTEXITCODE -ne 0) {
    Write-Host "FALHA: w2_validate nao passou — veja a saida acima." -ForegroundColor Red
    exit 1
}
Write-Host "  OK  servidor validado" -ForegroundColor Green

# --- 4. firewall (UDP 5555 = V2 dos Pis; UDP 7000 = OSC pro TD) ---------------
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
           ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
foreach ($rule in @(@{n="LidarMapper V2 5555"; p=5555}, @{n="LidarMapper OSC 7000"; p=7000})) {
    if (Get-NetFirewallRule -DisplayName $rule.n -ErrorAction SilentlyContinue) {
        Write-Host "  SKIP firewall: '$($rule.n)' ja existe"
    } elseif ($isAdmin) {
        New-NetFirewallRule -DisplayName $rule.n -Direction Inbound -Protocol UDP `
            -LocalPort $rule.p -Action Allow | Out-Null
        Write-Host "  OK  firewall: UDP $($rule.p) liberado"
    } else {
        Write-Host "  AVISO sem admin — rode depois em PowerShell (admin):" -ForegroundColor Yellow
        Write-Host "        New-NetFirewallRule -DisplayName '$($rule.n)' -Direction Inbound -Protocol UDP -LocalPort $($rule.p) -Action Allow"
    }
}

# --- 5. start_fleet.bat + auto-start ------------------------------------------
$bat = Join-Path $RepoRoot "start_fleet.bat"
@"
cd /d $RepoRoot
.venv\Scripts\python server\fleet_bridge.py --panels 1,2,3,4,5,6,7,8 --dest 127.0.0.1
"@ | Set-Content -Encoding ASCII $bat
Write-Host "  OK  start_fleet.bat gerado (TD local; edite --dest p/ mais IPs)"

$startup = [Environment]::GetFolderPath("Startup")
$resp = Read-Host "  Colocar o fleet_bridge no auto-start do Windows? (s/N)"
if ($resp -match "^[sS]") {
    Copy-Item $bat (Join-Path $startup "start_fleet.bat") -Force
    Write-Host "  OK  auto-start em $startup"
}

# --- 6. SSH para os nos --------------------------------------------------------
$key = "$env:USERPROFILE\.ssh\id_ed25519"
if (-not (Test-Path "$key.pub")) {
    $resp = Read-Host "  Gerar chave SSH para operar os nos? (S/n)"
    if ($resp -notmatch "^[nN]") {
        New-Item -ItemType Directory -Force "$env:USERPROFILE\.ssh" | Out-Null
        ssh-keygen -t ed25519 -N '""' -f $key
    }
}
if (Test-Path "$key.pub") {
    $resp = Read-Host "  Instalar a chave nos 8 nos agora? (senha pi123, 1x por no) (s/N)"
    if ($resp -match "^[sS]") {
        $pub = Get-Content "$key.pub"
        foreach ($n in 1..8) {
            $hostn = "lidar-0$n.local"
            Write-Host "  ... $hostn (senha: pi123)"
            $ok = echo $pub | ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=8 "pi@$hostn" "mkdir -p ~/.ssh && cat >> ~/.ssh/authorized_keys && echo OK"
            if ($ok -match "OK") { Write-Host "  OK  $hostn" -ForegroundColor Green }
            else { Write-Host "  AVISO $hostn falhou/offline — repita depois" -ForegroundColor Yellow }
        }
    }
}

Write-Host ""
Write-Host "== pronto ==" -ForegroundColor Green
Write-Host "  rodar agora:     start_fleet.bat"
Write-Host "  guia de operacao: docs\OPERACAO_EVENTO_WINDOWS.md"
Write-Host "  IMPORTANTE: re-apontar os nos para o IP deste servidor (guia, secao 3)"
