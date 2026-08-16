# LidarMapper - refaz o baseline ("espaco vazio") de um painel ou de todos.
# ASCII puro (ver install_server.ps1 para o motivo).
#
# A AREA DA(S) TELA(S) PRECISA ESTAR LIVRE por ~15 s apos o comando -
# o que estiver parado na frente vira "fundo" e o toque morre ali.
#
# Uso (PowerShell, qualquer pasta):
#   powershell -ExecutionPolicy Bypass -File deploy\baseline.ps1 2      # so o painel 2
#   powershell -ExecutionPolicy Bypass -File deploy\baseline.ps1 all    # os 8

param([Parameter(Mandatory=$true)][string]$Painel)

# painel fisico -> hostname do cartao (tabela canonica: docs/MANUAL_DE_CAMPO.md secao 2)
$Mapa = @{
    "1" = "lidar-01"; "2" = "lidar-03"; "3" = "lidar-08"; "4" = "lidar-06"
    "5" = "lidar-07"; "6" = "lidar-02"; "7" = "lidar-04"; "8" = "lidar-05"
}

$alvos = if ($Painel -eq "all") { 1..8 | ForEach-Object { "$_" } } else { @($Painel) }

foreach ($p in $alvos) {
    $hostn = $Mapa[$p]
    if (-not $hostn) { Write-Host "painel invalido: $p (use 1-8 ou all)" -ForegroundColor Red; continue }
    Write-Host "painel $p ($hostn): refazendo baseline..." -NoNewline
    $r = ssh -o ConnectTimeout=8 "pi@$hostn.local" "sudo systemctl restart lidarmapper && echo REINICIADO" 2>$null
    if ($r -match "REINICIADO") { Write-Host " OK" -ForegroundColor Green }
    else { Write-Host " FALHOU (no offline? chave ssh?)" -ForegroundColor Red }
}
Write-Host ""
Write-Host "aguarde ~15 s com a(s) area(s) livre(s); depois confira os cartoes no fleet_bridge."
