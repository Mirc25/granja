# Guía rápida en español

Esta es la forma más sencilla de usar la granja de bots (para QA legítimo en tus propios sitios o entornos de pruebas).

## Paso 1 — Preparar el entorno

```powershell
# Activar entorno virtual
./venv/Scripts/Activate.ps1

# Instalar navegadores de Playwright (una sola vez)
python -m playwright install
```

## Paso 2 — Ejecutar bots (modo fácil, sin Redis)

1) Edita `urls.txt` y escribe tus páginas (una por línea). Ejemplo:
```
https://tu-sitio.com
https://tu-sitio.com/pagina
```

2) Ejecuta el script y listo:
```powershell
# 2 bots en paralelo, rotando proxy cada 3 páginas, permanencia de 5–15 segundos
./ejecutar_bots.ps1 -Bots 2 -MaxPaginasPorProxy 3 -MinDwellMs 5000 -MaxDwellMs 15000

# Si quieres ver el navegador (no headless)
./ejecutar_bots.ps1 -Bots 1 -MaxPaginasPorProxy 2 -MostrarNavegador
```

Resultados:
- Capturas y archivos JSON de resultados dentro de `artifacts/`.
- El resumen indica cuántas páginas se visitaron correctamente por cada bot.

## Paso 3 — Usar la API (avanzado)

```powershell
# Iniciar la API
./iniciar_api.ps1
```

Abre `http://127.0.0.1:8000/docs`. Allí verás:
- `POST /enqueue_browser`: Encola una tarea de navegación con campos descritos en español.
- `GET /status/{job_id}`: Estado del job.
- `GET /result/{job_id}`: Resultado del job.

Si quieres que los jobs se procesen en segundo plano, instala Redis (Docker Desktop o Memurai) y en otra terminal:
```powershell
$env:RQ_REDIS_URL = "redis://localhost:6379/0"
rq worker bot_queue
```

## Interfaz web sencilla (/ui)

- Inicia la API con `./iniciar_api.ps1`.
- Abre `http://127.0.0.1:8000/ui` en el navegador.
- Pega varias URLs (una por línea), ajusta parámetros y pulsa "Ejecutar local".
- Se mostrará el resultado y podrás descargar el JSON.
- La UI está adaptada a claro/oscuro, con alto contraste de texto.

## Campos y opciones (explicación corta)
- `urls`: páginas de tu sitio o entorno de pruebas. Ejemplo API: `["https://example.com","https://example.com/docs"]`
- `bots` (CLI): cuántas sesiones paralelas. Ejemplo CLI: `--bots 3`
- `max_pages_per_proxy`: cuántas páginas antes de cambiar de proxy. Ejemplo: `--max 3`
- `min_dwell_ms` / `max_dwell_ms`: tiempo aproximado en cada página (ms). Ejemplo: `--min-dwell-ms 5000 --max-dwell-ms 15000`
- `respect_robots`: si está activo, evita URLs bloqueadas por `robots.txt`. Ejemplo: `--respect-robots`
- `--no-headless` (CLI): muestra el navegador. Ejemplo: `--no-headless`
- `user_agent`: define el agente de usuario. Ejemplo: `--user-agent "TestBot/1.0 (+contacto@ejemplo.com)"`

## Importante (cumplimiento)
- Úsalo para QA en sitios propios o con permiso explícito.
- No lo uses para manipular métricas ni automatizar acciones en plataformas de terceros (YouTube, Facebook, etc.).
- La rotación de proxy es para pruebas geográficas, no para evadir controles.