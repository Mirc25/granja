# Ejecuta bots en modo sencillo (sin Redis) usando urls.txt
# Edita urls.txt para poner tus páginas (una por línea)

Param(
  [int]$Bots = 2,
  [int]$MaxPaginasPorProxy = 3,
  [int]$MinDwellMs = 5000,
  [int]$MaxDwellMs = 15000,
  [switch]$MostrarNavegador
)

Set-Location $PSScriptRoot
.\venv\Scripts\Activate.ps1

$headlessArg = ""
if ($MostrarNavegador) { $headlessArg = "--no-headless" }

python -m src.cli -f urls.txt --bots $Bots --max $MaxPaginasPorProxy --min-dwell-ms $MinDwellMs --max-dwell-ms $MaxDwellMs $headlessArg --respect-robots