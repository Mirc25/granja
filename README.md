# Granja de Bots de QA (Legítima) — Guía en español con ejemplos

Este proyecto implementa una orquestación de "bots" (workers) para pruebas técnicas legítimas: navegación controlada, captura de métricas y capturas de pantalla, con rotación de proxies para pruebas geográficas. No está diseñado para manipular métricas de terceros (p. ej., vistas falsas) ni para realizar acciones en plataformas sin permiso.

## Características
- API de orquestación con FastAPI para encolar tareas.
- Distribución de trabajo con RQ + Redis.
- Workers con Playwright (Chromium) que visitan enlaces, simulan lectura/scroll y opcionalmente toman capturas.
- Rotación de proxies simple por número de páginas.
- User-Agent configurable, modo `headless` configurable.
- Opción de respetar `robots.txt` si se desea (conservadora).

## Requisitos
- Python 3.9+
- Redis (se puede usar Docker Desktop en Windows)

## Instalación

```powershell
# Crear y activar entorno virtual (PowerShell)
python -m venv venv
./venv/Scripts/Activate.ps1

# Instalar dependencias
pip install -r requirements.txt

# Instalar navegadores de Playwright
python -m playwright install

# Arrancar Redis con Docker (opcional)
docker run -p 6379:6379 --name redis -d redis:7-alpine
```

## Configuración (con ejemplos)
- Edita `proxies.txt` para añadir proxies en formato:
  - `http://host:port`
  - `http://usuario:password@host:port`
- Variables de entorno disponibles:
  - `RQ_REDIS_URL` (por defecto `redis://localhost:6379/0`)
  - `QUEUE_NAME` (por defecto `bot_queue`)
  - `BOT_USER_AGENT` (por defecto `TestBot/1.0 (+contact@example.com)`)
  - `ARTIFACTS_DIR` (por defecto `artifacts`)
  - `PROXIES_FILE` (por defecto `proxies.txt`)

Ejemplo PowerShell:
```
$env:RQ_REDIS_URL = "redis://localhost:6379/0"
$env:QUEUE_NAME = "bot_queue"
$env:BOT_USER_AGENT = "TestBot/1.0 (+contacto@tu-sitio.com)"
```

## Ejecución

```powershell
# Iniciar API
uvicorn src.orchestrator:app --reload --port 8000

# Configurar Redis URL para los workers
$env:RQ_REDIS_URL = "redis://localhost:6379/0"

# Arrancar un worker
rq worker bot_queue
```

### Modo fácil (sin Redis) — Ejemplos

Si no tienes Redis o quieres probar rápido, usa el script CLI local:

```powershell
# Ejecutar con URLs en línea (headless por defecto)
python -m src.cli -u https://example.com https://example.com/docs --respect-robots --bots 3 --min-dwell-ms 5000 --max-dwell-ms 15000

# O con archivo de URLs
python -m src.cli -f urls.txt --max 3 --bots 2

# Mostrar la ventana del navegador (no headless)
python -m src.cli -f urls.txt --no-headless
```

Esto visitará las páginas, hará scroll y guardará capturas y un `results_*.json` dentro de `artifacts/`.
Parámetros útiles con ejemplos:
- `--bots 2` (dos sesiones paralelas)
- `--max 3` (rotar proxy cada 3 páginas)
- `--min-dwell-ms 5000 --max-dwell-ms 15000` (permanencia ~5–15s)
- `--no-headless` (mostrar ventana)
- `--user-agent "TestBot/1.0 (+contacto@ejemplo.com)"`

## Uso de la API — Campos con ejemplos

- Encolar una tarea de navegación con rotación de proxy:

```bash
curl -X POST -H "Content-Type: application/json" \
  -d '{
    "urls":["https://tusitio.com","https://tusitio.com/pagina"],
    "max_pages_per_proxy":3,
    "screenshot":true,
    "headless":true
  }' \
  http://localhost:8000/enqueue_browser
```

- Ver estado del job: `GET /status/{job_id}`
- Ver resultado del job: `GET /result/{job_id}`
- Documentación interactiva: `GET /docs`

## Alcance y cumplimiento
- Úsalo para QA en tus propios sitios, staging o cuando tengas permiso explícito.
- Respeta TOS de cada servicio y evita scraping de datos personales.
- La rotación de proxy es para pruebas geográficas, no para evadir controles.

No está diseñado para automatizar acciones en plataformas como YouTube o Facebook sin permiso ni para manipular métricas.

## Estructura

```
GRANJA/
├─ requirements.txt
├─ README.md
├─ proxies.txt
└─ src/
   ├─ __init__.py
   ├─ config.py
   ├─ proxies.py
   ├─ robots_util.py
   ├─ browser_tasks.py
   └─ orchestrator.py
```