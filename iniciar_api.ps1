# Inicia la API de la granja de bots (QA)
# Uso: doble clic o ejecutar desde PowerShell

Set-Location $PSScriptRoot
.\venv\Scripts\Activate.ps1
uvicorn src.orchestrator:app --reload --port 8000