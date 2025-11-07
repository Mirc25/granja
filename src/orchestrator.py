from typing import Optional, List, Dict
import json

from fastapi import FastAPI, Form
import os
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
import uuid
from pydantic import BaseModel, AnyHttpUrl, Field, ConfigDict
from redis import Redis
from rq import Queue
from rq.job import Job
import urllib.parse
import threading
import time

from .config import redis_url, queue_name, artifacts_dir, proxies_file
from .browser_tasks import visit_links_with_rotation
from .vpn import get_vpn_controller

app = FastAPI(
    title="Granja de Bots (QA)",
    description=(
        "API para encolar tareas de navegaciÃ³n en sitios propios o de pruebas.\n\n"
        "Uso rÃ¡pido: envÃ­a una lista de URLs y opcionalmente define pÃ¡ginas por proxy,"
        " permanencia mÃ­nima/mÃ¡xima por pÃ¡gina y si tomar capturas.\n\n"
        "Ejemplo de cuerpo JSON para POST /enqueue_browser:\n"
        "{\n  \"urls\": [\"https://tu-sitio.com\", \"https://tu-sitio.com/pagina\"],\n"
        "  \"max_pages_per_proxy\": 3,\n  \"screenshot\": true,\n"
        "  \"respect_robots\": true,\n  \"min_dwell_ms\": 5000,\n  \"max_dwell_ms\": 15000\n}"
    ),
)

# Utilidad: lanzar contexto PERSISTENTE (no incógnito) usando carpeta chrome_profile
def _launch_persistent_ctx(p, args=None, viewport=None, window_size=None, window_pos=None):
    try:
        profile_dir = os.path.join(os.getcwd(), "chrome_profile")
        os.makedirs(profile_dir, exist_ok=True)
    except Exception:
        profile_dir = os.path.join(os.getcwd(), "chrome_profile")
    launch_args = list(args or [])
    if window_size:
        launch_args.insert(0, f"--window-size={window_size[0]},{window_size[1]}")
    if window_pos:
        launch_args.insert(0, f"--window-position={window_pos[0]},{window_pos[1]}")
    ctx = p.chromium.launch_persistent_context(
        user_data_dir=profile_dir,
        headless=False,
        args=launch_args,
        viewport=viewport,
    )
    return ctx

# Monta carpeta de artefactos (capturas) para servir archivos estÃ¡ticos
# Asegurar que el directorio exista antes de montar y escribir archivos
try:
    os.makedirs(artifacts_dir, exist_ok=True)
except Exception:
    pass
app.mount("/artifacts", StaticFiles(directory=artifacts_dir), name="artifacts")
app.mount(
    "/static",
    StaticFiles(directory=os.path.join(os.path.dirname(__file__), "static")),
    name="static",
)


@app.get("/ui_simple")
def ui_simple():
    html = """
    <!doctype html>
    <html lang=\"es\">
    <head>
      <meta charset=\"utf-8\" />
      <title>Granja de Bots â€” UI simple</title>
      <style>
        body { font-family: system-ui, Arial; background: #0f1115; color: #eee; margin: 0; }
        .wrap { max-width: 900px; margin: 24px auto; padding: 16px; }
        .card { background: #1a1f29; border: 1px solid #2a3140; border-radius: 8px; padding: 16px; }
        label { display: block; margin: 12px 0 6px; font-weight: 600; }
        textarea { width: 100%; min-height: 90px; background: #0f141b; color: #e5e7eb; border: 1px solid #334155; border-radius: 6px; padding: 8px; }
        .btn { margin-top: 16px; padding: 10px 14px; border: none; border-radius: 6px; background: #2563eb; color: white; cursor: pointer; font-weight: 600; }
        .btn:disabled { opacity: .6; cursor: default; }
        .hint { color: #94a3b8; font-size: 13px; }
        .result { margin-top: 16px; }
        a { color: #93c5fd; }
      </style>
    </head>
    <body>
      <div class=\"wrap\">
        <div class=\"card\">
          <h2>UI simple</h2>
          <p class=\"hint\">Introduce URL(s), Proxies y Acciones. Se abrirÃ¡ el navegador para que veas quÃ© hace.</p>
          <label for=\"urls\">URLs (una por lÃ­nea)</label>
          <textarea id=\"urls\" placeholder=\"https://ejemplo.com\nhttps://otra.com\"></textarea>

          <label for=\"proxies\">Proxies (uno por lÃ­nea, opcional)</label>
          <textarea id=\"proxies\" placeholder=\"http://usuario:clave@host:puerto\nhttp://host:puerto\"></textarea>

          <label for=\"actions\">Acciones (JSON, opcional)</label>
          <textarea id=\"actions\">[
  { "type": "wait_for", "selector": "video" },
  { "type": "js", "code": "(function(){ const texts = ['Aceptar','Acepto','Estoy de acuerdo','Continuar','Ok','Entendido','Permitir','Agree','Accept']; const btn = Array.from(document.querySelectorAll('button, a, [role=\"button\"]')).find(el => texts.some(x => (el.textContent||'').toLowerCase().includes(x.toLowerCase()))); if (btn) { try { btn.click(); } catch(e){} } })();" },
  { "type": "js", "code": "(function(){ const candidates = ['.vjs-big-play-button','.vjs-play-control','.jw-icon-play','.jw-icon-replay','button[aria-label=\"Play\"]','button[aria-label=\"Reproducir\"]','[data-testid=\"play-button\"]']; for (const sel of candidates) { const el = document.querySelector(sel); if (el) { try { el.click(); } catch(e){} break; } } const v = document.querySelector('video'); if (v) { try { v.scrollIntoView({behavior:'smooth', block:'center'}); } catch(e){} try { v.play && v.play(); } catch(e){} } })();" },
  { "type": "play_video" },
  { "type": "js", "code": "(function(){ const v = document.querySelector('video'); if (!v) return; try { v.muted = false; v.volume = 0.7; } catch(e){} })();" }
]</textarea>

          <button id=\"run-btn\" class=\"btn\">Ejecutar</button>

          <div id=\"result\" class=\"result\"></div>
        </div>
      </div>

      <script>
        const btn = document.getElementById('run-btn');
        btn.addEventListener('click', async (e) => {
          e.preventDefault();
          btn.disabled = true; btn.textContent = 'Ejecutando...';
          const fd = new FormData();
          fd.append('urls', document.getElementById('urls').value || '');
          fd.append('proxies', document.getElementById('proxies').value || '');
          fd.append('actions', document.getElementById('actions').value || '');
          try {
            const resp = await fetch('/run_simple_form', { method: 'POST', body: fd });
            const html = await resp.text();
            document.getElementById('result').innerHTML = html;
          } catch (err) {
            document.getElementById('result').innerHTML = '<p>Error: ' + (err && err.message ? err.message : err) + '</p>';
          } finally {
            btn.disabled = false; btn.textContent = 'Ejecutar';
          }
        });
      </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html)


@app.post("/run_simple_form")
async def run_simple_form(urls: str = Form(""), proxies: str = Form(""), actions: str = Form("")):
    import json
    # Parseo entradas
    urls_list = [u.strip() for u in urls.splitlines() if u.strip()]
    proxies_list = [p.strip() for p in proxies.splitlines() if p.strip()]

    # Si se pegaron proxies en la UI, los guardo en proxies.txt para que el rotador los use
    try:
        if proxies_list:
            with open(proxies_file, "w", encoding="utf-8") as f:
                f.write("\n".join(proxies_list) + "\n")
    except Exception:
        pass

    # Acciones por defecto para video si no se envÃ­a JSON
    default_actions = [
        {"type": "wait_for", "selector": "video"},
        {"type": "js", "code": "(function(){ const texts = ['Aceptar','Acepto','Estoy de acuerdo','Continuar','Ok','Entendido','Permitir','Agree','Accept']; const btn = Array.from(document.querySelectorAll('button, a, [role=\"button\"]')).find(el => texts.some(x => (el.textContent||'').toLowerCase().includes(x.toLowerCase()))); if (btn) { try { btn.click(); } catch(e){} } })();"},
        {"type": "js", "code": "(function(){ const candidates = ['.vjs-big-play-button','.vjs-play-control','.jw-icon-play','.jw-icon-replay','button[aria-label=\"Play\"]','button[aria-label=\"Reproducir\"]','[data-testid=\"play-button\"]']; for (const sel of candidates) { const el = document.querySelector(sel); if (el) { try { el.click(); } catch(e){} break; } } const v = document.querySelector('video'); if (v) { try { v.scrollIntoView({behavior:'smooth', block:'center'}); } catch(e){} try { v.play && v.play(); } catch(e){} } })();"},
        {"type": "play_video"},
        {"type": "js", "code": "(function(){ const v = document.querySelector('video'); if (!v) return; try { v.muted = false; v.volume = 0.7; } catch(e){} })();"}
    ]
    actions_list = default_actions
    if actions and actions.strip():
        try:
            actions_list = json.loads(actions)
        except Exception:
            actions_list = default_actions

    # Ejecutar con valores pensados para ver quÃ© hace en pantalla
    ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"
    # Ejecuta navegaciÃ³n usando Playwright sync API; en UI simple mostramos el navegador
    try:
        results = visit_links_with_rotation(
            urls=urls_list,
            max_pages_per_proxy=1,
            screenshot=True,
            user_agent=ua,
            headless=False,
            respect_robots=False,
            min_dwell_ms=30000,
            max_dwell_ms=30000,
            actions=actions_list,
            rotate_android_profiles=True,
        )
    except Exception as e:
        err = str(e)
        return HTMLResponse(content=f"<div class=\"card\"><h3>Error al ejecutar</h3><pre>{err}</pre></div>")

    # Render HTML sencillo con enlaces a capturas
    items = []
    for r in results:
        url = r.get("url")
        status = r.get("status")
        proxy = r.get("proxy")
        elapsed = r.get("elapsed_ms")
        ss = r.get("screenshot")
        li = f"<li><strong>{url}</strong> â€” estado: <code>{status}</code> â€” proxy: <code>{proxy or 'ninguno'}</code> â€” tiempo: {elapsed} ms"
        if ss:
            ss_web = ss.replace("\\", "/")
            li += f" â€” captura: <a href=\"/{ss_web}\" target=\"_blank\">ver</a>"
        li += "</li>"
        items.append(li)

    actions_json = json.dumps(actions_list, ensure_ascii=False, indent=2)
    html = f"""
    <div class=\"card\">
      <h3>Resumen de ejecuciÃ³n</h3>
      <ul>{''.join(items)}</ul>
      <p class=\"hint\">Se abriÃ³ una ventana de Chrome para ver la ejecuciÃ³n en vivo.</p>
      <details style=\"margin-top:12px;\"><summary>Acciones aplicadas</summary>
        <pre style=\"background:#0f141b;color:#e5e7eb;border:1px solid #334155;border-radius:6px;padding:8px;white-space:pre-wrap;\">{actions_json}</pre>
      </details>
    </div>
    """
    return HTMLResponse(content=html)

redis_conn = Redis.from_url(redis_url)
queue = Queue(queue_name, connection=redis_conn)


class EnqueueBrowserRequest(BaseModel):
    urls: List[AnyHttpUrl] = Field(
        ..., description="Lista de URLs a visitar (http/https)",
        examples=[["https://example.com", "https://example.com/docs"]],
    )
    max_pages_per_proxy: int = Field(
        5, description="PÃ¡ginas por proxy antes de rotar (ejemplo: 3)", examples=[3]
    )
    screenshot: bool = Field(
        True, description="Guardar capturas de las pÃ¡ginas en artifacts/ (true/false)", examples=[True]
    )
    user_agent: Optional[str] = Field(
        None, description="User-Agent personalizado (opcional)", examples=["TestBot/1.0 (+contacto@ejemplo.com)"]
    )
    headless: bool = Field(
        True, description="Ejecutar sin mostrar la ventana del navegador (true) o mostrarla (false)", examples=[True]
    )
    respect_robots: bool = Field(
        False, description="Respetar robots.txt (si true, evita URLs bloqueadas)", examples=[True]
    )
    min_dwell_ms: int = Field(
        3000, description="Permanencia mÃ­nima por pÃ¡gina en milisegundos (ejemplo: 5000)", examples=[5000]
    )
    max_dwell_ms: int = Field(
        15000, description="Permanencia mÃ¡xima por pÃ¡gina en milisegundos (ejemplo: 15000)", examples=[15000]
    )
    actions: Optional[List[Dict[str, str]]] = Field(
        None,
        description=(
            "Acciones opcionales a realizar en la pÃ¡gina en orden. Soporta: \n"
            "- wait_for: espera un selector (selector).\n"
            "- click: clic en un selector (selector).\n"
            "- type: escribir texto en un selector (selector, text).\n"
            "- js: ejecutar JavaScript arbitrario (code).\n"
            "- play_video: intenta reproducir un <video> por selector o por defecto."
        ),
        examples=[
            [
                {"type": "wait_for", "selector": "video"},
                {"type": "play_video"}
            ]
        ],
    )

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "urls": ["https://tu-sitio.com", "https://tu-sitio.com/pagina"],
                    "max_pages_per_proxy": 3,
                    "screenshot": True,
                    "user_agent": "TestBot/1.0 (+contacto@tu-sitio.com)",
                    "headless": True,
                    "respect_robots": True,
                    "min_dwell_ms": 5000,
                    "max_dwell_ms": 15000,
                    "actions": [
                        {"type": "wait_for", "selector": "video"},
                        {"type": "play_video"}
                    ],
                }
            ]
        }
    )


@app.post(
    "/enqueue_browser",
    summary="Encolar navegaciÃ³n de pÃ¡ginas",
    description=(
        "Encola una tarea que recorre las URLs dadas con comportamiento de lectura (scroll y permanencia).\n"
        "Campos:\n- urls: lista de URLs (http/https).\n- max_pages_per_proxy: rotaciÃ³n de proxy tras N pÃ¡ginas.\n"
        "- screenshot: guardar capturas en artifacts/.\n- respect_robots: si true, no visita URLs bloqueadas.\n"
        "- min_dwell_ms/max_dwell_ms: tiempo aproximado de permanencia por pÃ¡gina (ms)."
    ),
)
def enqueue_browser(req: EnqueueBrowserRequest):
    job = queue.enqueue(
        visit_links_with_rotation,
        [str(u) for u in req.urls],
        req.max_pages_per_proxy,
        req.screenshot,
        req.user_agent,
        req.headless,
        req.respect_robots,
        req.min_dwell_ms,
        req.max_dwell_ms,
        req.actions,
        job_timeout=180,
        result_ttl=1800,
        failure_ttl=1800,
    )
    return {"job_id": job.id, "queue": queue_name}


@app.get("/status/{job_id}", summary="Ver estado de un job")
def get_status(job_id: str):
    job = Job.fetch(job_id, connection=redis_conn)
    return {
        "id": job.id,
        "status": job.get_status(),
        "enqueued_at": job.enqueued_at,
        "started_at": job.started_at,
        "ended_at": job.ended_at,
    }


@app.get("/result/{job_id}", summary="Ver resultado de un job")
def get_result(job_id: str):
    job = Job.fetch(job_id, connection=redis_conn)
    return {"id": job.id, "result": job.result}


# EjecuciÃ³n local (sin cola) para simplificar pruebas: devuelve el JSON directamente
@app.post(
    "/run_browser",
    summary="Ejecutar navegaciÃ³n local (sin cola)",
    description=(
        "Ejecuta la visita de las URLs dadas de forma inmediata y devuelve el JSON con resultados.\n"
        "Ãštil cuando no tienes Redis/worker activo. Usa los mismos campos que /enqueue_browser."
    ),
)
def run_browser(req: EnqueueBrowserRequest):
    result = visit_links_with_rotation(
        [str(u) for u in req.urls],
        req.max_pages_per_proxy,
        req.screenshot,
        req.user_agent,
        req.headless,
        req.respect_robots,
        req.min_dwell_ms,
        req.max_dwell_ms,
        req.actions,
    )
    return JSONResponse(result)


# Guardar URLs desde formulario en urls.txt (implantar sin mostrar JSON)
@app.post("/save_urls_form", response_class=HTMLResponse, summary="Guardar URLs en urls.txt")
def save_urls_form(urls_text: str = Form(...)):
    urls = [u.strip() for u in urls_text.splitlines() if u.strip()]
    saved = 0
    try:
        with open("urls.txt", "a", encoding="utf-8") as f:
            for u in urls:
                f.write(u + "\n")
                saved += 1
        message = f"Se implantaron {saved} URL(s) en urls.txt."
        ok = True
    except Exception as e:
        message = f"Error guardando URLs: {str(e)}"
        ok = False
    page = f"""
<!doctype html>
<html lang=\"es\">
<head>
  <meta charset=\"utf-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
  <title>Guardar URLs â€” Granja de Bots (QA)</title>
  <style>
    body {{ font-family: system-ui, Arial, sans-serif; margin: 20px; color: #222; }}
    .ok {{ color: #0a7d2a; }}
    .err {{ color: #b00020; }}
    a.button {{ display:inline-block; padding:8px 12px; border:1px solid #444; border-radius:4px; text-decoration:none; }}
  </style>
  </head>
  <body>
    <h1>Guardar URLs</h1>
    <p class=\"{ 'ok' if ok else 'err' }\">{message}</p>
    <p><a class=\"button\" href=\"/ui\">Volver</a></p>
  </body>
  </html>
    """
    return HTMLResponse(content=page)


@app.post("/run_browser_form", response_class=HTMLResponse, summary="Ejecutar local desde formulario")
def run_browser_form(
    urls_text: str = Form(...),
    max_pages_per_proxy: int = Form(3),
    screenshot: bool | None = Form(None),
    user_agent: str | None = Form(None),
    show_browser: bool | None = Form(None),
    force_incognito: bool | None = Form(None),
    respect_robots: bool | None = Form(None),
    min_dwell_ms: int = Form(5000),
    max_dwell_ms: int = Form(15000),
    play_video: bool | None = Form(None),
    gmail_login: bool | None = Form(None),
    gmail_email: str | None = Form(None),
    gmail_password: str | None = Form(None),
    rotate_vpn_per_url: bool | None = Form(None),
    vpn_provider: str | None = Form(None),
    vpn_country: str | None = Form(None),
    vpn_wait_ms: int = Form(5000),
):
    urls = [u.strip() for u in urls_text.splitlines() if u.strip()]
    headless = not bool(show_browser)
    sc = bool(screenshot)
    rr = bool(respect_robots)
    actions = []
    # Si se pide login Gmail, anteponer acción dedicada
    if bool(gmail_login) and (gmail_email or "") and (gmail_password or ""):
        actions.append({"type": "gmail_sign_in", "email": gmail_email or "", "password": gmail_password or ""})
    if bool(play_video):
        actions = [
            {"type": "wait_for", "selector": "video"},
            {"type": "wait_for", "selector": "button.ytp-large-play-button"},
            {
                "type": "js",
                "code": r"""
                (function(){
                  try {
                    // Aceptar consentimientos genÃ©ricos y de YouTube por texto
                    const texts = ['Aceptar','Acepto','Estoy de acuerdo','Continuar','Ok','Entendido','Permitir','Agree','Accept','Accept all'];
                    const clickable = Array.from(document.querySelectorAll('button, a, [role="button"]'));
                    const btn = clickable.find(el => {
                      const t = (el.textContent||'').trim().toLowerCase();
                      return texts.some(x => t.includes(x.toLowerCase()));
                    });
                    if (btn) { try { btn.click(); } catch(e){} }
                  } catch(e){}
                  try {
                    const host = location.hostname || '';
                    const generic = [
                      '.vjs-big-play-button',
                      '.vjs-play-control',
                      '.jw-icon-play',
                      '.jw-icon-replay',
                      'button[aria-label="Play"]',
                      'button[aria-label="Reproducir"]',
                      '[data-testid="play-button"]'
                    ];
                    const youtube = [
                      'button.ytp-large-play-button',
                      'button.ytp-play-button',
                      'button[aria-label="Reproducir"]',
                      'button[aria-label="Play"]',
                    ];
                    const candidates = /youtube\.com$/.test(host) ? youtube.concat(generic) : generic;
                    for (const sel of candidates) {
                      try {
                        const el = document.querySelector(sel);
                        if (el) { el.click(); break; }
                      } catch(e){}
                    }
                  } catch(e){}
                })();
                """
            },
            {"type": "play_video"},
            {
                "type": "js",
                "code": """
                (function(){
                  try {
                    const v = document.querySelector('video');
                    if (v) {
                      try { v.scrollIntoView({behavior:'smooth', block:'center'}); } catch(e){}
                      try { v.muted = false; v.volume = 0.7; } catch(e){}
                    }
                  } catch(e){}
                })();
                """
            }
        ]

    result = visit_links_with_rotation(
        urls=urls,
        max_pages_per_proxy=max_pages_per_proxy,
        screenshot=sc,
        user_agent=user_agent,
        headless=headless,
        force_incognito=bool(force_incognito),
        respect_robots=rr,
        min_dwell_ms=min_dwell_ms,
        max_dwell_ms=max_dwell_ms,
        actions=actions,
        rotate_vpn_per_url=bool(rotate_vpn_per_url),
        vpn_provider=vpn_provider,
        vpn_country=vpn_country,
        vpn_wait_ms=vpn_wait_ms,
    )
    # Construir salida minimalista: tilde verde si OK y "Ver detalles" con error
    items_html = []
    total = len(result.get("results", []))
    for idx, r in enumerate(result.get("results", []), start=1):
        url = r.get("url")
        status = r.get("status") or "unknown"
        error = r.get("error")
        proxy = r.get("proxy")
        elapsed = r.get("elapsed_ms")
        ss = r.get("screenshot")
        vpn_p = r.get("vpn_provider")
        vpn_c = r.get("vpn_country")
        is_ok = status == "ok"
        is_blocked = status == "blocked_by_robots"
        symbol = "âœ“" if is_ok else ("!" if is_blocked else "âœ•")
        cls = "ok" if is_ok else ("warn" if is_blocked else "err")
        det_id = f"det_{idx}"
        det_lines = []
        det_lines.append(f"URL: {url}")
        det_lines.append(f"Estado: {status}")
        if error:
            det_lines.append(f"Error: {error}")
        det_lines.append(f"Tiempo: {elapsed} ms")
        det_lines.append(f"Proxy: {proxy or 'ninguno'}")
        if vpn_p:
            det_lines.append(f"VPN: {vpn_p} ({vpn_c or 'desconocido'})")
        if ss:
            ss_web = ss.replace('\\', '/')
            det_lines.append(f"Captura: /{ss_web}")
        details_text = "\n".join(det_lines)
        item = (
            f"<li>"
            f"<span class=\"status {cls}\" aria-label=\"{status}\">{symbol}</span> "
            f"<code>{url}</code> "
            f"<a href=\"#\" onclick=\"toggle('{det_id}');return false;\" class=\"details\">Ver detalles</a>"
            f"<div id=\"{det_id}\" class=\"hidden\"><pre>{details_text}</pre>"
            + (f"<p><a href=\"/{ss.replace('\\', '/')}\" target=\"_blank\">Ver captura</a></p>" if ss else "")
            + "</div>" 
            + "</li>"
        )
        items_html.append(item)

    page = f"""
<!doctype html>
<html lang=\"es\">
<head>
  <meta charset=\"utf-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
  <title>Consola de procesos</title>
  <style>
    body {{ font-family: system-ui, -apple-system, Segoe UI, Roboto, Arial, sans-serif; margin: 20px; color: #222; }}
    ul {{ list-style: none; padding-left: 0; }}
    li {{ margin: 8px 0; }}
    code {{ background: #f1f1f1; padding: 2px 4px; border-radius: 3px; }}
    .status {{ display:inline-block; width: 20px; text-align:center; font-weight: bold; }}
    .ok {{ color: #28a745; }}
    .err {{ color: #d73a49; }}
    .warn {{ color: #e5a100; }}
    .details {{ margin-left: 8px; }}
    .hidden {{ display:none; margin-top:6px; }}
    pre {{ background: #f7f7f7; padding: 8px; border: 1px solid #ddd; overflow:auto; }}
  </style>
  <script>
    function toggle(id) {{
      var el = document.getElementById(id);
      if (!el) return;
      el.style.display = (el.style.display === 'none' || el.style.display === '') ? 'block' : 'none';
    }}
  </script>
  </head>
  <body>
    <h1>Consola de procesos</h1>
    <p>Procesos: {total}. Mostrando tilde verde si funcionÃ³ y "Ver detalles" para errores.</p>
    <ul>
      {''.join(items_html)}
    </ul>
    <p><a href=\"/ui\">Volver</a></p>
  </body>
  </html>
    """
    return HTMLResponse(content=page)


# EjecuciÃ³n local silenciosa: muestra un resumen HTML sin JSON
@app.post("/run_browser_silent_form", response_class=HTMLResponse, summary="Ejecutar local (silencioso)")
def run_browser_silent_form(
    urls_text: str = Form(...),
    max_pages_per_proxy: int = Form(3),
    screenshot: bool | None = Form(None),
    user_agent: str | None = Form(None),
    show_browser: bool | None = Form(None),
    force_incognito: bool | None = Form(None),
    respect_robots: bool | None = Form(None),
    min_dwell_ms: int = Form(5000),
    max_dwell_ms: int = Form(15000),
    play_video: bool | None = Form(None),
    gmail_login: bool | None = Form(None),
    gmail_email: str | None = Form(None),
    gmail_password: str | None = Form(None),
    rotate_vpn_per_url: bool | None = Form(None),
    vpn_provider: str | None = Form(None),
    vpn_country: str | None = Form(None),
    vpn_wait_ms: int = Form(5000),
    inline: bool | None = Form(None),
):
    urls = [u.strip() for u in urls_text.splitlines() if u.strip()]
    headless = not bool(show_browser)
    sc = bool(screenshot)
    rr = bool(respect_robots)

    actions = []
    if bool(gmail_login) and (gmail_email or "") and (gmail_password or ""):
        actions.append({"type": "gmail_sign_in", "email": gmail_email or "", "password": gmail_password or ""})
    if bool(play_video):
        actions = [
            {"type": "wait_for", "selector": "video"},
            {"type": "wait_for", "selector": "button.ytp-large-play-button"},
            {
                "type": "js",
                "code": r"""
                (function(){
                  try {
                    // Aceptar consentimientos por texto
                    const texts = ['Aceptar','Acepto','Estoy de acuerdo','Continuar','Ok','Entendido','Permitir','Agree','Accept','Accept all'];
                    const clickable = Array.from(document.querySelectorAll('button, a, [role="button"]'));
                    const btn = clickable.find(el => {
                      const t = (el.textContent||'').trim().toLowerCase();
                      return texts.some(x => t.includes(x.toLowerCase()));
                    });
                    if (btn) { try { btn.click(); } catch(e){} }
                  } catch(e){}
                  try {
                    const host = location.hostname || '';
                    const generic = [
                      '.vjs-big-play-button',
                      '.vjs-play-control',
                      '.jw-icon-play',
                      '.jw-icon-replay',
                      'button[aria-label="Play"]',
                      'button[aria-label="Reproducir"]',
                      '[data-testid="play-button"]'
                    ];
                    const youtube = [
                      'button.ytp-large-play-button',
                      'button.ytp-play-button',
                      'button[aria-label="Reproducir"]',
                      'button[aria-label="Play"]',
                    ];
                    const candidates = /youtube\.com$/.test(host) ? youtube.concat(generic) : generic;
                    for (const sel of candidates) {
                      try {
                        const el = document.querySelector(sel);
                        if (el) { el.click(); break; }
                      } catch(e){}
                    }
                  } catch(e){}
                })();
                """
            },
            {"type": "play_video"},
            {
                "type": "js",
                "code": """
                (function(){
                  try {
                    const v = document.querySelector('video');
                    if (v) {
                      try { v.scrollIntoView({behavior:'smooth', block:'center'}); } catch(e){}
                      try { v.muted = false; v.volume = 0.7; } catch(e){}
                    }
                  } catch(e){}
                })();
                """
            }
        ]

    result = visit_links_with_rotation(
        urls=urls,
        max_pages_per_proxy=max_pages_per_proxy,
        screenshot=sc,
        user_agent=user_agent,
        headless=headless,
        force_incognito=bool(force_incognito),
        respect_robots=rr,
        min_dwell_ms=min_dwell_ms,
        max_dwell_ms=max_dwell_ms,
        actions=actions,
        rotate_vpn_per_url=bool(rotate_vpn_per_url),
        vpn_provider=vpn_provider,
        vpn_country=vpn_country,
        vpn_wait_ms=vpn_wait_ms,
    )

    # Construye resumen HTML y contador de reproducciones
    items = []
    total = len(result.get("results", []))
    plays = 0
    for i, r in enumerate(result.get("results", [])):
        url = r.get("url")
        status = r.get("status")
        proxy = r.get("proxy")
        ss = r.get("screenshot")
        elapsed = r.get("elapsed_ms")
        vpn_provider = r.get("vpn_provider")
        vpn_country = r.get("vpn_country")
        video_playing = r.get("video_playing")
        status_class = "ok" if status == "ok" else ("warn" if status == "blocked_by_robots" else "err")
        if video_playing:
            plays += 1
        li = f"<li class=\"{status_class}\"><code>{url}</code> â€” estado: <code>{status}</code> â€” proxy: <code>{proxy or 'ninguno'}</code>"
        if vpn_provider:
            vp = vpn_provider or 'desconocido'
            vc = vpn_country or 'desconocido'
            li += f" â€” vpn: <code>{vp} ({vc})</code>"
        if video_playing is not None:
            li += f" â€” video: <code>{'reproduciendo' if video_playing else 'no'}</code>"
        li += f" â€” tiempo: {elapsed} ms"
        if ss:
            ss_web = ss.replace("\\", "/")
            li += f" â€” captura: <a href=\"/{ss_web}\" target=\"_blank\">ver</a>"
        li += "</li>"
        items.append(li)
    # Si se solicita inline, devolver solo el fragmento para incrustar en la home
    if bool(inline):
        snippet = f"""
<div class=\"inline-result\">
  <h3>Resultado (silencioso)</h3>
  <p><strong>Reproducciones exitosas:</strong> {plays} de {total}</p>
  <p>Se visitaron {len(items)} URL(s). Debajo tienes un resumen y enlaces a capturas si las activaste.</p>
  <ul class=\"results\">
    {''.join(items)}
  </ul>
</div>
        """
        return HTMLResponse(content=snippet)

    page = f"""
<!doctype html>
<html lang=\"es\">
<head>
  <meta charset=\"utf-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
  <title>Resultado (silencioso) â€” Granja de Bots (QA)</title>
  <style>
    body {{ font-family: system-ui, Arial, sans-serif; margin: 20px; color: #222; }}
    ul {{ padding-left: 20px; }}
    code {{ background: #f1f1f1; padding: 2px 4px; border-radius: 3px; }}
    a.button {{ display:inline-block; padding:8px 12px; border:1px solid #444; border-radius:4px; text-decoration:none; }}
  </style>
  </head>
  <body>
    <h1>Resultado (silencioso)</h1>
    <p><strong>Reproducciones exitosas:</strong> {plays} de {total}</p>
    <p>Se visitaron {len(items)} URL(s). Debajo tienes un resumen y enlaces a capturas si las activaste.</p>
    <ul>
      {''.join(items)}
    </ul>
    <p><a class=\"button\" href=\"/ui\">Volver</a></p>
  </body>
  </html>
    """
    return HTMLResponse(content=page)


# Variante inline mÃ­nima del modo silencioso: solo mini y botÃ³n "Ver"
@app.post("/run_browser_silent_inline", response_class=HTMLResponse, summary="Ejecutar local (silencioso, inline)")
def run_browser_silent_inline(
    urls_text: str = Form(...),
    max_pages_per_proxy: int = Form(3),
    screenshot: bool | None = Form(None),
    user_agent: str | None = Form(None),
    show_browser: bool | None = Form(None),
    force_incognito: bool | None = Form(None),
    respect_robots: bool | None = Form(None),
    min_dwell_ms: int = Form(5000),
    max_dwell_ms: int = Form(15000),
    play_video: bool | None = Form(None),
    gmail_login: bool | None = Form(None),
    gmail_email: str | None = Form(None),
    gmail_password: str | None = Form(None),
    rotate_vpn_per_url: bool | None = Form(None),
    vpn_provider: str | None = Form(None),
    vpn_country: str | None = Form(None),
    vpn_wait_ms: int = Form(5000),
):
    urls = [u.strip() for u in urls_text.splitlines() if u.strip()]
    headless = not bool(show_browser)
    sc = bool(screenshot)
    rr = bool(respect_robots)
    want_video = bool(play_video)

    actions = []
    if bool(gmail_login) and (gmail_email or "") and (gmail_password or ""):
        actions.append({"type": "gmail_sign_in", "email": gmail_email or "", "password": gmail_password or ""})
    if bool(play_video):
        actions = [
            {"type": "wait_for", "selector": "video"},
            {"type": "wait_for", "selector": "button.ytp-large-play-button"},
            {
                "type": "js",
                "code": r"""
                (function(){
                  try {
                    const texts = ['Aceptar','Acepto','Estoy de acuerdo','Continuar','Ok','Entendido','Permitir','Agree','Accept','Accept all'];
                    const clickable = Array.from(document.querySelectorAll('button, a, [role="button"]'));
                    const btn = clickable.find(el => {
                      const t = (el.textContent||'').trim().toLowerCase();
                      return texts.some(x => t.includes(x.toLowerCase()));
                    });
                    if (btn) { try { btn.click(); } catch(e){} }
                  } catch(e){}
                  try {
                    const host = location.hostname || '';
                    const generic = [
                      '.vjs-big-play-button',
                      '.vjs-play-control',
                      '.jw-icon-play',
                      '.jw-icon-replay',
                      'button[aria-label="Play"]',
                      'button[aria-label="Reproducir"]',
                      '[data-testid="play-button"]'
                    ];
                    const youtube = [
                      'button.ytp-large-play-button',
                      'button.ytp-play-button',
                      'button[aria-label="Reproducir"]',
                      'button[aria-label="Play"]',
                    ];
                    const candidates = /youtube\.com$/.test(host) ? youtube.concat(generic) : generic;
                    for (const sel of candidates) {
                      try { const el = document.querySelector(sel); if (el) { el.click(); break; } } catch(e){}
                    }
                  } catch(e){}
                })();
                """
            },
            {"type": "play_video"},
            {
                "type": "js",
                "code": """
                (function(){
                  try {
                    const v = document.querySelector('video');
                    if (v) {
                      try { v.scrollIntoView({behavior:'smooth', block:'center'}); } catch(e){}
                      try { v.muted = false; v.volume = 0.7; } catch(e){}
                    }
                  } catch(e){}
                })();
                """
            }
        ]

    result = visit_links_with_rotation(
        urls=urls,
        max_pages_per_proxy=max_pages_per_proxy,
        screenshot=sc,
        user_agent=user_agent,
        headless=headless,
        force_incognito=bool(force_incognito),
        respect_robots=rr,
        min_dwell_ms=min_dwell_ms,
        max_dwell_ms=max_dwell_ms,
        actions=actions,
        rotate_vpn_per_url=bool(rotate_vpn_per_url),
        vpn_provider=vpn_provider,
        vpn_country=vpn_country,
        vpn_wait_ms=vpn_wait_ms,
    )

    items = []
    for r in result.get("results", []):
        url = r.get("url")
        status = r.get("status")
        proxy = r.get("proxy")
        ss = r.get("screenshot")
        elapsed = r.get("elapsed_ms")
        vpn_p = r.get("vpn_provider")
        vpn_c = r.get("vpn_country")
        video_playing = r.get("video_playing")
        status_class = "ok" if status == "ok" else ("warn" if status == "blocked_by_robots" else "err")
        li = f"<li class=\"{status_class}\"><code>{url}</code> â€” estado: <code>{status}</code>"
        if proxy:
            li += f" â€” proxy: <code>{proxy}</code>"
        if vpn_p:
            li += f" â€” vpn: <code>{vpn_p} ({vpn_c or 'desconocido'})</code>"
        if video_playing is not None:
            li += f" â€” video: <code>{'reproduciendo' if video_playing else 'no'}</code>"
        li += f" â€” tiempo: {elapsed} ms"
        if ss:
            ss_web = ss.replace("\\", "/")
            li += f" â€” captura: <a href=\"/{ss_web}\" target=\"_blank\">ver</a>"
        li += "</li>"
        items.append(li)

    statuses = [r.get("status") for r in result.get("results", [])]
    any_err = any(s not in ("ok", "blocked_by_robots") for s in statuses)
    any_warn = (not any_err) and any(s == "blocked_by_robots" for s in statuses)
    btn_cls = "ok" if (not any_err and not any_warn) else ("warn" if any_warn else "err")

    mini_player_html = ""
    for r in result.get("results", []):
        try:
            url = r.get("url") or ""
            video_playing = r.get("video_playing")
            ss = r.get("screenshot")
            parsed = urllib.parse.urlparse(url)
            host = (parsed.netloc or "").lower()

            # Si estÃ¡ reproduciendo video y es YouTube, insertar iframe mini con autoplay
            if (video_playing or want_video) and ("youtube.com" in host or "youtu.be" in host):
                vid = None
                if "youtube.com" in host:
                    # Soportar /watch?v= y /shorts/<id>
                    path = (parsed.path or "").lower()
                    if "/shorts/" in path:
                        segs = [s for s in path.split("/") if s]
                        try:
                            i = segs.index("shorts")
                            vid = segs[i+1] if len(segs) > i+1 else None
                        except Exception:
                            vid = None
                    if not vid:
                        qs = urllib.parse.parse_qs(parsed.query or "")
                        vid = (qs.get("v") or [None])[0]
                elif "youtu.be" in host:
                    # Ruta /<id>
                    segs = [s for s in (parsed.path or "").split("/") if s]
                    vid = segs[0] if segs else None
                if vid:
                    mini_player_html = (
                        f'<div class="mini-player" style="width: 160px; height: 90px; border:1px solid #333; border-radius:6px; overflow:hidden;">'
                        f'<iframe src="https://www.youtube.com/embed/{vid}?autoplay=1&mute=1" title="YouTube mini" frameborder="0" allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share" allowfullscreen></iframe>'
                        f'</div>'
                    )
                    break

            # Si no hay video embebible, mostrar captura si existe
            if not mini_player_html and ss:
                ss_web = ss.replace("\\", "/")
                mini_player_html = (
                    f"<div class=\"mini-thumb\"><img src=\"/{ss_web}\" alt=\"captura\" loading=\"lazy\" /></div>"
                )
                break
        except Exception:
            pass

    snippet = f"""
<div class=\"inline-silent\">\n  <div class=\"mini-bar\">\n    {mini_player_html}\n    <button class=\"view-btn {btn_cls}\" onclick=\"this.closest('div.inline-silent').querySelector('.silent-full').classList.remove('hidden'); this.classList.add('hidden'); return false;\">Ver Resultado (silencioso)</button>\n  </div>\n  <div class=\"silent-full hidden\">\n    <ul class=\"results\">\n      {''.join(items)}\n    </ul>\n  </div>\n</div>\n    """

    return HTMLResponse(content=snippet)


@app.post("/run_browser_silent_batch", response_class=HTMLResponse, summary="Ejecutar local (silencioso, batch inline)")
def run_browser_silent_batch(
    urls_text: str = Form(...),
    instances: int = Form(1),
    play_video: bool | None = Form(None),
):
    # Tomar primera URL como fuente de video (v1 minimal)
    urls = [u.strip() for u in urls_text.splitlines() if u.strip()]
    url = urls[0] if urls else ""
    parsed = urllib.parse.urlparse(url)
    host = (parsed.netloc or "").lower()
    want_video = bool(play_video)
    instances = int(instances or 1)
    if instances < 1:
        instances = 1
    if instances > 100:
        instances = 100

    counter_id = uuid.uuid4().hex
    minis = []
    vid = None
    if "youtube.com" in host:
        # Soportar /watch?v=, /shorts/<id>
        path = (parsed.path or "").lower()
        if "/shorts/" in path:
            segs = [s for s in path.split("/") if s]
            try:
                i = segs.index("shorts")
                vid = segs[i+1] if len(segs) > i+1 else None
            except Exception:
                vid = None
        if not vid:
            qs = urllib.parse.parse_qs(parsed.query or "")
            vid = (qs.get("v") or [None])[0]
    elif "youtu.be" in host:
        segs = [s for s in (parsed.path or "").split("/") if s]
        vid = segs[0] if segs else None

    if vid and want_video:
        for i in range(instances):
            minis.append(
                f'<div class="mini warn" data-counter-id="{counter_id}" data-index="{i}">'
                f'<a href="/artifacts/{counter_id}_{i}.png" target="_blank" title="ver captura">'
                f'<img class="mini-live" data-src-base="/artifacts/{counter_id}_{i}.png" src="/artifacts/{counter_id}_{i}.png" style="width:160px;height:90px;border:1px solid #333;border-radius:6px;object-fit:cover;background:#0f1115;" alt="vista previa" />'
                f'</a>'
                f'<span class="badge">Ejecutando</span>'
                f'</div>'
            )
    else:
        minis.append(
            f"<div class=\"mini warn\">No es un enlace de YouTube embebible o reproducciÃ³n no solicitada.</div>"
        )

    snippet = f"""
<div class=\"inline-silent-batch\">
  <div class=\"mini-grid\" style=\"display:flex;flex-wrap:wrap;gap:8px;\">
    {''.join(minis)}
  </div>
  <div class=\"repro-line\" id=\"counter-{counter_id}\" data-url=\"{url}\">\n    <code>{url}</code> â€” <span class=\"count\">0</span> reproducciones\n  </div>
</div>
    """
    return HTMLResponse(content=snippet)


@app.post("/run_browser_headful_batch", response_class=HTMLResponse, summary="Ejecutar como navegador real (25 simultÃ¡neos)")
def run_browser_headful_batch(
    urls_text: str = Form(...),
    instances: int = Form(1),
    min_dwell_ms: int = Form(20000),
    play_video: bool | None = Form(None),
):
    """Abre pestaÃ±as reales con Playwright (Chromium no headless) en grupos de 25.
    Intenta reproducir el video en la pÃ¡gina de YouTube y mantiene cada pestaÃ±a
    abierta al menos `min_dwell_ms` milisegundos.
    """
    urls = [u.strip() for u in urls_text.splitlines() if u.strip()]
    url = urls[0] if urls else ""
    instances = int(instances or 1)
    if instances < 1:
        instances = 1
    counter_id = uuid.uuid4().hex
    group_size = 25

    def worker_open_tabs(urls_count: int, dwell_ms: int):
        from playwright.sync_api import sync_playwright
        try:
            with sync_playwright() as p:
                ctx = _launch_persistent_ctx(p)
                pages = []
                for i in range(urls_count):
                    # Abrir por grupos de 25 simultÃ¡neos
                    if i % group_size == 0 and i > 0:
                        time.sleep(dwell_ms / 1000)
                        while pages:
                            pg = pages.pop()
                            try:
                                pg.close()
                            except Exception:
                                pass
                    page = ctx.new_page()
                    page.set_viewport_size({"width": 480, "height": 270})
                    try:
                        page.goto(url, wait_until="domcontentloaded")
                        # Intentar aceptar consentimientos genÃ©ricos
                        try:
                            texts = ['Aceptar','Acepto','Estoy de acuerdo','Continuar','Ok','Entendido','Permitir','Agree','Accept','Accept all']
                            clickable = page.locator('button, a, [role="button"]')
                            for t in texts:
                                try:
                                    clickable.filter({"hasText": t}).first.click(timeout=1000)
                                except Exception:
                                    pass
                        except Exception:
                            pass
                        # Intentar reproducir en watch: botÃ³n grande o tecla espacio
                        try:
                            page.locator('button.ytp-large-play-button').click(timeout=3000)
                        except Exception:
                            try:
                                page.keyboard.press('Space')
                            except Exception:
                                pass
                        try:
                            page.evaluate("() => { const v = document.querySelector('video'); if (v) { try { v.muted = true; } catch(e){} try { v.play(); } catch(e){} } }")
                        except Exception:
                            pass
                    except Exception:
                        pass
                    pages.append(page)
                # Esperar y cerrar Ãºltimas
                time.sleep(dwell_ms / 1000)
                for pg in pages:
                    try:
                        pg.close()
                    except Exception:
                        pass
                try:
                    ctx.close()
                except Exception:
                    pass
        except Exception:
            pass

    # Ejecutar en hilo para no bloquear la respuesta
    threading.Thread(target=worker_open_tabs, args=(instances, min_dwell_ms), daemon=True).start()

    # Render mini resumen con paginaciÃ³n 25 visibles + botÃ³n "MÃ¡s +"
    minis = []
    for i in range(instances):
        minis.append(
            f"<div class=\"mini ok\" data-counter-id=\"{counter_id}\"><div class=\"mini-player\" style=\"width:160px;height:90px;border:1px solid #333;border-radius:6px;overflow:hidden;display:flex;align-items:center;justify-content:center;color:#bbb;font-size:12px;\">Tab {i+1}</div></div>"
        )
    visible_limit = 25
    visible = minis[:visible_limit]
    hidden = minis[visible_limit:]
    more_btn = ""
    hidden_block = ""
    if hidden:
        more_btn = (
            f"<button class=\"more-btn\" data-target=\"hidden-{counter_id}\" style=\"margin-top:8px;padding:6px 10px;border:1px solid #4caf50;color:#fff;background:#2e7d32;border-radius:6px;cursor:pointer;\">MÃ¡s +</button>"
        )
        hidden_block = (
            f"<div id=\"hidden-{counter_id}\" class=\"mini-grid\" style=\"display:none;flex-wrap:wrap;gap:8px;margin-top:10px;\">{''.join(hidden)}</div>"
        )

    snippet = f"""
<div class=\"inline-silent-batch\">\n  <div class=\"mini-grid\" id=\"visible-{counter_id}\" style=\"display:flex;flex-wrap:wrap;gap:8px;\">\n    {''.join(visible)}\n  </div>\n  <div class=\"mini-toolbar\" style=\"display:flex;align-items:center;gap:10px;margin-top:6px;\">\n    <span class=\"muted\">Mostrando {min(len(minis), visible_limit)} de {len(minis)}</span>\n    {more_btn}\n  </div>\n  {hidden_block}\n  <div class=\"repro-line\"><code>{url}</code> â€” Tabs reales (mÃ¡x. 25 simultÃ¡neas)</div>\n</div>\n    """
    return HTMLResponse(content=snippet)


@app.post("/run_browser_headful_grid", response_class=HTMLResponse, summary="Navegadores visibles en cuadrÃ­cula (mÃ¡x 25)")
def run_browser_headful_grid(
    urls_text: str = Form(...),
    instances: int = Form(1),
    min_dwell_ms: int = Form(60000),
):
    """Abre hasta 25 ventanas reales de Chromium posicionadas en cuadrÃ­cula.
    Cada ventana navega a la URL y reproduce el video en silencio.
    """
    urls = [u.strip() for u in urls_text.splitlines() if u.strip()]
    url = urls[0] if urls else ""
    instances = max(1, int(instances or 1))
    limit = min(instances, 25)
    counter_id = uuid.uuid4().hex

    cols = 5
    win_w, win_h = 480, 270
    gap_x, gap_y = 10, 40

    def open_window(idx: int):
        x = (idx % cols) * (win_w + gap_x)
        y = (idx // cols) * (win_h + gap_y)
        from playwright.sync_api import sync_playwright
        try:
            with sync_playwright() as p:
                ctx = _launch_persistent_ctx(
                    p,
                    args=[f"--window-size={win_w},{win_h}", f"--window-position={x},{y}"],
                    viewport={"width": win_w, "height": win_h},
                    window_size=(win_w, win_h),
                    window_pos=(x, y),
                )
                page = ctx.new_page()
                try:
                    page.goto(url, wait_until="domcontentloaded")
                    # Consentimientos genÃ©ricos
                    try:
                        texts = ['Aceptar','Acepto','Estoy de acuerdo','Continuar','Ok','Entendido','Permitir','Agree','Accept','Accept all']
                        clickable = page.locator('button, a, [role="button"]')
                        for t in texts:
                            try:
                                clickable.filter({"hasText": t}).first.click(timeout=1000)
                            except Exception:
                                pass
                    except Exception:
                        pass

                    # Intentar reproducir
                    try:
                        page.locator('button.ytp-large-play-button').click(timeout=3000)
                    except Exception:
                        try:
                            page.keyboard.press('k')
                        except Exception:
                            pass
                    try:
                        page.evaluate("() => { const v = document.querySelector('video'); if (v) { try { v.muted = true; } catch(e){} try { v.play(); } catch(e){} } }")
                    except Exception:
                        pass
                except Exception:
                    pass
                # Mantener abierta y cerrar
                page.wait_for_timeout(min_dwell_ms)
                try:
                    ctx.close()
                except Exception:
                    pass
        except Exception:
            pass

    # Lanzar ventanas en paralelo
    for i in range(limit):
        threading.Thread(target=open_window, args=(i,), daemon=True).start()

    minis = []
    for i in range(instances):
        minis.append(
            f"<div class=\"mini ok\" data-counter-id=\"{counter_id}\"><div class=\"mini-player\" style=\"width:160px;height:90px;border:1px solid #333;border-radius:6px;overflow:hidden;display:flex;align-items:center;justify-content:center;color:#bbb;font-size:12px;\">Ventana {i+1}</div></div>"
        )

    visible_limit = 25
    visible = minis[:visible_limit]
    hidden = minis[visible_limit:]
    more_btn = ""
    hidden_block = ""
    if hidden:
        more_btn = (
            f"<button class=\"more-btn\" data-target=\"hidden-{counter_id}\" style=\"margin-top:8px;padding:6px 10px;border:1px solid #4caf50;color:#fff;background:#2e7d32;border-radius:6px;cursor:pointer;\">MÃ¡s +</button>"
        )
        hidden_block = (
            f"<div id=\"hidden-{counter_id}\" class=\"mini-grid\" style=\"display:none;flex-wrap:wrap;gap:8px;margin-top:10px;\">{''.join(hidden)}</div>"
        )

    snippet = f"""
<div class=\"inline-silent-batch\">\n  <div class=\"mini-grid\" id=\"visible-{counter_id}\" style=\"display:flex;flex-wrap:wrap;gap:8px;\">\n    {''.join(visible)}\n  </div>\n  <div class=\"mini-toolbar\" style=\"display:flex;align-items:center;gap:10px;margin-top:6px;\">\n    <span class=\"muted\">Mostrando {min(len(minis), visible_limit)} de {len(minis)}</span>\n    {more_btn}\n  </div>\n  {hidden_block}\n  <div class=\"repro-line\"><code>{url}</code> â€” Ventanas reales visibles (mÃ¡x. 25 simultÃ¡neas)</div>\n</div>\n    """
    return HTMLResponse(content=snippet)


@app.post("/run_browser_headful_grid_live", response_class=HTMLResponse, summary="CuadrÃ­cula visible con vista previa en vivo")
def run_browser_headful_grid_live(
    urls_text: str = Form(...),
    instances: int = Form(1),
    min_dwell_ms: int = Form(60000),
    gmail_login: bool | None = Form(None),
    use_saved_accounts: bool | None = Form(None),
    gmail_email: str | None = Form(None),
    gmail_password: str | None = Form(None),
):
    """Abre hasta 25 ventanas y captura screenshots periÃ³dicas hacia artifacts/ para vista previa.
    Muestra miniaturas que se actualizan cada segundo en la UI.
    """
    urls = [u.strip() for u in urls_text.splitlines() if u.strip()]
    url = urls[0] if urls else ""
    # Si es YouTube, intenta usar el embed con autoplay silencioso
    def to_embed(u: str) -> str:
        try:
            parsed = urllib.parse.urlparse(u)
            host = (parsed.netloc or '').lower()
            if 'youtube.com' in host or 'youtu.be' in host:
                vid = None
                if 'youtu.be' in host:
                    vid = parsed.path.strip('/').split('/')[0] if parsed.path else None
                else:
                    # Soportar /watch?v= y /shorts/<id>
                    path = (parsed.path or '').lower()
                    if '/shorts/' in path:
                        segs = [s for s in path.split('/') if s]
                        try:
                            i = segs.index('shorts')
                            vid = segs[i+1] if len(segs) > i+1 else None
                        except Exception:
                            vid = None
                    if not vid:
                        q = urllib.parse.parse_qs(parsed.query or '')
                        vid = (q.get('v') or [None])[0]
                if vid:
                    # Forzar modo watch si el usuario lo solicitó
                    if force_watch_mode:
                        return f"https://www.youtube.com/watch?v={vid}"
                    # Embed básico sin parámetros de autoplay/mute para evitar restricciones
                    return f"https://www.youtube.com/embed/{vid}?playsinline=1"
        except Exception:
            pass
        return u
    nav_url = to_embed(url)
    instances = max(1, int(instances or 1))
    limit = min(instances, 25)
    counter_id = uuid.uuid4().hex

    cols = 5
    win_w, win_h = 480, 270
    gap_x, gap_y = 10, 40
    refresh_ms = 1000

    def open_and_capture(idx: int, session: str, target_url: str):
        x = (idx % cols) * (win_w + gap_x)
        y = (idx // cols) * (win_h + gap_y)
        from playwright.sync_api import sync_playwright
        try:
            with sync_playwright() as p:
                ctx = _launch_persistent_ctx(
                    p,
                    args=[
                        f"--window-size={win_w},{win_h}",
                        f"--window-position={x},{y}",
                        "--autoplay-policy=no-user-gesture-required",
                    ],
                    viewport={"width": win_w, "height": win_h},
                    window_size=(win_w, win_h),
                    window_pos=(x, y),
                )
                page = ctx.new_page()
                played_ok = False
                try:
                    # Login de Gmail si fue solicitado
                    try:
                        if gmail_login:
                            acct_email = None
                            acct_pwd = None
                            if use_saved_accounts:
                                try:
                                    acc_path = os.path.join(artifacts_dir, "accounts.json")
                                    if os.path.exists(acc_path):
                                        with open(acc_path, "r", encoding="utf-8") as f:
                                            accs = json.load(f) or []
                                        if isinstance(accs, list) and len(accs) > 0:
                                            pick = accs[idx % len(accs)] if isinstance(accs[idx % len(accs)], dict) else {}
                                            acct_email = (pick.get("email") or "").strip()
                                            acct_pwd = (pick.get("password") or "").strip()
                                except Exception:
                                    pass
                            if not acct_email:
                                acct_email = (gmail_email or "").strip()
                            if not acct_pwd:
                                acct_pwd = (gmail_password or "").strip()
                            if acct_email:
                                try:
                                    page.goto("https://accounts.google.com/signin/v2/identifier?service=youtube", wait_until="domcontentloaded")
                                except Exception:
                                    try:
                                        page.goto("https://accounts.google.com/ServiceLogin?service=youtube", wait_until="domcontentloaded")
                                    except Exception:
                                        pass
                                try:
                                    # Rellenar email
                                    try:
                                        page.fill('input[name="identifier"]', acct_email, timeout=2000)
                                    except Exception:
                                        try:
                                            page.fill('input[name="identifierId"]', acct_email, timeout=2000)
                                        except Exception:
                                            page.fill('input[type="email"]', acct_email, timeout=2000)
                                    try:
                                        page.click('#identifierNext button, #identifierNext', timeout=2000)
                                    except Exception:
                                        try:
                                            page.locator('button').filter({"hasText": "Siguiente"}).first.click(timeout=1500)
                                        except Exception:
                                            pass
                                    page.wait_for_timeout(2000)
                                    # Rellenar password
                                    if acct_pwd:
                                        try:
                                            page.fill('input[type="password"]', acct_pwd, timeout=2500)
                                        except Exception:
                                            pass
                                        try:
                                            page.click('#passwordNext button, #passwordNext', timeout=2000)
                                        except Exception:
                                            try:
                                                page.locator('button').filter({"hasText": "Siguiente"}).nth(1).click(timeout=1500)
                                            except Exception:
                                                pass
                                        page.wait_for_timeout(2500)
                                except Exception:
                                    pass
                    except Exception:
                        pass
                    page.goto(target_url, wait_until="domcontentloaded")
                    # Intento de consentimiento y reproducciÃ³n
                    try:
                        texts = ['Aceptar','Acepto','Estoy de acuerdo','Continuar','Ok','Entendido','Permitir','Agree','Accept','Accept all']
                        clickable = page.locator('button, a, [role="button"]')
                        for t in texts:
                            try:
                                clickable.filter({"hasText": t}).first.click(timeout=1000)
                            except Exception:
                                pass
                    except Exception:
                        pass
                    # Si no es embed, intenta reproducir
                    try:
                        page.locator('button.ytp-large-play-button').click(timeout=3000)
                    except Exception:
                        try:
                            page.keyboard.press('k')
                        except Exception:
                            pass
                    try:
                        page.evaluate("() => { const v = document.querySelector('video'); if (v) { try { v.muted = true; } catch(e){} try { v.play(); } catch(e){} } }")
                        page.wait_for_timeout(1500)
                        played_ok = bool(page.evaluate("() => { const v = document.querySelector('video'); if (!v) return false; try { return !v.paused || v.currentTime > 1; } catch(e){ return false; } }"))
                    except Exception:
                        pass
                except Exception:
                    pass
                # Capturar screenshots periÃ³dicas
                try:
                    steps = max(1, int(min_dwell_ms // refresh_ms))
                    for _ in range(steps):
                        try:
                            out_path = os.path.join(artifacts_dir, f"{session}_{idx}.png")
                            page.screenshot(path=out_path)
                        except Exception:
                            pass
                        page.wait_for_timeout(refresh_ms)
                except Exception:
                    pass
                try:
                    ctx.close()
                except Exception:
                    pass
        except Exception:
            pass
        finally:
            # Escribir marcador de finalizaciÃ³n con estado incluso si hubo excepciones tempranas
            try:
                os.makedirs(artifacts_dir, exist_ok=True)
                done_path = os.path.join(artifacts_dir, f"{session}_{idx}.done")
                with open(done_path, 'w', encoding='utf-8') as f:
                    f.write('ok' if played_ok else 'err')
            except Exception:
                pass

    # Lanzar ventanas y captura en paralelo
    for i in range(limit):
        threading.Thread(target=open_and_capture, args=(i, counter_id, nav_url), daemon=True).start()

    # Construir miniaturas con imagen en vivo desde artifacts/
    minis = []
    for i in range(instances):
        img_src = f"/artifacts/{counter_id}_{i}.png"
        done_src = f"/artifacts/{counter_id}_{i}.done"
        minis.append(
            f'<div class="mini warn" data-counter-id="{counter_id}" data-index="{i}">' 
            f'<a href="{img_src}" target="_blank" title="ver captura">' 
            f'<img class="mini-live" data-src-base="{img_src}" src="{img_src}" style="width:160px;height:90px;border:1px solid #333;border-radius:6px;object-fit:cover;background:#0f1115;" alt="vista previa" />' 
            f'</a>' 
            f'<span class="badge">Ejecutando</span>' 
            f'</div>'
        )

    visible_limit = 25
    visible = minis[:visible_limit]
    hidden = minis[visible_limit:]
    more_btn = ""
    hidden_block = ""
    if hidden:
        more_btn = (
            f"<button class=\"more-btn\" data-target=\"hidden-{counter_id}\" style=\"margin-top:8px;padding:6px 10px;border:1px solid #4caf50;color:#fff;background:#2e7d32;border-radius:6px;cursor:pointer;\">MÃ¡s +</button>"
        )
        hidden_block = (
            f"<div id=\"hidden-{counter_id}\" class=\"mini-grid\" style=\"display:none;flex-wrap:wrap;gap:8px;margin-top:10px;\">{''.join(hidden)}</div>"
        )

    snippet = f"""
<div class=\"inline-silent-batch\">\n  <div class=\"mini-grid\" id=\"visible-{counter_id}\" style=\"display:flex;flex-wrap:wrap;gap:8px;\">\n    {''.join(visible)}\n  </div>\n  <div class=\"mini-toolbar\" style=\"display:flex;align-items:center;gap:10px;margin-top:6px;\">\n    <span class=\"muted\">Mostrando {min(len(minis), visible_limit)} de {len(minis)}</span>\n    {more_btn}\n  </div>\n  {hidden_block}\n  <div class=\"repro-line\"><code>{url}</code> â€” Vista previa en vivo desde navegador real</div>\n</div>\n    """
    return HTMLResponse(content=snippet)


@app.post("/run_browser_single_tabs_live", response_class=HTMLResponse, summary="Una sola ventana con pestaÃ±as y vista previa en vivo")
def run_browser_single_tabs_live(
    urls_text: str = Form(...),
    min_dwell_ms: int = Form(60000),
    gmail_login: bool | None = Form(None),
    use_saved_accounts: bool | None = Form(None),
    gmail_email: str | None = Form(None),
    gmail_password: str | None = Form(None),
    rotate_vpn_per_url: bool | None = Form(None),
    vpn_provider: str | None = Form(None),
    vpn_country: str | None = Form(None),
    vpn_wait_ms: int = Form(5000),
    force_watch_mode: bool | None = Form(None),
):
    """Abre un solo navegador y crea una pestaÃ±a por URL.
    Captura screenshots periÃ³dicas hacia artifacts/ para vista previa.
    """
    urls = [u.strip() for u in urls_text.splitlines() if u.strip()]
    if not urls:
        return HTMLResponse(content="<div class='err'>No se enviaron URLs.</div>")

    counter_id = uuid.uuid4().hex
    refresh_ms = 1000

    def to_embed(u: str) -> str:
        """Devuelve URL adecuada para abrir primero.
        - Si es YouTube y force_watch_mode está activo → usar watch limpio.
        - Si es YouTube y no está activo → usar embed básico (sin autoplay/mute) para evitar restricciones.
        """
        try:
            parsed = urllib.parse.urlparse(u)
            host = (parsed.netloc or '').lower()
            if 'youtube.com' in host or 'youtu.be' in host:
                vid = None
                if 'youtu.be' in host:
                    vid = parsed.path.strip('/').split('/')[0] if parsed.path else None
                else:
                    path = (parsed.path or '').lower()
                    if '/shorts/' in path:
                        segs = [s for s in path.split('/') if s]
                        try:
                            i = segs.index('shorts')
                            vid = segs[i+1] if len(segs) > i+1 else None
                        except Exception:
                            vid = None
                    if not vid:
                        q = urllib.parse.parse_qs(parsed.query or '')
                        vid = (q.get('v') or [None])[0]
                if vid:
                    if force_watch_mode:
                        return f"https://www.youtube.com/watch?v={vid}"
                    # Embed básico: sin autoplay/mute para minimizar bloqueos
                    return f"https://www.youtube.com/embed/{vid}?playsinline=1"
        except Exception:
            pass
        return u

    # Hilo supervisor: mantiene un solo navegador y abre pestaÃ±as
    def run_session(session: str, nav_urls: list[str]):
        from playwright.sync_api import sync_playwright
        try:
            with sync_playwright() as p:
                ctx = _launch_persistent_ctx(
                    p,
                    args=[
                        "--autoplay-policy=no-user-gesture-required",
                        "--mute-audio",
                    ],
                )

                # Login de Gmail (una sola vez para la sesiÃ³n)
                try:
                    if gmail_login:
                        acct_email = None
                        acct_pwd = None
                        if use_saved_accounts:
                            try:
                                acc_path = os.path.join(artifacts_dir, "accounts.json")
                                if os.path.exists(acc_path):
                                    with open(acc_path, "r", encoding="utf-8") as f:
                                        accs = json.load(f) or []
                                    if isinstance(accs, list) and len(accs) > 0:
                                        pick = accs[0] if isinstance(accs[0], dict) else {}
                                        acct_email = (pick.get("email") or "").strip()
                                        acct_pwd = (pick.get("password") or "").strip()
                            except Exception:
                                pass
                        if not acct_email:
                            acct_email = (gmail_email or "").strip()
                        if not acct_pwd:
                            acct_pwd = (gmail_password or "").strip()
                        if acct_email:
                            lp = ctx.new_page()
                            try:
                                lp.goto("https://accounts.google.com/signin/v2/identifier?service=youtube", wait_until="domcontentloaded")
                            except Exception:
                                try:
                                    lp.goto("https://accounts.google.com/ServiceLogin?service=youtube", wait_until="domcontentloaded")
                                except Exception:
                                    pass
                            try:
                                try:
                                    lp.fill('input[name="identifier"]', acct_email, timeout=2500)
                                except Exception:
                                    try:
                                        lp.fill('input[name="identifierId"]', acct_email, timeout=2500)
                                    except Exception:
                                        lp.fill('input[type="email"]', acct_email, timeout=2500)
                                try:
                                    lp.click('#identifierNext button, #identifierNext', timeout=2500)
                                except Exception:
                                    try:
                                        lp.locator('button').filter({"hasText": "Siguiente"}).first.click(timeout=2000)
                                    except Exception:
                                        pass
                                lp.wait_for_timeout(2500)
                                if acct_pwd:
                                    try:
                                        lp.fill('input[type="password"]', acct_pwd, timeout=3000)
                                    except Exception:
                                        pass
                                    try:
                                        lp.click('#passwordNext button, #passwordNext', timeout=2500)
                                    except Exception:
                                        try:
                                            lp.locator('button').filter({"hasText": "Siguiente"}).nth(1).click(timeout=2000)
                                        except Exception:
                                            pass
                                    lp.wait_for_timeout(3000)
                            except Exception:
                                pass
                            try:
                                lp.close()
                            except Exception:
                                pass
                except Exception:
                    pass

                # Control de VPN (rotaciÃ³n por URL si se solicita)
                vpn = None
                try:
                    if rotate_vpn_per_url:
                        vpn = get_vpn_controller(vpn_provider, vpn_country, None, vpn_wait_ms)
                except Exception:
                    vpn = None

                # Abrir pestaÃ±as y capturar miniaturas
                def capture_loop(pg, out_path: str, total_ms: int):
                    try:
                        steps = max(1, int(total_ms // refresh_ms))
                        for _ in range(steps):
                            try:
                                pg.screenshot(path=out_path)
                            except Exception:
                                pass
                            pg.wait_for_timeout(refresh_ms)
                    except Exception:
                        pass

                for i, raw in enumerate(nav_urls):
                    try:
                        target = to_embed(raw)
                        # Rotar VPN antes de abrir cada pestaÃ±a (afecta sistema completo)
                        try:
                            if vpn:
                                vpn.disconnect()
                                vpn.connect_next()
                        except Exception:
                            pass
                        page = ctx.new_page()
                        played_ok = False
                        try:
                            page.goto(target, wait_until="domcontentloaded")
                        except Exception:
                            pass
                        # Consentimientos comunes y reproducciÃ³n/mute
                        try:
                            texts = ['Aceptar','Acepto','Estoy de acuerdo','Continuar','Ok','Entendido','Permitir','Agree','Accept','Accept all']
                            clickable = page.locator('button, a, [role="button"]')
                            for t in texts:
                                try:
                                    clickable.filter({"hasText": t}).first.click(timeout=1000)
                                except Exception:
                                    pass
                        except Exception:
                            pass
                        try:
                            # Intento 1: reproducir directamente el <video> si existe
                            page.evaluate("() => { const v = document.querySelector('video'); if (v) { try { v.muted = true; } catch(e){} try { v.play(); } catch(e){} } }")
                            page.wait_for_timeout(1200)
                            played_ok = bool(page.evaluate("() => { const v = document.querySelector('video'); if (!v) return false; try { return !v.paused || v.currentTime > 1; } catch(e){ return false; } }"))
                        except Exception:
                            played_ok = False

                        # Intento 2: clic en botón de play del reproductor
                        if not played_ok:
                            try:
                                page.locator('.ytp-large-play-button, .ytp-play-button').first.click(timeout=1500)
                                page.wait_for_timeout(1000)
                                played_ok = bool(page.evaluate("() => { const v = document.querySelector('video'); if (!v) return false; try { return !v.paused || v.currentTime > 1; } catch(e){ return false; } }"))
                            except Exception:
                                pass

                        # Intento 2.2: interactuar dentro de iframes de YouTube y extraer ID del video
                        if not played_ok:
                            try:
                                # 2.2.a Intentar clic de play dentro de iframes del reproductor
                                try:
                                    for fr in page.frames:
                                        try:
                                            u = (fr.url or '')
                                            if ('youtube.com' in u) or ('youtube-nocookie.com' in u):
                                                try:
                                                    fr.locator('.ytp-large-play-button, .ytp-play-button').first.click(timeout=1200)
                                                    page.wait_for_timeout(800)
                                                except Exception:
                                                    pass
                                        except Exception:
                                            pass
                                    # Verificar si ya reproduce
                                    played_ok = bool(page.evaluate("() => { const v = document.querySelector('video'); if (!v) return false; try { return !v.paused || v.currentTime > 1; } catch(e){ return false; } }"))
                                except Exception:
                                    pass

                                # 2.2.b Extraer ID del video desde el src del iframe y navegar a watch
                                if not played_ok:
                                    href_watch = None
                                    try:
                                        for fr in page.frames:
                                            try:
                                                u = (fr.url or '')
                                                if ('/embed/' in u) and (('youtube.com' in u) or ('youtube-nocookie.com' in u)):
                                                    # Ejemplos: https://www.youtube.com/embed/VIDEOID?...
                                                    try:
                                                        import re as _re
                                                        m = _re.search(r"/embed/([A-Za-z0-9_-]{6,})", u)
                                                        vid = m.group(1) if m else None
                                                    except Exception:
                                                        vid = None
                                                    if vid:
                                                        href_watch = f"https://www.youtube.com/watch?v={vid}"
                                                        break
                                            except Exception:
                                                pass
                                    except Exception:
                                        href_watch = None
                                    if href_watch:
                                        try:
                                            page.goto(href_watch, wait_until="domcontentloaded")
                                            page.wait_for_timeout(800)
                                        except Exception:
                                            pass
                                        try:
                                            page.locator('.ytp-large-play-button, .ytp-play-button').first.click(timeout=1500)
                                        except Exception:
                                            pass
                                        page.evaluate("() => { const v = document.querySelector('video'); if (v) { try { v.muted = true; } catch(e){} try { v.play(); } catch(e){} } }")
                                        page.wait_for_timeout(1200)
                                        try:
                                            played_ok = bool(page.evaluate("() => { const v = document.querySelector('video'); if (!v) return false; try { return !v.paused || v.currentTime > 1; } catch(e){ return false; } }"))
                                        except Exception:
                                            played_ok = False
                            except Exception:
                                pass

                        # Intento 2.5: detectar overlay "Mirar en YouTube" y abrir href directamente
                        if not played_ok:
                            try:
                                href = page.evaluate(
                                    "() => {\n"
                                    "  const texts = ['Mirar el video en YouTube','Ver en YouTube','Watch on YouTube'];\n"
                                    "  const links = Array.from(document.querySelectorAll('a'));\n"
                                    "  for (const a of links) {\n"
                                    "    const t = (a.textContent || '').trim().toLowerCase();\n"
                                    "    if (texts.some(x => t.includes(x.toLowerCase()))) {\n"
                                    "      if (a.href) return a.href;\n"
                                    "    }\n"
                                    "  }\n"
                                    "  for (const a of links) {\n"
                                    "    const h = a.href || '';\n"
                                    "    if (h.includes('youtube.com/watch') || h.includes('youtu.be/')) return h;\n"
                                    "  }\n"
                                    "  return null;\n"
                                    "}"
                                )
                                if href:
                                    try:
                                        page.goto(href, wait_until="domcontentloaded")
                                    except Exception:
                                        pass
                                    page.wait_for_timeout(800)
                                    try:
                                        page.locator('.ytp-large-play-button, .ytp-play-button').first.click(timeout=1500)
                                    except Exception:
                                        pass
                                    page.evaluate("() => { const v = document.querySelector('video'); if (v) { try { v.muted = true; } catch(e){} try { v.play(); } catch(e){} } }")
                                    page.wait_for_timeout(1200)
                                    played_ok = bool(page.evaluate("() => { const v = document.querySelector('video'); if (!v) return false; try { return !v.paused || v.currentTime > 1; } catch(e){ return false; } }"))
                            except Exception:
                                pass

                        # Intento 2.8: si seguimos en embed y no reproduce, convertir a watch por ID
                        if not played_ok:
                            try:
                                import re as _re
                                curr = page.url or ""
                                vid2 = None
                                if ("/embed/" in curr) and ("youtube" in curr):
                                    m2 = _re.search(r"/embed/([A-Za-z0-9_-]{6,})", curr)
                                    vid2 = m2.group(1) if m2 else None
                                if not vid2:
                                    # extraer del RAW por si el actual no es embed estándar
                                    try:
                                        parsed = urllib.parse.urlparse(raw)
                                        host = (parsed.netloc or '').lower()
                                        if 'youtu.be' in host:
                                            vid2 = parsed.path.strip('/').split('/')[0] if parsed.path else None
                                        elif 'youtube.com' in host:
                                            path = (parsed.path or '').lower()
                                            if '/shorts/' in path:
                                                segs = [s for s in path.split('/') if s]
                                                try:
                                                    i = segs.index('shorts'); vid2 = segs[i+1] if len(segs) > i+1 else None
                                                except Exception:
                                                    vid2 = None
                                            if not vid2:
                                                q = urllib.parse.parse_qs(parsed.query or '')
                                                vid2 = (q.get('v') or [None])[0]
                                    except Exception:
                                        vid2 = None
                                if vid2:
                                    try:
                                        page.goto(f"https://www.youtube.com/watch?v={vid2}", wait_until="domcontentloaded")
                                    except Exception:
                                        pass
                                    page.wait_for_timeout(800)
                                    try:
                                        page.locator('.ytp-large-play-button, .ytp-play-button').first.click(timeout=1500)
                                    except Exception:
                                        pass
                                    page.evaluate("() => { const v = document.querySelector('video'); if (v) { try { v.muted = true; } catch(e){} try { v.play(); } catch(e){} } }")
                                    page.wait_for_timeout(1200)
                                    try:
                                        played_ok = bool(page.evaluate("() => { const v = document.querySelector('video'); if (!v) return false; try { return !v.paused || v.currentTime > 1; } catch(e){ return false; } }"))
                                    except Exception:
                                        played_ok = False
                            except Exception:
                                pass

                        # Si el embed falla, intentar la página original (watch)
                        if not played_ok:
                            try:
                                page.goto(raw, wait_until="domcontentloaded")
                                # Aceptar posibles diálogos de consentimiento
                                try:
                                    texts = ['Aceptar','Acepto','Estoy de acuerdo','Continuar','Ok','Entendido','Permitir','Agree','I agree','Accept','Accept all']
                                    btns = page.locator('button, a, [role="button"]')
                                    for t in texts:
                                        try:
                                            btns.filter({"hasText": t}).first.click(timeout=800)
                                        except Exception:
                                            pass
                                except Exception:
                                    pass
                                # Si aparece el mensaje de error 153, intentar abrir el enlace para ver en YouTube
                                try:
                                    err153 = bool(page.evaluate("() => { const txt = document.body ? (document.body.innerText||'') : ''; return txt.includes('153'); }"))
                                except Exception:
                                    err153 = False
                                if err153:
                                    try:
                                        href = page.evaluate(
                                            "() => {\n"
                                            "  const texts = ['Mirar el video en YouTube','Ver en YouTube','Watch on YouTube'];\n"
                                            "  const links = Array.from(document.querySelectorAll('a'));\n"
                                            "  for (const a of links) {\n"
                                            "    const t = (a.textContent || '').trim().toLowerCase();\n"
                                            "    if (texts.some(x => t.includes(x.toLowerCase()))) {\n"
                                            "      if (a.href) return a.href;\n"
                                            "    }\n"
                                            "  }\n"
                                            "  for (const a of links) {\n"
                                            "    const h = a.href || '';\n"
                                            "    if (h.includes('youtube.com/watch') || h.includes('youtu.be/')) return h;\n"
                                            "  }\n"
                                            "  return null;\n"
                                            "}"
                                        )
                                        if href:
                                            try:
                                                page.goto(href, wait_until="domcontentloaded")
                                                page.wait_for_timeout(800)
                                            except Exception:
                                                pass
                                        else:
                                            # Si no hay href en la página, convertir a watch por ID
                                            try:
                                                import re as _re
                                                curr = page.url or ""
                                                m2 = _re.search(r"[?&]v=([A-Za-z0-9_-]{6,})", curr)
                                                vid2 = m2.group(1) if m2 else None
                                                if not vid2 and "/embed/" in curr:
                                                    m3 = _re.search(r"/embed/([A-Za-z0-9_-]{6,})", curr)
                                                    vid2 = m3.group(1) if m3 else None
                                                if vid2:
                                                    page.goto(f"https://www.youtube.com/watch?v={vid2}", wait_until="domcontentloaded")
                                                    page.wait_for_timeout(800)
                                            except Exception:
                                                pass
                                    except Exception:
                                        pass
                                # Reproducir con clic si es necesario
                                try:
                                    page.locator('.ytp-large-play-button, .ytp-play-button').first.click(timeout=1500)
                                except Exception:
                                    pass
                                page.evaluate("() => { const v = document.querySelector('video'); if (v) { try { v.muted = true; } catch(e){} try { v.play(); } catch(e){} } }")
                                page.wait_for_timeout(1200)
                                played_ok = bool(page.evaluate("() => { const v = document.querySelector('video'); if (!v) return false; try { return !v.paused || v.currentTime > 1; } catch(e){ return false; } }"))
                            except Exception:
                                pass

                        # Intento final: versión móvil de YouTube
                        if not played_ok:
                            try:
                                vid = None
                                try:
                                    parsed = urllib.parse.urlparse(raw)
                                    host = (parsed.netloc or '').lower()
                                    if 'youtu.be' in host:
                                        vid = parsed.path.strip('/').split('/')[0] if parsed.path else None
                                    elif 'youtube.com' in host:
                                        path = (parsed.path or '').lower()
                                        if '/shorts/' in path:
                                            segs = [s for s in path.split('/') if s]
                                            try:
                                                i = segs.index('shorts'); vid = segs[i+1] if len(segs) > i+1 else None
                                            except Exception:
                                                vid = None
                                        if not vid:
                                            q = urllib.parse.parse_qs(parsed.query or '')
                                            vid = (q.get('v') or [None])[0]
                                except Exception:
                                    vid = None
                                if vid:
                                    page.goto(f"https://m.youtube.com/watch?v={vid}", wait_until="domcontentloaded")
                                    try:
                                        page.locator('.ytp-large-play-button, .ytp-play-button').first.click(timeout=1500)
                                    except Exception:
                                        pass
                                    page.evaluate("() => { const v = document.querySelector('video'); if (v) { try { v.muted = true; } catch(e){} try { v.play(); } catch(e){} } }")
                                    page.wait_for_timeout(1200)
                                    played_ok = bool(page.evaluate("() => { const v = document.querySelector('video'); if (!v) return false; try { return !v.paused || v.currentTime > 1; } catch(e){ return false; } }"))
                            except Exception:
                                pass

                        # Lanzar captura en segundo plano para esta pestaÃ±a
                        out_path = os.path.join(artifacts_dir, f"{session}_{i}.png")
                        threading.Thread(target=capture_loop, args=(page, out_path, min_dwell_ms), daemon=True).start()
                        # Marcar estado segÃºn reproducciÃ³n detectada
                        try:
                            done_path = os.path.join(artifacts_dir, f"{session}_{i}.done")
                            with open(done_path, 'w', encoding='utf-8') as f:
                                f.write('ok' if played_ok else 'err')
                        except Exception:
                            pass
                    except Exception:
                        # Marcar error si algo fallÃ³ al abrir
                        try:
                            done_path = os.path.join(artifacts_dir, f"{session}_{i}.done")
                            with open(done_path, 'w', encoding='utf-8') as f:
                                f.write('err')
                        except Exception:
                            pass

                # Mantener la sesiÃ³n viva durante el dwell y luego cerrar
                try:
                    time.sleep(max(1, int(min_dwell_ms / 1000)))
                except Exception:
                    pass
                try:
                    ctx.close()
                except Exception:
                    pass
        except Exception:
            pass

    threading.Thread(target=run_session, args=(counter_id, urls), daemon=True).start()

    # Construir miniaturas con imagen en vivo desde artifacts/
    minis = []
    for i, u in enumerate(urls):
        img_src = f"/artifacts/{counter_id}_{i}.png"
        minis.append(
            f'<div class="mini warn" data-counter-id="{counter_id}" data-index="{i}">' \
            f'<a href="{img_src}" target="_blank" title="ver captura">' \
            f'<img class="mini-live" data-src-base="{img_src}" src="{img_src}" style="width:160px;height:90px;border:1px solid #333;border-radius:6px;object-fit:cover;background:#0f1115;" alt="vista previa" />' \
            f'</a>' \
            f'<span class="badge">Ejecutando</span>' \
            f'</div>'
        )

    visible_limit = 25
    visible = minis[:visible_limit]
    hidden = minis[visible_limit:]
    more_btn = ""
    hidden_block = ""
    if hidden:
        more_btn = (
            f"<button class=\"more-btn\" data-target=\"hidden-{counter_id}\" style=\"margin-top:8px;padding:6px 10px;border:1px solid #4caf50;color:#fff;background:#2e7d32;border-radius:6px;cursor:pointer;\">MÃ¡s +</button>"
        )
        hidden_block = (
            f"<div id=\"hidden-{counter_id}\" class=\"mini-grid\" style=\"display:none;flex-wrap:wrap;gap:8px;margin-top:10px;\">{''.join(hidden)}</div>"
        )

    snippet = f"""
<div class=\"inline-silent-batch\">\n  <div class=\"mini-grid\" id=\"visible-{counter_id}\" style=\"display:flex;flex-wrap:wrap;gap:8px;\">\n    {''.join(visible)}\n  </div>\n  <div class=\"mini-toolbar\" style=\"display:flex;align-items:center;gap:10px;margin-top:6px;\">\n    <span class=\"muted\">Mostrando {min(len(minis), visible_limit)} de {len(minis)}</span>\n    {more_btn}\n  </div>\n  {hidden_block}\n  <div class=\"repro-line\"><code>{len(urls)} pestaÃ±as</code> — Una sola ventana, audio silenciado y vista previa</div>\n</div>\n    """
    return HTMLResponse(content=snippet)


# PÃ¡gina simple para pegar URLs y generar JSON
@app.get("/ui", response_class=HTMLResponse, summary="Interfaz web sencilla")
def ui():
    html = """
<!doctype html>
<html lang=\"es\">
<head>
  <meta charset=\"utf-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
  <title>Granja de Bots (QA) â€” UI sencilla</title>
  <style>
    body { font-family: system-ui, Arial, sans-serif; margin: 20px; background: #ffffff; color: #111111; }
    textarea, input, button { font-size: 14px; }
    .row { margin-bottom: 10px; }
    textarea { width: 100%; height: 160px; background: #ffffff; color: #111111; border: 1px solid #cccccc; }
    input { background: #ffffff; color: #111111; border: 1px solid #cccccc; padding: 6px; }
    button { background: #0969da; color: #ffffff; border: none; padding: 8px 12px; border-radius: 6px; cursor: pointer; }
    button:disabled { background: #9bbcf0; cursor: not-allowed; }
    #out { white-space: pre-wrap; background: #f7f7f7; color: #111111; padding: 10px; border: 1px solid #ddd; }
    /* Estilos para resultados inline en la home */
    .inline-result { margin-top: 12px; }
    .inline-result .results { list-style: none; padding-left: 0; }
    .inline-result .results li { margin: 6px 0; }
    .inline-result { color: #ffffff; }
    .inline-result code { background: rgba(255,255,255,0.12); color: #ffffff; }
    .inline-result .ok { color: #2ecc71; }
    .inline-result .err { color: #ff4d4f; }
    .inline-result .warn { color: #f0ad4e; }

    /* Mini bloque para resultado silencioso con botÃ³n Ver */
    .inline-silent { margin-top: 12px; color: #ffffff; }
    .inline-silent .mini-bar { display: flex; align-items: center; gap: 10px; }
    .inline-silent .view-btn { padding: 6px 10px; border-radius: 6px; border: none; cursor: pointer; font-weight: 600; }
    .inline-silent .view-btn.ok { background: #2ecc71; color: #0b2f14; }
    .inline-silent .view-btn.err { background: #ff4d4f; color: #33060a; }
    .inline-silent .view-btn.warn { background: #f0ad4e; color: #3a2404; }
    .inline-silent .hidden { display: none; }
    .inline-silent .results { list-style: none; padding-left: 0; }
    .inline-silent .results li { margin: 6px 0; }
    /* Miniatura sÃºper compacta para evitar saturar la pÃ¡gina */
    .inline-silent .mini-thumb { width: 140px; height: 79px; background: #000; border: 1px solid #333; border-radius: 6px; overflow: hidden; display: inline-block; }
    .inline-silent .mini-thumb img { width: 100%; height: 100%; object-fit: cover; display: block; }
    /* Miniaturas en vivo (grid compacta) */
    .live-minis { display: grid; grid-template-columns: repeat(auto-fill, minmax(96px, 1fr)); gap: 6px; align-items: start; }
    .live-minis .mini { position: relative; width: 96px; height: 54px; background: #000; border: 1px solid #333; border-radius: 6px; overflow: hidden; }
    .live-minis .mini iframe { width: 100%; height: 100%; border: 0; }
    .live-minis .mini .badge { position: absolute; bottom: 2px; right: 2px; font-size: 10px; padding: 2px 4px; border-radius: 4px; background: rgba(255,255,255,0.85); color: #000; }
    .live-minis .mini.ok .badge { background: #2ecc71; color: #0b2f14; }
    .live-minis .mini.err .badge { background: #ff4d4f; color: #33060a; }
    .live-minis .mini.warn .badge { background: #f0ad4e; color: #3a2404; }
    .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
    .card { border: 1px solid #ddd; padding: 12px; border-radius: 6px; background: #ffffff; }
    .muted { color: #555; font-size: 12px; }
    .player { position: relative; width: 100%; padding-top: 56.25%; border: 1px solid #ddd; border-radius: 6px; overflow: hidden; background: #000; }
    .player iframe { position: absolute; top: 0; left: 0; width: 100%; height: 100%; border: 0; }

    textarea::placeholder { color: #777; }
    a { color: #0969da; }

    @media (prefers-color-scheme: dark) {
      body { background: #121212; color: #eeeeee; }
      .card { border-color: #333; background: #1e1e1e; }
      #out { background: #1a1a1a; color: #eeeeee; border-color: #333; }
      textarea, input { background: #1e1e1e; color: #eeeeee; border-color: #333; }
      textarea::placeholder { color: #aaaaaa; }
      button { background: #2d7bf5; color: #ffffff; }
      .inline-result { color: #ffffff; }
      .inline-result code { background: rgba(255,255,255,0.12); color: #ffffff; }
      .inline-silent { color: #ffffff; }
    }
  </style>
</head>
<body>
  <h1>Granja de Bots (QA) â€” UI sencilla</h1>
  <nav style="margin-bottom:12px; display:flex; gap:10px; align-items:center;">
    <a href="/ui" style="text-decoration:none; padding:6px 10px; border:1px solid #ddd; border-radius:6px;">Home</a>
    <a href="/ui_accounts" style="text-decoration:none; padding:6px 10px; border:1px solid #4caf50; color:#fff; background:#2e7d32; border-radius:6px;">Cuentas</a>
  </nav>
  <p class=\"muted\">Pega muchas URLs (una por lÃ­nea), elige parÃ¡metros y ejecuta localmente o encola (requiere Redis).</p>

  <div class=\"card\">
    <div class=\"row\">
      <label>URLs (una por lÃ­nea):</label><br />
      <textarea id=\"urls\" placeholder=\"https://example.com\nhttps://example.com/docs\"></textarea>
    </div>

  <div class=\"grid\">
      <div>
        <label>Repeticiones por URL:</label><br />
        <input id=\"reps\" type=\"number\" min=\"1\" value=\"1\" />
      </div>
      <div>
        <label>Capturas de pantalla:</label><br />
        <input id=\"screenshot\" type=\"checkbox\" checked />
      </div>
      <div>
        <label>Respetar robots.txt:</label><br />
        <input id=\"respect\" type=\"checkbox\" />
      </div>
      <div>
        <label>Mostrar navegador (no headless):</label><br />
        <input id=\"show\" type=\"checkbox\" />
      </div>
      <div>
        <label>Forzar modo incógnito (Chrome):</label><br />
        <input id=\"force_incognito\" type=\"checkbox\" />
      </div>
      <div>
        <label>Permanencia mÃ­nima (ms):</label><br />
        <input id=\"min\" type=\"number\" min=\"0\" value=\"30000\" />
      </div>
      <div>
        <label>Permanencia mÃ¡xima (ms):</label><br />
        <input id=\"maxd\" type=\"number\" min=\"0\" value=\"30000\" />
      </div>
      <div>
        <label>Intentar reproducir video:</label><br />
        <input id=\"play\" type=\"checkbox\" checked />
      </div>
      <div>
        <label>Forzar modo watch (evitar embed restringido):</label><br />
        <input id=\"force_watch_mode\" type=\"checkbox\" />
      </div>
      <div>
        <label>Ingresar a Gmail antes de reproducir:</label><br />
        <input id=\"login_gmail\" type=\"checkbox\" />
      </div>
      <div>
        <label>Usar cuentas guardadas (por instancia):</label><br />
        <input id=\"use_saved_accounts\" type=\"checkbox\" />
      </div>
      <div>
        <label>Email de Gmail:</label><br />
        <input id=\"gmail_email\" type=\"text\" placeholder=\"usuario@gmail.com\" />
      </div>
      <div>
        <label>ContraseÃ±a de Gmail:</label><br />
        <input id=\"gmail_password\" type=\"password\" placeholder=\"********\" />
      </div>
      <div>
        <label>Rotar VPN por URL:</label><br />
        <input id=\"rotate_vpn\" type=\"checkbox\" checked />
      </div>
      <div>
        <label>Proveedor VPN:</label><br />
        <select id=\"vpn_provider\">\n          <option value=\"nordvpn\" selected>NordVPN</option>\n        </select>
      </div>
      <div>
        <label>PaÃ­s VPN:</label><br />
        <input id=\"vpn_country\" type=\"text\" placeholder=\"Argentina\" value=\"Argentina\" />
      </div>
      <div>
        <label>Espera tras conectar VPN (ms):</label><br />
        <input id=\"vpn_wait\" type=\"number\" min=\"1000\" value=\"5000\" />
      </div>
      <div>
        <label>Instancias (1â€“100):</label><br />
        <input id=\"instances\" type=\"number\" min=\"1\" max=\"100\" value=\"4\" />
      </div>
  </div>

    

  <div class=\"row\">
      <button id=\"run\">Ejecutar local (sin cola)</button>
      <button id=\"run_silent\">Ejecutar local (silencioso)</button>
  </div>
  </div>

  <h3>Salida</h3>
  <div id=\"out\">
    <div id=\"live_bar\" class=\"row\" style=\"display:flex; justify-content:flex-end; gap:8px;\">
      <button id=\"clear_live\" title=\"Limpiar miniaturas\">Limpiar miniaturas</button>
    </div>
    <div id=\"live_minis\" class=\"live-minis\"></div>
    <div id=\"silent_result\">Esperando acciÃ³nâ€¦</div>
  </div>

  <h3>Reproductores internos</h3>
  <details id=\"players_section\" open>
    <summary id=\"players_summary\">Se reprodujo 0 veces</summary>
    <div id=\"players_grid\" class=\"grid\"></div>
  </details>

  <script>
    // La lógica de eventos se gestiona en /static/ui.js
  </script>
  <script src="/static/ui.js"></script>
</body>
</html>
    """
    return HTMLResponse(content=html)


@app.get("/ui_accounts", response_class=HTMLResponse)
def ui_accounts():
    try:
        acc_path = os.path.join(artifacts_dir, "accounts.json")
        if os.path.exists(acc_path):
            with open(acc_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        else:
            data = []
    except Exception:
        data = []
    prev = "\n".join([f"{(a.get('email') or '').strip()}:{(a.get('password') or '').strip()}" for a in data if isinstance(a, dict)])
    html = f"""
<!doctype html>
<html lang=\"es\">
<head>
  <meta charset=\"utf-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
  <title>Cuentas — gestión manual</title>
  <style>
    body {{ font-family: system-ui, Arial, sans-serif; margin: 20px; background:#121212; color:#ffffff; }}
    .card {{ border:1px solid #333; padding:12px; border-radius:6px; background:#1e1e1e; color:#ffffff; }}
    textarea, input, button {{ font-size:14px; }}
    textarea {{ width:100%; height:220px; background:#1e1e1e; color:#ffffff; border:1px solid #333; }}
    button {{ background:#2d7bf5; color:#ffffff; border:none; padding:8px 12px; border-radius:6px; cursor:pointer; }}
    .muted {{ color:#bbbbbb; font-size:12px; }}
  </style>
</head>
<body>
  <nav style=\"margin-bottom:12px; display:flex; gap:10px; align-items:center;\"> 
    <a href=\"/ui\" style=\"text-decoration:none; padding:6px 10px; border:1px solid #333; color:#ffffff; border-radius:6px;\">Home</a>
    <strong style=\"color:#ffffff;\">Cuentas</strong>
  </nav>
  <h1>Cuentas — emails para reproducción</h1>
  <p class=\"muted\">Pega uno por línea. Formatos aceptados: <code>email</code> o <code>email:password</code>.</p>
  <div class=\"card\">
    <form method=\"POST\" action=\"/save_accounts_form\"> 
      <textarea name=\"accounts_text\" placeholder=\"usuario@gmail.com\nusuario2@gmail.com:password\">{prev}</textarea>
      <div style=\"margin-top:10px;\">
        <button type=\"submit\">Guardar</button>
      </div>
    </form>
  </div>
</body>
</html>
    """
    return HTMLResponse(content=html)

@app.get("/accounts", response_class=JSONResponse)
def get_accounts():
    try:
        acc_path = os.path.join(artifacts_dir, "accounts.json")
        if os.path.exists(acc_path):
            with open(acc_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        else:
            data = []
    except Exception:
        data = []
    return JSONResponse(content=data)

@app.post("/save_accounts_form", response_class=HTMLResponse)
def save_accounts_form(accounts_text: str = Form("")):
    lines = [l.strip() for l in (accounts_text or "").splitlines() if l.strip()]
    out = []
    for l in lines:
        try:
            if ":" in l:
                email, pwd = l.split(":", 1)
                out.append({"email": email.strip(), "password": pwd.strip()})
            else:
                out.append({"email": l.strip(), "password": ""})
        except Exception:
            pass
    try:
        os.makedirs(artifacts_dir, exist_ok=True)
        acc_path = os.path.join(artifacts_dir, "accounts.json")
        with open(acc_path, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
        msg = "Guardado correcto."
    except Exception as e:
        msg = f"Error guardando: {e}"
    html = f"""
    <div class=\"inline-result\">{msg} — <a href=\"/ui_accounts\">volver</a></div>
    """
    return HTMLResponse(content=html)