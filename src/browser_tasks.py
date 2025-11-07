from typing import List, Optional, Dict
import os
import time
import random
import subprocess

from playwright.sync_api import sync_playwright
from .config import artifacts_dir, default_user_agent
from .proxies import ProxyRotator
from .robots_util import is_allowed_by_robots
from .vpn import get_vpn_controller


def ensure_artifacts_dir():
    os.makedirs(artifacts_dir, exist_ok=True)


def find_chrome_executable() -> Optional[str]:
    """Best-effort detection of system Chrome on Windows, with manual override support.

    Checks common installation paths and allows overriding via environment variables
    `CHROME_EXE_PATH` or `CHROME_PATH`. If a directory is provided, attempts to
    locate `chrome.exe` in typical subpaths.
    """
    # 0) Manual override via environment variable
    override = os.environ.get("CHROME_EXE_PATH") or os.environ.get("CHROME_PATH")
    if override:
        try:
            # If points directly to an .exe
            if override.lower().endswith(".exe") and os.path.exists(override):
                return override
            # If points to a directory, try common subpaths
            for sub in [
                "chrome.exe",
                os.path.join("Application", "chrome.exe"),
            ]:
                candidate = os.path.join(override, sub)
                if os.path.exists(candidate):
                    return candidate
        except Exception:
            pass

    # 1) Standard locations
    candidates = [
        os.path.join(os.environ.get("PROGRAMFILES", "C:\\Program Files"), "Google", "Chrome", "Application", "chrome.exe"),
        os.path.join(os.environ.get("PROGRAMFILES(X86)", "C:\\Program Files (x86)"), "Google", "Chrome", "Application", "chrome.exe"),
        os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~\\AppData\\Local")), "Google", "Chrome", "Application", "chrome.exe"),
        # Some installs may place chrome.exe directly under the Chrome folder
        os.path.join(os.environ.get("PROGRAMFILES", "C:\\Program Files"), "Google", "Chrome", "chrome.exe"),
        os.path.join(os.environ.get("PROGRAMFILES(X86)", "C:\\Program Files (x86)"), "Google", "Chrome", "chrome.exe"),
    ]
    for p in candidates:
        try:
            if os.path.exists(p):
                return p
        except Exception:
            pass

    # 2) Shallow search within Chrome folders (helps when user gives base path)
    bases = [
        os.path.join(os.environ.get("PROGRAMFILES", "C:\\Program Files"), "Google", "Chrome"),
        os.path.join(os.environ.get("PROGRAMFILES(X86)", "C:\\Program Files (x86)"), "Google", "Chrome"),
        os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~\\AppData\\Local")), "Google", "Chrome"),
    ]
    for base in bases:
        try:
            for sub in ["Application", ""]:
                candidate = os.path.join(base, sub, "chrome.exe") if sub else os.path.join(base, "chrome.exe")
                if os.path.exists(candidate):
                    return candidate
        except Exception:
            pass

    return None


def visit_links_with_rotation(
    urls: List[str],
    max_pages_per_proxy: int = 5,
    screenshot: bool = True,
    user_agent: Optional[str] = None,
    headless: bool = True,
    force_incognito: bool = False,
    respect_robots: bool = False,
    min_dwell_ms: int = 3000,
    max_dwell_ms: int = 15000,
    actions: Optional[List[Dict]] = None,
    rotate_android_profiles: bool = False,
    rotate_vpn_per_url: bool = False,
    vpn_provider: Optional[str] = None,
    vpn_country: Optional[str] = None,
    vpn_servers: Optional[List[str]] = None,
    vpn_wait_ms: int = 5000,
) -> Dict:
    """
    Visita una lista de URLs con Playwright, rotando proxies tras N páginas.
    Opcionalmente respeta robots.txt. Permite acciones simples (click, type, wait_for, js, play_video).
    Devuelve resultados por URL.
    """
    ua = user_agent or default_user_agent
    ensure_artifacts_dir()

    rotator = ProxyRotator()
    current_proxy = rotator.next()
    pages_on_current_proxy = 0
    results = []

    # Conjunto de perfiles Android para simular dispositivos diferentes
    android_profiles = [
        {
            "name": "Pixel 5 (Android 11)",
            "user_agent": "Mozilla/5.0 (Linux; Android 11; Pixel 5) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Mobile Safari/537.36",
            "viewport": {"width": 393, "height": 851},
            "is_mobile": True,
            "has_touch": True,
            "device_scale_factor": 3,
            "locale": "es-ES",
            "timezone_id": "America/Buenos_Aires",
        },
        {
            "name": "Galaxy S21 (Android 13)",
            "user_agent": "Mozilla/5.0 (Linux; Android 13; SM-G991B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Mobile Safari/537.36",
            "viewport": {"width": 412, "height": 915},
            "is_mobile": True,
            "has_touch": True,
            "device_scale_factor": 3,
            "locale": "es-ES",
            "timezone_id": "America/Buenos_Aires",
        },
        {
            "name": "Xiaomi Mi 11 (Android 12)",
            "user_agent": "Mozilla/5.0 (Linux; Android 12; M2011K2G) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Mobile Safari/537.36",
            "viewport": {"width": 414, "height": 896},
            "is_mobile": True,
            "has_touch": True,
            "device_scale_factor": 3,
            "locale": "es-ES",
            "timezone_id": "America/Buenos_Aires",
        },
        {
            "name": "Moto G Power (Android 12)",
            "user_agent": "Mozilla/5.0 (Linux; Android 12; moto g(10)) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Mobile Safari/537.36",
            "viewport": {"width": 412, "height": 915},
            "is_mobile": True,
            "has_touch": True,
            "device_scale_factor": 2.75,
            "locale": "es-ES",
            "timezone_id": "America/Buenos_Aires",
        },
    ]
    profile_index = 0

    # Prepara controlador VPN si se solicita
    vpn = get_vpn_controller(
        provider=vpn_provider,
        country=vpn_country,
        servers=vpn_servers,
        wait_ms=vpn_wait_ms,
    ) if rotate_vpn_per_url else None

    with sync_playwright() as p:
        browser_type = p.chromium

        def new_browser_and_context():
            # Intentar abrir con bandera de incógnito visible si se solicita y hay UI
            launch_kwargs: Dict = {"headless": headless}
            if current_proxy:
                launch_kwargs["proxy"] = current_proxy
            # Determina opciones de contexto según perfil
            context_opts: Dict = {"user_agent": ua}
            current_profile = None
            if rotate_android_profiles and android_profiles:
                current_profile = android_profiles[profile_index % len(android_profiles)]
                context_opts.update({
                    "user_agent": current_profile["user_agent"],
                    "viewport": current_profile["viewport"],
                    "is_mobile": current_profile["is_mobile"],
                    "has_touch": current_profile["has_touch"],
                    "device_scale_factor": current_profile["device_scale_factor"],
                    "locale": current_profile["locale"],
                    "timezone_id": current_profile["timezone_id"],
                })
            if not headless and force_incognito:
                # Lanzar incognito con mejores fallbacks: Chrome channel -> Chrome exe -> Edge InPrivate -> Chromium
                args_incog = ["--incognito", "--new-window", "--no-first-run", "--no-default-browser-check"]
                try:
                    browser = browser_type.launch(**launch_kwargs, channel="chrome", args=args_incog)
                except Exception:
                    chrome_exe = find_chrome_executable()
                    if chrome_exe:
                        try:
                            browser = browser_type.launch(**launch_kwargs, executable_path=chrome_exe, args=args_incog)
                        except Exception:
                            try:
                                # Edge InPrivate como último recurso visible en Windows
                                browser = browser_type.launch(**launch_kwargs, channel="msedge", args=["--inprivate", "--new-window"])
                            except Exception:
                                browser = browser_type.launch(**launch_kwargs, args=["--incognito"])
                    else:
                        try:
                            browser = browser_type.launch(**launch_kwargs, channel="msedge", args=["--inprivate", "--new-window"])
                        except Exception:
                            browser = browser_type.launch(**launch_kwargs, args=["--incognito"])
            else:
                browser = browser_type.launch(**launch_kwargs)
            context = browser.new_context(**context_opts)
            return browser, context

        browser, context = new_browser_and_context()

        for idx, url in enumerate(urls):
            # Cambia VPN antes de cada URL si está habilitado
            if vpn is not None:
                try:
                    vpn.connect_next()
                except Exception:
                    pass
            if max_pages_per_proxy > 0 and pages_on_current_proxy >= max_pages_per_proxy:
                try:
                    context.close()
                    browser.close()
                except Exception:
                    pass
                pages_on_current_proxy = 0
                current_proxy = rotator.next()
                if rotate_android_profiles:
                    profile_index += 1
                browser, context = new_browser_and_context()

            if respect_robots and not is_allowed_by_robots(url, ua):
                results.append({
                    "url": url,
                    "status": "blocked_by_robots",
                    "proxy": current_proxy["server"] if current_proxy else None,
                    "screenshot": None,
                    "elapsed_ms": 0,
                })
                pages_on_current_proxy += 1
                continue

            # Normaliza rangos de permanencia
            if min_dwell_ms > max_dwell_ms:
                min_dwell_ms, max_dwell_ms = max_dwell_ms, min_dwell_ms

            page = context.new_page()
            started = time.time()
            status = "ok"
            error = None
            screenshot_path = None
            video_playing = False

            try:
                page.goto(url, wait_until="domcontentloaded", timeout=30000)

                # Ejecuta acciones si fueron provistas
                if actions:
                    for act in actions:
                        try:
                            t = act.get("type")
                            if t == "wait_for":
                                sel = act.get("selector")
                                if sel:
                                    page.wait_for_selector(sel, timeout=10000)
                            elif t == "click":
                                sel = act.get("selector")
                                if sel:
                                    page.click(sel, timeout=10000)
                            elif t == "type":
                                sel = act.get("selector")
                                txt = act.get("text", "")
                                if sel is not None:
                                    page.fill(sel, txt)
                            elif t == "gmail_sign_in":
                                email = act.get("email")
                                passwd = act.get("password")
                                if email and passwd:
                                    try:
                                        p2 = context.new_page()
                                        # Ir al flujo de login
                                        try:
                                            p2.goto("https://accounts.google.com/signin/v2/identifier?hl=es", wait_until="domcontentloaded", timeout=30000)
                                        except Exception:
                                            try:
                                                p2.goto("https://accounts.google.com/signin", wait_until="domcontentloaded", timeout=30000)
                                            except Exception:
                                                pass
                                        # Intentar aceptar consentimientos
                                        try:
                                            texts = ["Aceptar", "Acepto", "Estoy de acuerdo", "Continuar", "Ok", "Entendido", "Permitir", "Accept", "Agree", "Accept all", "Aceptar todo", "Aceptar todas", "Rechazar todo", "Reject all"]
                                            btns = p2.locator("button, [role='button'], a")
                                            count = min(10, btns.count())
                                            for i in range(count):
                                                try:
                                                    t = (btns.nth(i).inner_text() or "").lower().strip()
                                                    if any(x.lower() in t for x in texts):
                                                        btns.nth(i).click(force=True)
                                                        break
                                                except Exception:
                                                    pass
                                        except Exception:
                                            pass
                                        # Rellenar email
                                        try:
                                            if p2.locator("input[name='identifier']").count() > 0:
                                                p2.fill("input[name='identifier']", email)
                                            else:
                                                p2.fill("input[type='email']", email)
                                        except Exception:
                                            pass
                                        # Siguiente email
                                        clicked_next = False
                                        for sel in ["#identifierNext button", "button:has-text('Siguiente')", "text=Next", "button[type='submit']"]:
                                            try:
                                                p2.click(sel, timeout=6000, force=True)
                                                clicked_next = True
                                                break
                                            except Exception:
                                                pass
                                        if not clicked_next:
                                            try:
                                                p2.keyboard.press("Enter")
                                            except Exception:
                                                pass
                                        # Esperar y rellenar contraseña
                                        try:
                                            p2.wait_for_selector("input[type='password'], input[name='Passwd']", timeout=15000)
                                            if p2.locator("input[name='Passwd']").count() > 0:
                                                p2.fill("input[name='Passwd']", passwd)
                                            else:
                                                p2.fill("input[type='password']", passwd)
                                        except Exception:
                                            pass
                                        # Siguiente contraseña
                                        for sel in ["#passwordNext button", "button:has-text('Siguiente')", "text=Next", "button[type='submit']"]:
                                            try:
                                                p2.click(sel, timeout=6000, force=True)
                                                break
                                            except Exception:
                                                pass
                                        try:
                                            p2.wait_for_timeout(800)
                                        except Exception:
                                            pass
                                        try:
                                            p2.close()
                                        except Exception:
                                            pass
                                        # Recargar página actual para reflejar login
                                        try:
                                            page.reload()
                                        except Exception:
                                            pass
                                    except Exception:
                                        pass
                            elif t == "js":
                                code = act.get("code")
                                if code:
                                    page.evaluate(code)
                            elif t == "play_video":
                                sel = act.get("selector")
                                if sel:
                                    page.evaluate(
                                        "selector => { const v = document.querySelector(selector); if (v && v.play) v.play(); }",
                                        sel,
                                    )
                                else:
                                    page.evaluate(
                                        "() => { const v = document.querySelector('video'); if (v && v.play) v.play(); }"
                                    )
                        except Exception:
                            # Ignora errores de acción individuales para no detener toda la visita
                            pass

                # Comprobar estado de reproducción de video tras acciones
                try:
                    status_info = page.evaluate(
                        "() => { const v = document.querySelector('video'); return { hasVideo: !!v, isPlaying: !!(v && !v.paused && v.currentTime > 0), readyState: v ? v.readyState : null }; }"
                    )
                    video_playing = bool(status_info.get("isPlaying")) if isinstance(status_info, dict) else False
                except Exception:
                    video_playing = False

                # Comportamiento básico de lectura para QA: pequeño scroll y permanencia configurable
                page.wait_for_timeout(random.randint(500, 1500))
                page.mouse.wheel(0, random.randint(800, 2400))
                dwell = random.randint(min_dwell_ms, max_dwell_ms)
                page.wait_for_timeout(dwell)

                if screenshot:
                    screenshot_path = os.path.join(artifacts_dir, f"page_{idx}.png")
                    page.screenshot(path=screenshot_path, full_page=True)
            except Exception as e:
                # Fallback: si falla con proxy, reintenta sin proxy para asegurar funcionalidad
                status = "error"
                error = str(e)
                if current_proxy:
                    try:
                        try:
                            page.close()
                        except Exception:
                            pass
                        try:
                            context.close()
                        except Exception:
                            pass
                        try:
                            browser.close()
                        except Exception:
                            pass

                        # Desactiva proxy y reintenta
                        current_proxy = None
                        browser, context = new_browser_and_context()
                        page = context.new_page()

                        page.goto(url, wait_until="domcontentloaded", timeout=35000)

                        if actions:
                            for act in actions:
                                try:
                                    t = act.get("type")
                                    if t == "wait_for":
                                        sel = act.get("selector")
                                        if sel:
                                            page.wait_for_selector(sel, timeout=10000)
                                    elif t == "click":
                                        sel = act.get("selector")
                                        if sel:
                                            page.click(sel, timeout=10000)
                                    elif t == "type":
                                        sel = act.get("selector")
                                        txt = act.get("text", "")
                                        if sel is not None:
                                            page.fill(sel, txt)
                                    elif t == "gmail_sign_in":
                                        email = act.get("email")
                                        passwd = act.get("password")
                                        if email and passwd:
                                            try:
                                                p2 = context.new_page()
                                                try:
                                                    p2.goto(
                                                        "https://accounts.google.com/signin/v2/identifier?service=mail&hl=es-419",
                                                        wait_until="domcontentloaded",
                                                        timeout=40000,
                                                    )
                                                except Exception:
                                                    try:
                                                        p2.goto(
                                                            "https://accounts.google.com/ServiceLogin?service=mail",
                                                            wait_until="domcontentloaded",
                                                            timeout=40000,
                                                        )
                                                    except Exception:
                                                        pass
                                                # Consentimientos posibles
                                                try:
                                                    for txt in [
                                                        "Aceptar","Acepto","Estoy de acuerdo","Continuar","Ok","Entendido","Permitir",
                                                        "Aceptar todo","Rechazar todo","Aceptar todas","Reject all",
                                                        "Agree","I agree","Accept","Accept all"
                                                    ]:
                                                        try:
                                                            p2.get_by_text(txt, exact=False).click(timeout=1500)
                                                        except Exception:
                                                            pass
                                                except Exception:
                                                    pass
                                                # Email
                                                try:
                                                    filled = False
                                                    for sel in [
                                                        'input[type="email"]',
                                                        'input[name="identifier"]',
                                                        '#identifierId',
                                                    ]:
                                                        try:
                                                            p2.fill(sel, email)
                                                            filled = True
                                                            break
                                                        except Exception:
                                                            pass
                                                    if not filled:
                                                        try:
                                                            p2.get_by_label("Correo electrónico", exact=False).fill(email)
                                                        except Exception:
                                                            pass
                                                except Exception:
                                                    pass
                                                # Next
                                                try:
                                                    clicked = False
                                                    for name in ["Siguiente","Next","Continuar","Continue"]:
                                                        try:
                                                            p2.get_by_role("button", name=name).click(timeout=2500)
                                                            clicked = True
                                                            break
                                                        except Exception:
                                                            pass
                                                    if not clicked:
                                                        try:
                                                            p2.click('button[type="submit"]', timeout=2500)
                                                        except Exception:
                                                            pass
                                                except Exception:
                                                    pass
                                                # Password
                                                try:
                                                    p2.wait_for_selector('input[type="password"]', timeout=10000)
                                                except Exception:
                                                    pass
                                                try:
                                                    filledp = False
                                                    for sel in [
                                                        'input[type="password"]',
                                                        'input[name="Passwd"]',
                                                    ]:
                                                        try:
                                                            p2.fill(sel, passwd)
                                                            filledp = True
                                                            break
                                                        except Exception:
                                                            pass
                                                    if not filledp:
                                                        try:
                                                            p2.get_by_label("Contraseña", exact=False).fill(passwd)
                                                        except Exception:
                                                            pass
                                                except Exception:
                                                    pass
                                                try:
                                                    clicked2 = False
                                                    for name in ["Siguiente","Next","Continuar","Continue"]:
                                                        try:
                                                            p2.get_by_role("button", name=name).click(timeout=2500)
                                                            clicked2 = True
                                                            break
                                                        except Exception:
                                                            pass
                                                    if not clicked2:
                                                        try:
                                                            p2.click('button[type="submit"]', timeout=2500)
                                                        except Exception:
                                                            pass
                                                except Exception:
                                                    pass
                                                try:
                                                    p2.close()
                                                except Exception:
                                                    pass
                                                # Recargar la página original para reflejar sesión
                                                try:
                                                    page.reload()
                                                except Exception:
                                                    pass
                                            except Exception:
                                                pass
                                    elif t == "js":
                                        code = act.get("code")
                                        if code:
                                            page.evaluate(code)
                                    elif t == "play_video":
                                        sel = act.get("selector")
                                        if sel:
                                            page.evaluate(
                                                "selector => { const v = document.querySelector(selector); if (v && v.play) v.play(); }",
                                                sel,
                                            )
                                        else:
                                            page.evaluate(
                                                "() => { const v = document.querySelector('video'); if (v && v.play) v.play(); }"
                                            )
                                except Exception:
                                    pass

                        # Recalcular estado de reproducción en fallback
                        try:
                            status_info = page.evaluate(
                                "() => { const v = document.querySelector('video'); return { hasVideo: !!v, isPlaying: !!(v && !v.paused && v.currentTime > 0), readyState: v ? v.readyState : null }; }"
                            )
                            video_playing = bool(status_info.get("isPlaying")) if isinstance(status_info, dict) else False
                        except Exception:
                            video_playing = False

                        page.wait_for_timeout(random.randint(500, 1500))
                        page.mouse.wheel(0, random.randint(800, 2400))
                        dwell = random.randint(min_dwell_ms, max_dwell_ms)
                        page.wait_for_timeout(dwell)

                        if screenshot:
                            screenshot_path = os.path.join(artifacts_dir, f"page_{idx}.png")
                            page.screenshot(path=screenshot_path, full_page=True)

                        status = "ok"
                        error = None
                    except Exception as e2:
                        status = "error"
                        error = f"proxy_failed_and_fallback_error: {e2} | original: {error}"
            finally:
                try:
                    page.close()
                except Exception:
                    pass

            pages_on_current_proxy += 1
            results.append({
                "url": url,
                "status": status,
                "error": error,
                "proxy": current_proxy["server"] if current_proxy else None,
                "screenshot": screenshot_path,
                "elapsed_ms": int((time.time() - started) * 1000),
                "min_dwell_ms": min_dwell_ms,
                "max_dwell_ms": max_dwell_ms,
                "device_profile": (android_profiles[profile_index % len(android_profiles)]["name"] if rotate_android_profiles and android_profiles else None),
                "vpn_provider": vpn_provider if vpn is not None else None,
                "vpn_country": vpn_country if vpn is not None else None,
                "video_playing": video_playing,
            })

        try:
            context.close()
            browser.close()
        except Exception:
            pass

    return {"status": "completed", "results": results}


def create_google_accounts_backup(
    count: int = 1,
    incognito: bool = True,
    rotate_vpn_per_account: bool = False,
    vpn_provider: Optional[str] = None,
    vpn_country: Optional[str] = None,
    vpn_servers: Optional[List[str]] = None,
    vpn_wait_ms: int = 5000,
    password: str = "",
) -> Dict:
    """
    Intenta preparar la creación de cuentas y capturar el correo elegido.

    Navega al formulario de alta de Google, rellena nombre, apellidos, fecha
    de nacimiento y género, intenta seleccionar la primera sugerencia de email
    y establece la contraseña proporcionada. Es posible que Google requiera
    verificación adicional (SMS/CAPTCHA). En ese caso se devuelve el email
    candidato y el estado "verification_required".
    """
    ensure_artifacts_dir()

    items = []
    vpn = get_vpn_controller(
        provider=vpn_provider,
        country=vpn_country,
        servers=vpn_servers,
        wait_ms=vpn_wait_ms,
    ) if rotate_vpn_per_account else None

    male_names = [
        "Pablo","Juan","Luis","Carlos","Diego","Miguel","Nicolás","Sergio","Andrés","Hernán",
        "Martín","Jorge","Ramiro","Gustavo","Ricardo","Tomás","Mateo","Agustín","Felipe","Iván",
        "Alejandro","Eduardo","Emiliano","Esteban","Federico","Franco","Gabriel","Gonzalo","Guillermo","Hugo",
        "Iker","Julián","Leandro","Manuel","Mario","Mauricio","Maximiliano","Nahuel","Óscar","Patricio",
        "Rafael","Raúl","Santiago","Sebastián","Tadeo","Thiago","Valentín","Vladimir","Bruno","Damián"
    ]
    female_names = [
        "María","Lucía","Sofía","Valentina","Camila","Julieta","Agustina","Carolina","Paula","Florencia",
        "Natalia","Rocío","Daniela","Noelia","Romina","Milagros","Martina","Victoria","Abril","Bianca",
        "Ariana","Brisa","Cecilia","Claudia","Constanza","Elena","Emma","Gabriela","Isabella","Jimena",
        "Josefina","Kiara","Lola","Luna","Magdalena","Micaela","Naira","Olivia","Pilar","Renata",
        "Salomé","Tamara","Teresa","Triana","Uma","Violeta","Ximena","Zaira","Alma","Agustina"
    ]
    last_names = [
        "González","Rodríguez","García","Fernández","López","Martínez","Pérez","Sánchez","Romero","Díaz",
        "Silva","Torres","Ruiz","Suárez","Álvarez","Vega","Navarro","Molina","Castro","Ortega",
        "Acosta","Aguilar","Arias","Benítez","Blanco","Bravo","Campos","Córdoba","Correa","Cruz",
        "Delgado","Domínguez","Escobar","Figueroa","Fuentes","Giménez","Guerrero","Herrera","Ibarra","Juárez",
        "Luna","Mejía","Miranda","Montero","Morales","Núñez","Orozco","Pacheco","Palacios","Peña",
        "Quiroga","Ramos","Rojas","Salas","Salazar","Soto","Vargas","Velázquez","Villarreal","Zamora"
    ]

    def rand_birth():
        # Asegurar mayoría de edad para evitar bloqueos silenciosos en "Información básica"
        year = random.randint(1980, 2000)
        month = random.randint(1, 12)
        day = random.randint(1, 28)
        return year, month, day

    with sync_playwright() as p:
        browser_type = p.chromium
        # Helper para lanzar/re-lanzar navegador con la misma política de incógnito
        def launch_browser():
            """Abrir una única ventana visible (preferencia: incógnito) sin CDP.

            - Se evita crear procesos externos y conexiones CDP que pueden abrir varias ventanas.
            - Se intenta primero `channel="chrome"` con flags de incógnito.
            - Fallback por ruta ejecutable, luego Edge InPrivate; último: Chromium headful.
            """
            args_incog = ["--incognito", "--new-window", "--no-first-run", "--no-default-browser-check"]
            try:
                browser = browser_type.launch(headless=False, channel="chrome", args=args_incog)
                print("[incognito] Chrome via channel con incógnito.")
                return browser
            except Exception:
                pass
            chrome_exe = None
            try:
                chrome_exe = find_chrome_executable()
            except Exception:
                chrome_exe = None
            if chrome_exe:
                try:
                    browser = browser_type.launch(headless=False, executable_path=chrome_exe, args=args_incog)
                    print("[incognito] Chrome por ruta ejecutable con incógnito.")
                    return browser
                except Exception:
                    pass
            try:
                browser = browser_type.launch(headless=False, channel="msedge", args=["--inprivate", "--new-window"])
                print("[incognito] Edge en modo InPrivate.")
                return browser
            except Exception:
                pass
            print("[fallback] Abriendo Chromium headful (sin incógnito).")
            return browser_type.launch(headless=False)

        browser = launch_browser()
        context = None

        for i in range(max(1, int(count))):
            if vpn is not None:
                try:
                    vpn.connect_next()
                except Exception:
                    pass

            # Abrir página en la MISMA ventana incógnito conectada por CDP (contexto persistente).
            try:
                context = browser.contexts[0] if getattr(browser, "contexts", None) else None
                if context is None:
                    # Si por alguna razón no hay contexto persistente, crear uno como fallback
                    context = browser.new_context()
                page = context.new_page()
            except Exception:
                # Si el navegador fue cerrado/desconectado, re-lanzar y reintentar
                try:
                    try:
                        browser.close()
                    except Exception:
                        pass
                    browser = launch_browser()
                    context = browser.contexts[0] if getattr(browser, "contexts", None) else browser.new_context()
                    page = context.new_page()
                except Exception as e_relaunch:
                    # Propagar con mensaje claro
                    raise RuntimeError(f"No se pudo abrir página en ventana incognito; relanzado falló: {e_relaunch}")
            started = time.time()
            screenshot_path = None
            status = "attempted"
            error = None
            chosen_email = None

            try:
                # Paso 1: abrir Google y acceder (con fallbacks)
                def try_goto(u: str, to: int = 35000):
                    try:
                        page.goto(u, wait_until="domcontentloaded", timeout=to)
                        page.wait_for_timeout(400)
                        return True
                    except Exception:
                        return False

                ok = try_goto("https://www.google.com")
                if not ok:
                    # Fallbacks directos a flows de cuenta, priorizando URL sugerida por el usuario
                    identifier_fallbacks = [
                        # Variante con continue -> Google
                        "https://accounts.google.com/v3/signin/identifier?continue=https%3A%2F%2Fwww.google.com%2F&hl=es-419&passive=true&flowName=GlifWebSignIn&flowEntry=ServiceLogin",
                        # Variante directa al servicio de Mail
                        "https://accounts.google.com/v3/signin/identifier?service=mail&hl=es-419&passive=true&flowName=GlifWebSignIn&flowEntry=ServiceLogin",
                        # Fallback clásico
                        "https://accounts.google.com/signin/v2/identifier?service=mail",
                        "https://accounts.google.com/ServiceLogin?service=mail",
                    ]
                    ok = any(try_goto(u, 40000) for u in identifier_fallbacks) or \
                         try_goto("https://accounts.google.com/signup/v2/createaccount?service=mail", 40000) or \
                         try_goto("https://www.google.com/?hl=es")

                # Intentar aceptar consentimientos genéricos si aparecen
                try:
                    for txt in [
                        "Aceptar","Acepto","Estoy de acuerdo","Continuar","Ok","Entendido","Permitir",
                        "Aceptar todo","Rechazar todo","Aceptar todas","Reject all",
                        "Agree","I agree","Accept","Accept all"
                    ]:
                        try:
                            page.get_by_text(txt, exact=False).click(timeout=1500)
                        except Exception:
                            pass
                except Exception:
                    pass

                # Ir directo al formulario de alta (más confiable)
                try:
                    page.goto(
                        "https://accounts.google.com/signup/v2/createaccount?service=mail&hl=es-419",
                        wait_until="domcontentloaded",
                        timeout=40000,
                    )
                except Exception:
                    try:
                        page.goto(
                            "https://accounts.google.com/signup/v2/webcreateaccount?flowName=GlifWebSignIn&flowEntry=SignUp",
                            wait_until="domcontentloaded",
                            timeout=40000,
                        )
                    except Exception:
                        # Último intento: empujar nuevamente al identificador si la ruta de alta falla
                        try:
                            page.goto(
                                "https://accounts.google.com/v3/signin/identifier?service=mail&hl=es-419&passive=true&flowName=GlifWebSignIn&flowEntry=ServiceLogin",
                                wait_until="domcontentloaded",
                                timeout=35000,
                            )
                        except Exception:
                            pass
                try:
                    page.wait_for_selector('input[name="firstName"]', timeout=10000)
                except Exception:
                    pass

                # Clic en "Acceder" (fallback si el formulario no abre)
                try:
                    if "signup" not in page.url:
                        for name in ["Iniciar sesión", "Acceder", "Sign in"]:
                            try:
                                page.get_by_role("link", name=name).click(timeout=2000)
                                break
                            except Exception:
                                pass
                except Exception:
                    pass
                page.wait_for_timeout(400)

                # Paso 2: crear cuenta
                # Si estamos en login, buscar "Crear cuenta"
                try:
                    for txt in ["Crear cuenta", "Create account"]:
                        for role in ["button", "link"]:
                            try:
                                page.get_by_role(role, name=txt).click(timeout=2000)
                                break
                            except Exception:
                                pass
                    # Elegir "Para mí" / "For myself"
                    for opt in ["Para mí", "For myself"]:
                        try:
                            page.get_by_text(opt, exact=False).click(timeout=1500)
                            break
                        except Exception:
                            pass
                except Exception:
                    pass

                # Si aterrizamos en otra ruta, navega al formulario de alta explícito
                try:
                    if "signup" not in page.url:
                        page.goto("https://accounts.google.com/signup/v2/createaccount?service=mail", wait_until="domcontentloaded", timeout=40000)
                except Exception:
                    page.goto("https://accounts.google.com/signup/v2/createaccount?service=mail", wait_until="domcontentloaded", timeout=40000)
                page.wait_for_timeout(600)
                # Fallback: si estamos en pantalla de login, avanzar a crear cuenta
                try:
                    # Variantes en ES/EN
                    for txt in ["Crear cuenta", "Create account"]:
                        # Puede ser botón o enlace con menu desplegable
                        for role in ["button", "link"]:
                            try:
                                page.get_by_role(role, name=txt).click(timeout=2000)
                                break
                            except Exception:
                                pass
                    # Seleccionar opción "Para mí" / "For myself" si aparece menú
                    for opt in ["Para mí", "For myself"]:
                        try:
                            page.get_by_text(opt, exact=False).click(timeout=1500)
                            break
                        except Exception:
                            pass
                except Exception:
                    pass

                gender_flag = random.choice(["male","female"])
                first = random.choice(male_names) if gender_flag == "male" else random.choice(female_names)
                last = random.choice(last_names)
                year, month, day = rand_birth()

                # Ajustar género según el nombre elegido (determinista)
                try:
                    if first in male_names:
                        gender_flag = "male"
                    elif first in female_names:
                        gender_flag = "female"
                except Exception:
                    pass

                try:
                    page.fill('input[name="firstName"]', first)
                except Exception:
                    pass
                try:
                    page.fill('input[name="lastName"]', last)
                except Exception:
                    pass
                # Clic robusto en "Siguiente" tras completar nombre y apellido
                try:
                    page.wait_for_timeout(300)
                    clicked = False
                    for name in ["Siguiente","Next","Continuar","Continue"]:
                        try:
                            page.get_by_role("button", name=name).click(timeout=2500)
                            clicked = True
                            break
                        except Exception:
                            pass
                    if not clicked:
                        try:
                            btn = page.locator('xpath=//span[contains(text(),"Siguiente") or contains(text(),"Next") or contains(text(),"Continuar") or contains(text(),"Continue")]/ancestor::button').first
                            if btn and btn.count() > 0:
                                btn.click(timeout=2500, force=True)
                                clicked = True
                        except Exception:
                            pass
                    if not clicked:
                        try:
                            page.click('button[type="submit"]', timeout=2500, force=True)
                            clicked = True
                        except Exception:
                            pass
                    if not clicked:
                        try:
                            page.focus('input[name="lastName"]')
                            page.keyboard.press('Enter')
                            clicked = True
                        except Exception:
                            pass
                    if not clicked:
                        try:
                            page.evaluate("() => { const labels = ['Siguiente','Next','Continuar','Continue']; const btns = Array.from(document.querySelectorAll('button,[role=button]')); const b = btns.find(el => labels.some(l => (el.textContent||'').includes(l))); if (b) b.click(); }")
                            clicked = True
                        except Exception:
                            pass
                    page.wait_for_timeout(500)
                except Exception:
                    pass
                # Seleccionar mes por etiqueta visible (ES/EN) con fallbacks
                try:
                    # Helper: selección en combobox Material Design con fallback a <select>
                    def select_material_combo(combo_labels, option_labels, fallback_select_name=None):
                        # 1) Intentar combobox por rol y etiqueta visible
                        for lab in combo_labels:
                            try:
                                cb = page.get_by_role("combobox", name=lab)
                                if cb and cb.count() > 0:
                                    cb.first.click(timeout=2000)
                                    page.wait_for_timeout(200)
                                    for opt in option_labels:
                                        try:
                                            page.get_by_role("option", name=opt).first.click(timeout=1500)
                                            return True
                                        except Exception:
                                            # Buscar por texto en lista desplegable genérica
                                            try:
                                                page.locator('[role="listbox"] [role="option"]').filter(has_text=opt).first.click(timeout=1500)
                                                return True
                                            except Exception:
                                                pass
                                            # Intento adicional: click por texto directo (no limitado al rol)
                                            try:
                                                page.get_by_text(opt, exact=False).first.click(timeout=1500)
                                                return True
                                            except Exception:
                                                pass
                                    # Intento adicional: coincidencia insensible a mayúsculas usando regex
                                    try:
                                        import re
                                        for opt in option_labels:
                                            try:
                                                regex = re.compile(rf"^{re.escape(opt)}$", re.IGNORECASE)
                                                page.get_by_role("option", name=regex).first.click(timeout=1500)
                                                return True
                                            except Exception:
                                                try:
                                                    regex2 = re.compile(re.escape(opt), re.IGNORECASE)
                                                    page.get_by_role("option", name=regex2).first.click(timeout=1500)
                                                    return True
                                                except Exception:
                                                    pass
                                    except Exception:
                                        pass
                                    # Si no se encontró opción, cerrar y seguir
                                    try:
                                        page.keyboard.press('Escape')
                                    except Exception:
                                        pass
                            except Exception:
                                pass
                        # 1b) Intentar combobox por aria-label directo (muchas UIs lo usan)
                        for lab in combo_labels:
                            try:
                                cb = page.locator(f'[aria-label="{lab}"]').first
                                if cb and cb.count() > 0:
                                    cb.click(timeout=2000)
                                    page.wait_for_timeout(200)
                                    for opt in option_labels:
                                        # Click por rol opción o por texto
                                        try:
                                            page.get_by_role("option", name=opt).first.click(timeout=1500)
                                            return True
                                        except Exception:
                                            try:
                                                page.locator('[role="option"]').filter(has_text=opt).first.click(timeout=1500)
                                                return True
                                            except Exception:
                                                pass
                                            try:
                                                page.get_by_text(opt, exact=False).first.click(timeout=1500)
                                                return True
                                            except Exception:
                                                pass
                                    # Intento adicional con regex insensible a mayúsculas
                                    try:
                                        import re
                                        for opt in option_labels:
                                            try:
                                                regex = re.compile(rf"^{re.escape(opt)}$", re.IGNORECASE)
                                                page.get_by_role("option", name=regex).first.click(timeout=1500)
                                                return True
                                            except Exception:
                                                try:
                                                    regex2 = re.compile(re.escape(opt), re.IGNORECASE)
                                                    page.get_by_role("option", name=regex2).first.click(timeout=1500)
                                                    return True
                                                except Exception:
                                                    pass
                                    except Exception:
                                        pass
                                    # Intentar escribir y Enter (combos con búsqueda)
                                    try:
                                        page.keyboard.type(str(option_labels[0]))
                                        page.keyboard.press('Enter')
                                        return True
                                    except Exception:
                                        pass
                                    try:
                                        page.keyboard.press('Escape')
                                    except Exception:
                                        pass
                            except Exception:
                                pass
                        # 2) Intentar por label directa/aria-label
                        for lab in combo_labels:
                            try:
                                el = page.get_by_label(lab)
                                if el and el.count() > 0:
                                    el.first.click(timeout=2000)
                                    page.wait_for_timeout(200)
                                    for opt in option_labels:
                                        try:
                                            page.get_by_role("option", name=opt).first.click(timeout=1500)
                                            return True
                                        except Exception:
                                            try:
                                                page.locator('[role="listbox"] [role="option"]').filter(has_text=opt).first.click(timeout=1500)
                                                return True
                                            except Exception:
                                                pass
                            except Exception:
                                pass
                        # 2b) XPath: label -> combobox
                        for lab in combo_labels:
                            try:
                                cb = page.locator(f'xpath=//label[contains(normalize-space(.),"{lab}")]/following::*[@role="combobox" or @aria-haspopup="listbox" or @role="button"][1]').first
                                if cb and cb.count() > 0:
                                    cb.click(timeout=2000)
                                    page.wait_for_timeout(200)
                                    for opt in option_labels:
                                        try:
                                            page.locator('xpath=//*[@role="option" and (normalize-space(.)=concat("", "", "") or contains(normalize-space(.), ""))]').filter(has_text=opt).first.click(timeout=1500)
                                            return True
                                        except Exception:
                                            try:
                                                page.locator('[role="option"]').filter(has_text=opt).first.click(timeout=1500)
                                                return True
                                            except Exception:
                                                pass
                                    # Regex insensible a mayúsculas como último intento en esta ruta
                                    try:
                                        import re
                                        for opt in option_labels:
                                            try:
                                                regex = re.compile(re.escape(opt), re.IGNORECASE)
                                                page.get_by_role("option", name=regex).first.click(timeout=1500)
                                                return True
                                            except Exception:
                                                pass
                                    except Exception:
                                        pass
                                    try:
                                        page.keyboard.type(str(option_labels[0]))
                                        page.keyboard.press('Enter')
                                        return True
                                    except Exception:
                                        pass
                            except Exception:
                                pass
                        # 3) Fallback: <select name="...">
                        if fallback_select_name:
                            try:
                                page.wait_for_selector(f'select[name="{fallback_select_name}"]', timeout=2000)
                                for opt in option_labels:
                                    try:
                                        page.select_option(f'select[name="{fallback_select_name}"]', label=opt)
                                        return True
                                    except Exception:
                                        # Buscar opción por texto y seleccionar por valor
                                        try:
                                            o = page.locator(f'select[name="{fallback_select_name}"] option').filter(has_text=opt).first
                                            if o and o.count() > 0:
                                                val = o.get_attribute('value')
                                                if val:
                                                    page.select_option(f'select[name="{fallback_select_name}"]', val)
                                                    return True
                                        except Exception:
                                            pass
                                # Último recurso: seleccionar por valor numérico
                                try:
                                    page.select_option(f'select[name="{fallback_select_name}"]', option_labels[-1])
                                    return True
                                except Exception:
                                    pass
                            except Exception:
                                pass
                        # 3) Fallback final: recorrer todas las opciones y hacer match por texto en minúsculas
                        try:
                            lower_opts = [str(o).lower() for o in option_labels]
                            ok = page.evaluate(
                                "(opts) => {\n                                    const options = Array.from(document.querySelectorAll('[role=option]'));\n                                    for (const el of options) {\n                                        const t = (el.innerText || el.textContent || '').trim().toLowerCase();\n                                        if (opts.some(o => t === o || t.includes(o))) {\n                                            el.click();\n                                            return true;\n                                        }\n                                    }\n                                    const native = document.querySelector('select');\n                                    if (native) {\n                                        for (const opt of Array.from(native.options)) {\n                                            const t = (opt.textContent||'').trim().toLowerCase();\n                                            if (opts.some(o => t === o || t.includes(o))) {\n                                                native.value = opt.value;\n                                                native.dispatchEvent(new Event('change',{bubbles:true}));\n                                                return true;\n                                            }\n                                        }\n                                    }\n                                    return false;\n                                }",
                                lower_opts,
                            )
                            if ok:
                                return True
                        except Exception:
                            pass
                        return False

                    months_es = {
                        1: "Enero", 2: "Febrero", 3: "Marzo", 4: "Abril", 5: "Mayo", 6: "Junio",
                        7: "Julio", 8: "Agosto", 9: "Septiembre", 10: "Octubre", 11: "Noviembre", 12: "Diciembre",
                    }
                    months_en = {
                        1: "January", 2: "February", 3: "March", 4: "April", 5: "May", 6: "June",
                        7: "July", 8: "August", 9: "September", 10: "October", 11: "November", 12: "December",
                    }
                    label_month_es = months_es.get(month, str(month))
                    label_month_en = months_en.get(month, str(month))
                    label_month_es_caps = label_month_es.upper()
                    label_month_en_caps = label_month_en.upper()
                    # Intentar por combobox "Mes"/"Month", con alternativas
                    # Intentar primero en español (lista desplegable con "octubre noviembre enero febrero" etc.)
                    # Construir opciones de mes en español priorizando mayúsculas y variantes
                    opts_es = [label_month_es, label_month_es_caps, label_month_es.lower()]
                    # Variante regional "Setiembre" (LATAM) para septiembre
                    try:
                        if str(month) == '9':
                            opts_es += ["Setiembre", "SETIEMBRE", "setiembre"]
                    except Exception:
                        pass
                    selected_month = select_material_combo([
                        "Mes", "MES", "Fecha de nacimiento"
                    ], opts_es, fallback_select_name="month")
                    if not selected_month:
                        # Fallback si la UI está en inglés
                        selected_month = select_material_combo([
                            "Month", "Date of birth"
                        ], [label_month_en, label_month_en_caps, label_month_en.lower()], fallback_select_name="month")
                    if not selected_month:
                        # Último recurso: forzar por JavaScript si existiera <select>
                        try:
                            page.evaluate(
                                "(lab) => { const sel = document.querySelector('select[name=month]'); if(!sel) return; const opt = Array.from(sel.options).find(o => (o.textContent||'').trim().toLowerCase() === lab.toLowerCase()); if(opt){ sel.value = opt.value; sel.dispatchEvent(new Event('change',{bubbles:true})); } }",
                                label_month_es,
                            )
                        except Exception:
                            pass
                    if not selected_month:
                        # Intentar abrir el combobox y seleccionar por índice (orden natural)
                        try:
                            # Abrir por rol/label
                            cb = None
                            for lab in ["Mes", "Month", "Fecha de nacimiento", "Date of birth"]:
                                try:
                                    el = page.get_by_role("combobox", name=lab)
                                    if el and el.count() > 0:
                                        cb = el.first
                                        break
                                except Exception:
                                    pass
                            if cb is None:
                                for lab in ["Mes", "Month"]:
                                    try:
                                        el = page.get_by_label(lab)
                                        if el and el.count() > 0:
                                            cb = el.first
                                            break
                                    except Exception:
                                        pass
                            if cb is None:
                                for lab in ["Mes", "Month"]:
                                    try:
                                        el = page.locator(f'[aria-label="{lab}"]').first
                                        if el and el.count() > 0:
                                            cb = el
                                            break
                                    except Exception:
                                        pass
                            if cb is not None:
                                try:
                                    cb.click(timeout=2000)
                                    page.wait_for_timeout(150)
                                except Exception:
                                    pass
                            # Seleccionar la opción N-ésima
                            try:
                                opts = page.locator('[role="listbox"] [role="option"]')
                                if opts and opts.count() >= month:
                                    opts.nth(month-1).click(timeout=1500)
                                    selected_month = True
                                else:
                                    # Fallback: cualquier opción que contenga el texto en mayúsculas
                                    page.locator('[role="option"]').filter(has_text=label_month_es_caps).first.click(timeout=1500)
                                    selected_month = True
                            except Exception:
                                pass
                            try:
                                page.keyboard.press('Escape')
                            except Exception:
                                pass
                        except Exception:
                            pass
                except Exception:
                    pass
                try:
                    # Llenar día con robustez (label ES/EN o name)
                    filled_day = False
                    for sel in [
                        lambda: page.get_by_label("Día"),
                        lambda: page.get_by_label("Day"),
                        lambda: page.locator('input[name="day"]')
                    ]:
                        try:
                            el = sel()
                            if el and el.count() > 0:
                                el.first.fill(str(day))
                                filled_day = True
                                break
                        except Exception:
                            pass
                    if not filled_day:
                        try:
                            page.fill('input[name="day"]', str(day))
                        except Exception:
                            pass
                    # Disparar eventos para asegurar validación del día
                    try:
                        page.evaluate("""
                            () => {
                                const el = document.querySelector('input[name=day]');
                                if (!el) return;
                                el.dispatchEvent(new Event('input',{bubbles:true}));
                                el.dispatchEvent(new Event('change',{bubbles:true}));
                                el.dispatchEvent(new Event('blur',{bubbles:true}));
                            }
                        """)
                    except Exception:
                        pass
                except Exception:
                    pass
                try:
                    # Llenar año con robustez (label ES/EN o name)
                    filled_year = False
                    for sel in [
                        lambda: page.get_by_label("Año"),
                        lambda: page.get_by_label("Year"),
                        lambda: page.locator('input[name="year"]')
                    ]:
                        try:
                            el = sel()
                            if el and el.count() > 0:
                                el.first.fill(str(year))
                                filled_year = True
                                break
                        except Exception:
                            pass
                    if not filled_year:
                        try:
                            page.fill('input[name="year"]', str(year))
                        except Exception:
                            pass
                    # Disparar eventos para asegurar validación de año y mes
                    try:
                        page.evaluate("""
                            () => {
                                const fire = (el) => { if(!el) return; el.dispatchEvent(new Event('input',{bubbles:true})); el.dispatchEvent(new Event('change',{bubbles:true})); el.dispatchEvent(new Event('blur',{bubbles:true})); };
                                fire(document.querySelector('input[name=year]'));
                                const monthEl = document.querySelector('[aria-label="Mes"]') || document.querySelector('[aria-label="Month"]') || document.querySelector('select[name=month]');
                                if (monthEl) monthEl.dispatchEvent(new Event('change',{bubbles:true}));
                            }
                        """)
                    except Exception:
                        pass
                except Exception:
                    pass
                # Seleccionar género por etiqueta con fallbacks (ES/EN)
                try:
                    labels_gender = ['Hombre','Masculino','Male'] if gender_flag == 'male' else ['Mujer','Femenino','Female']
                    # Primero intentar combobox por rol/label
                    def try_gender_combo():
                        return select_material_combo([
                            "Género", "Genero", "Sexo", "Sex", "Gender"
                        ], labels_gender + ['1','2'], fallback_select_name="gender")
                    ok_gender = False
                    try:
                        ok_gender = try_gender_combo()
                    except Exception:
                        ok_gender = False
                    if not ok_gender:
                        # Último recurso directo por <select>
                        try:
                            page.wait_for_selector('select[name="gender"]', timeout=2000)
                            for g in labels_gender:
                                try:
                                    page.select_option('select[name="gender"]', label=g)
                                    ok_gender = True
                                    break
                                except Exception:
                                    pass
                            if not ok_gender:
                                page.select_option('select[name="gender"]', '1' if gender_flag == 'male' else '2')
                        except Exception:
                            pass
                    # Fallback adicional: radios (Material) por rol
                    try:
                        if not ok_gender:
                            for g in labels_gender + ['Prefiero no decirlo','Prefer not to say']:
                                chosen = False
                                try:
                                    loc = page.get_by_role('radio', name=g)
                                    if loc and loc.count() > 0:
                                        loc.first.click(timeout=1200)
                                        ok_gender = True
                                        chosen = True
                                except Exception:
                                    pass
                                if chosen:
                                    break
                            # Si no hay rol radio, probar por texto cercano
                            if not ok_gender:
                                for g in labels_gender + ['Prefiero no decirlo','Prefer not to say']:
                                    try:
                                        page.get_by_text(g, exact=False).click(timeout=1000)
                                        ok_gender = True
                                        break
                                    except Exception:
                                        pass
                    except Exception:
                        pass
                    # Disparar change del género para desbloquear validación
                    try:
                        page.evaluate("""
                            () => {
                                const sel = document.querySelector('select[name=gender]');
                                if (sel) sel.dispatchEvent(new Event('change',{bubbles:true}));
                                const checked = document.querySelector('[role="radio"][aria-checked="true"]');
                                if (checked) {
                                    const el = checked;
                                    el.dispatchEvent(new Event('change',{bubbles:true}));
                                    if (typeof el.blur === 'function') el.blur();
                                }
                            }
                        """)
                    except Exception:
                        pass
                except Exception:
                    pass

                # Avanzar a selección de nombre de usuario (más rápido y agresivo)
                try:
                    clicked = False
                    # Pre-habilitar botón y forzar submit si hay formulario
                    try:
                        page.evaluate("""
                            () => {
                                const texts = /Siguiente|Next|Continuar|Continue/i;
                                const btn = Array.from(document.querySelectorAll('button,[role=button]')).find(b => texts.test(b.textContent||''));
                                if (btn) { btn.removeAttribute('disabled'); btn.setAttribute('aria-disabled','false'); }
                                const form = document.querySelector('form');
                                if (form) { form.requestSubmit ? form.requestSubmit() : form.submit(); }
                            }
                        """)
                    except Exception:
                        pass
                    # Disparar eventos y blur en campos de fecha y género para asegurar validación
                    try:
                        page.evaluate("""
                            () => {
                                const sels = [
                                    'input[name=day]', 'input[name="birthday-day"]', 'input[name="birthDay"]', 'input[name="Day"]',
                                    'input[name=year]', 'input[name="birthYear"]', 'input[name="Year"]',
                                    'select[name=month]', 'select[name="birthMonth"]', 'select[name="Month"]',
                                    'input[name=gender]', 'select[name=gender]'
                                ];
                                for (const s of sels) {
                                    const el = document.querySelector(s);
                                    if (!el) continue;
                                    el.dispatchEvent(new Event('input', {bubbles:true}));
                                    el.dispatchEvent(new Event('change', {bubbles:true}));
                                    if (typeof el.blur === 'function') el.blur();
                                }
                            }
                        """)
                    except Exception:
                        pass
                    # 0) Enter inmediato para evitar esperas
                    clicked = False
                    try:
                        page.keyboard.press('Enter')
                    except Exception:
                        pass

                    # 0a) Desplazar y generar clic sintético sobre el botón en main
                    try:
                        page.evaluate("""
                            () => {
                                const texts = /Siguiente|Next|Continuar|Continue/i;
                                let btn = Array.from(document.querySelectorAll('button,[role=button]')).find(b => texts.test(b.textContent||''));
                                btn = btn || document.querySelector('button[type=submit]');
                                if (!btn) return false;
                                try { btn.scrollIntoView({block:'center', inline:'center'}); } catch {}
                                try { btn.focus(); } catch {}
                                const rect = btn.getBoundingClientRect();
                                const base = { bubbles: true, cancelable: true, view: window,
                                               clientX: Math.floor(rect.left + Math.min(10, rect.width/2)),
                                               clientY: Math.floor(rect.top + Math.min(10, rect.height/2)),
                                               button: 0 };
                                const fire = (type) => btn.dispatchEvent(new MouseEvent(type, base));
                                ['pointerover','pointerenter','mousemove','pointerdown','mousedown','pointerup','mouseup','click']
                                    .forEach(fire);
                                return true;
                            }
                        """)
                    except Exception:
                        pass

                    # 1) Botones visibles por rol/nombre con timeouts cortos
                    if not clicked:
                        for name in ["Siguiente","Next","Continuar","Continue"]:
                            try:
                                loc = page.get_by_role("button", name=name)
                                if loc and loc.count() > 0:
                                    btn = loc.first
                                    try:
                                        if btn.is_visible():
                                            btn.click(timeout=600, force=True)
                                            clicked = True
                                            break
                                    except Exception:
                                        pass
                            except Exception:
                                pass

                    # 2) Locators por texto/submit con timeouts cortos
                    if not clicked:
                        try:
                            btn = page.locator('button[type="submit"], [role="button"]:has-text("Siguiente"), [role="button"]:has-text("Next"), button:has-text("Siguiente"), button:has-text("Next"), button:has-text("Continuar"), button:has-text("Continue")').first
                            if btn and btn.count() > 0:
                                btn.click(timeout=600, force=True)
                                clicked = True
                        except Exception:
                            pass

                    # 3) Intentos dentro de iframes del flujo lifecycle/signup/identifier
                    if not clicked:
                        try:
                            target_frames = []
                            for fr in page.frames:
                                try:
                                    uf = (fr.url or "")
                                    if ("accounts.google.com" in uf) and ("signup" in uf or "lifecycle" in uf or "identifier" in uf):
                                        target_frames.append(fr)
                                except Exception:
                                    pass
                            if not target_frames:
                                target_frames = list(page.frames)
                            for fr in target_frames:
                                try:
                                    # Pre-habilitar botón y forzar submit dentro del iframe
                                    try:
                                        fr.evaluate("""
                                            () => {
                                                const texts = /Siguiente|Next|Continuar|Continue/i;
                                                const btn = Array.from(document.querySelectorAll('button,[role=button]')).find(b => texts.test(b.textContent||''));
                                                if (btn) { btn.removeAttribute('disabled'); btn.setAttribute('aria-disabled','false'); }
                                                const form = document.querySelector('form');
                                                if (form) { form.requestSubmit ? form.requestSubmit() : form.submit(); }
                                            }
                                        """)
                                    except Exception:
                                        pass
                                    # Disparar eventos y blur en campos de fecha y género dentro del iframe
                                    try:
                                        fr.evaluate("""
                                            () => {
                                                const sels = [
                                                    'input[name=day]', 'input[name="birthday-day"]', 'input[name="birthDay"]', 'input[name="Day"]',
                                                    'input[name=year]', 'input[name="birthYear"]', 'input[name="Year"]',
                                                    'select[name=month]', 'select[name="birthMonth"]', 'select[name="Month"]',
                                                    'input[name=gender]', 'select[name=gender]'
                                                ];
                                                for (const s of sels) {
                                                    const el = document.querySelector(s);
                                                    if (!el) continue;
                                                    el.dispatchEvent(new Event('input', {bubbles:true}));
                                                    el.dispatchEvent(new Event('change', {bubbles:true}));
                                                    if (typeof el.blur === 'function') el.blur();
                                                }
                                            }
                                        """)
                                    except Exception:
                                        pass
                                    # 3a) Click por rol/nombre
                                    done = False
                                    for name in ["Siguiente","Next","Continuar","Continue"]:
                                        try:
                                            loc = fr.get_by_role("button", name=name)
                                            if loc and loc.count() > 0:
                                                el = loc.first
                                                try:
                                                    if el.is_visible() and el.is_enabled():
                                                        el.click(timeout=600, force=True)
                                                        clicked = True
                                                        done = True
                                                        break
                                                except Exception:
                                                    pass
                                        except Exception:
                                            pass
                                    if clicked:
                                        break
                                    if not done:
                                        # 3b) Submit genérico
                                        try:
                                            fr.click('button[type="submit"]', timeout=600, force=True)
                                            clicked = True
                                            break
                                        except Exception:
                                            pass
                                    if not clicked:
                                        # 3c) JS directo (incluye requestSubmit)
                                        try:
                                            fr.evaluate("""
                                                () => {
                                                    const texts = /Siguiente|Next|Continuar|Continue/i;
                                                    let cand = Array.from(document.querySelectorAll('button,[role=button]')).find(b => texts.test(b.textContent||''));
                                                    cand = cand || document.querySelector('button[type=submit]');
                                                    if (cand) { cand.click(); return true; }
                                                    const year = document.querySelector('input[name=year]');
                                                    if (year) { year.dispatchEvent(new KeyboardEvent('keydown', {key:'Enter'})); }
                                                    const form = document.querySelector('form');
                                                    if (form) { form.requestSubmit ? form.requestSubmit() : form.submit(); return true; }
                                                    return false;
                                                }
                                            """)
                                            clicked = True
                                            break
                                        except Exception:
                                            pass
                                    if not clicked:
                                        # 3d) Clic sintético con desplazamiento dentro del iframe
                                        try:
                                            fr.evaluate("""
                                                () => {
                                                    const texts = /Siguiente|Next|Continuar|Continue/i;
                                                    let btn = Array.from(document.querySelectorAll('button,[role=button]')).find(b => texts.test(b.textContent||''));
                                                    btn = btn || document.querySelector('button[type=submit]');
                                                    if (!btn) return false;
                                                    try { btn.scrollIntoView({block:'center', inline:'center'}); } catch {}
                                                    try { btn.focus(); } catch {}
                                                    const rect = btn.getBoundingClientRect();
                                                    const base = { bubbles: true, cancelable: true, view: window,
                                                                   clientX: Math.floor(rect.left + Math.min(10, rect.width/2)),
                                                                   clientY: Math.floor(rect.top + Math.min(10, rect.height/2)),
                                                                   button: 0 };
                                                    const fire = (type) => btn.dispatchEvent(new MouseEvent(type, base));
                                                    ['pointerover','pointerenter','mousemove','pointerdown','mousedown','pointerup','mouseup','click']
                                                        .forEach(fire);
                                                    return true;
                                                }
                                            """)
                                            clicked = True
                                            break
                                        except Exception:
                                            pass
                                except Exception:
                                    pass
                        except Exception:
                            pass

                    # 4) Último recurso: Enter nuevamente
                    if not clicked:
                        try:
                            page.keyboard.press('Enter')
                            clicked = True
                        except Exception:
                            pass
                except Exception:
                    pass

                try:
                    page.wait_for_selector('input[name="Username"], [role="listbox"] [role="option"], input[name="Passwd"]', timeout=5000)
                except Exception:
                    pass

                try:
                    # Intentar seleccionar una opción sugerida en listbox
                    suggest_locator = page.locator('[role="listbox"] [role="option"]').first
                    if suggest_locator and suggest_locator.count() > 0:
                        try:
                            text = suggest_locator.inner_text().strip()
                        except Exception:
                            text = ""
                        if text:
                            chosen_email = text if '@' in text else f"{text}@gmail.com"
                        try:
                            suggest_locator.click(timeout=2000)
                        except Exception:
                            pass
                    else:
                        # Manejo alternativo: sugerencias estilo radio
                        radio = None
                        try:
                            cand = page.locator('[role="radio"]').first
                            if cand and cand.count() > 0:
                                radio = cand
                        except Exception:
                            pass
                        if (not radio) or (radio and radio.count() == 0):
                            try:
                                cand2 = page.locator('input[type="radio"]').first
                                if cand2 and cand2.count() > 0:
                                    radio = cand2
                            except Exception:
                                pass

                        email_text = None
                        if radio and radio.count() > 0:
                            try:
                                email_text = radio.inner_text().strip()
                            except Exception:
                                pass
                            if not email_text:
                                try:
                                    sib = radio.locator('xpath=./ancestor::*[1]//span | ./following-sibling::*[1]').first
                                    if sib and sib.count() > 0:
                                        email_text = sib.inner_text().strip()
                                except Exception:
                                    pass
                            try:
                                radio.click(timeout=2000)
                            except Exception:
                                pass

                        if email_text:
                            try:
                                import re
                                m = re.search(r"[\w\.\-\+]+@gmail\.com", email_text)
                                if m:
                                    chosen_email = m.group(0)
                                else:
                                    txt = (email_text.split()[-1] if email_text.split() else email_text)
                                    chosen_email = txt if ('@' in txt and 'gmail.com' in txt) else (f"{txt}@gmail.com" if txt else None)
                            except Exception:
                                pass

                        if not chosen_email:
                            # Fallback: crear dirección propia con nombre+apellido+5 cifras
                            base = f"{first}{last}".lower().replace("á","a").replace("é","e").replace("í","i").replace("ó","o").replace("ú","u").replace("ñ","n").replace(" ","")
                            digits = f"{random.randint(10000,99999)}"
                            handle = f"{base}{digits}"
                            chosen_email = f"{handle}@gmail.com"
                            try:
                                page.fill('input[name="Username"]', handle)
                            except Exception:
                                pass
                except Exception:
                    pass

                # Si estamos en login, no escribir correo ahí: ir a "Crear cuenta"; si estamos en signup, rellenar Username y avanzar
                try:
                    is_login = ("signin" in (page.url or "")) or (page.locator('input[name="identifier"]').count() > 0)
                except Exception:
                    is_login = False

                try:
                    if is_login:
                        # Ir a crear cuenta desde la pantalla de login
                        try:
                            for txt in ["Crear cuenta", "Crear una cuenta", "Create account", "Create a account", "Create new account"]:
                                for role in ["button", "link"]:
                                    try:
                                        page.get_by_role(role, name=txt).click(timeout=2000)
                                        break
                                    except Exception:
                                        pass
                            # Fallback por texto directo
                            try:
                                page.get_by_text("Crear cuenta", exact=False).click(timeout=2000)
                            except Exception:
                                pass
                            # Fallback por enlaces hacia signup
                            try:
                                page.locator('a[href*="signup"], a[href*="createaccount"]').first.click(timeout=2000)
                            except Exception:
                                pass
                            for opt in ["Para mí", "For myself"]:
                                try:
                                    page.get_by_text(opt, exact=False).click(timeout=1500)
                                    break
                                except Exception:
                                    pass
                            # Caso especial: en algunos países Google muestra el paso "Crea una dirección de Gmail" dentro de la ruta de login.
                            # Si detectamos ese encabezado, rellenamos el campo "identifier"/"identifierId" como Username y avanzamos.
                            try:
                                import re
                                special_heading = False
                                show_gmail_suffix = False
                                try:
                                    if page.get_by_role("heading", name=re.compile("Crea una dirección de Gmail|Choose your Gmail address|Cómo acceder", re.I)).count() > 0:
                                        special_heading = True
                                except Exception:
                                    pass
                                try:
                                    if page.get_by_text("Crea una dirección de Gmail", exact=False).count() > 0:
                                        special_heading = True
                                except Exception:
                                    pass
                                try:
                                    if "lifecycle/steps/signup/username" in (page.url or ""):
                                        special_heading = True
                                except Exception:
                                    pass
                                try:
                                    # Heurística: en el paso de username aparece el sufijo @gmail.com cerca del input
                                    if page.get_by_text("@gmail.com", exact=False).count() > 0:
                                        show_gmail_suffix = True
                                except Exception:
                                    pass

                                if special_heading or show_gmail_suffix:
                                    base = f"{first}{last}".lower().replace("á","a").replace("é","e").replace("í","i").replace("ó","o").replace("ú","u").replace("ñ","n").replace(" ","")
                                    digits = f"{random.randint(10000,99999)}"
                                    handle = f"{base}{digits}"
                                    chosen_email = f"{handle}@gmail.com"

                                    id_selectors = [
                                        "input#identifierId",
                                        "input[name='identifier']",
                                        "input[aria-label*='usuario']",
                                        "input[aria-label*='Username']",
                                        "input[type='email']",
                                        "input[type='text']",
                                    ]
                                    filled_id = False
                                    # Intento en página principal
                                    try_count = 0
                                    while (not filled_id) and try_count < 12:
                                        try_count += 1
                                        for sel in id_selectors:
                                            try:
                                                loc = page.locator(sel)
                                                c = loc.count()
                                                if c > 0:
                                                    # Elegir el primer candidato visible/habilitado
                                                    idx = 0
                                                    while idx < c:
                                                        cand = loc.nth(idx)
                                                        try:
                                                            if cand.is_visible() and cand.is_enabled():
                                                                try:
                                                                    cand.click(timeout=1200)
                                                                except Exception:
                                                                    pass
                                                                try:
                                                                    cand.press("Control+A")
                                                                except Exception:
                                                                    pass
                                                                try:
                                                                    cand.fill("")
                                                                except Exception:
                                                                    pass
                                                                cand.type(handle, delay=20)
                                                                filled_id = True
                                                                break
                                                        except Exception:
                                                            pass
                                                        idx += 1
                                                    if filled_id:
                                                        break
                                            except Exception:
                                                pass
                                        if not filled_id:
                                            page.wait_for_timeout(250)

                                    # Intento dentro de iframes si no se pudo
                                    if not filled_id:
                                        try:
                                            target_frame = None
                                            for fr in page.frames:
                                                try:
                                                    urlf = (fr.url or "")
                                                    if ("accounts.google.com" in urlf) and ("signup" in urlf or "lifecycle" in urlf or "identifier" in urlf):
                                                        target_frame = fr
                                                        break
                                                except Exception:
                                                    pass
                                            frames_to_check = [f for f in [target_frame] if f] or list(page.frames)
                                            for fr in frames_to_check:
                                                try:
                                                    for sel in id_selectors:
                                                        loc = fr.locator(sel)
                                                        c = loc.count()
                                                        if c > 0:
                                                            idx = 0
                                                            while idx < c:
                                                                cand = loc.nth(idx)
                                                                try:
                                                                    if cand.is_visible() and cand.is_enabled():
                                                                        try:
                                                                            cand.click(timeout=1200)
                                                                        except Exception:
                                                                            pass
                                                                        try:
                                                                            cand.press("Control+A")
                                                                        except Exception:
                                                                            pass
                                                                        try:
                                                                            cand.fill("")
                                                                        except Exception:
                                                                            pass
                                                                        cand.type(handle, delay=20)
                                                                        filled_id = True
                                                                        break
                                                                except Exception:
                                                                    pass
                                                                idx += 1
                                                            if filled_id:
                                                                break
                                                    if filled_id:
                                                        break
                                                except Exception:
                                                    pass
                                        except Exception:
                                            pass

                                    # Seleccionar primera sugerencia si aparece
                                    try:
                                        sug = page.locator('[role="listbox"] [role="option"], li[role="option"]').first
                                        if sug and sug.count() > 0:
                                            try:
                                                sug.click(timeout=1500)
                                            except Exception:
                                                pass
                                    except Exception:
                                        pass

                                    # Avanzar: botón "Siguiente/Continuar" o Enter como respaldo
                                    try:
                                        clicked = False
                                        for name in ["Siguiente","Next","Continuar","Continue"]:
                                            try:
                                                btn = page.get_by_role("button", name=name)
                                                if btn and btn.count() > 0:
                                                    # Intentar el primero visible
                                                    c = btn.count()
                                                    i = 0
                                                    while i < c:
                                                        b = btn.nth(i)
                                                        try:
                                                            if b.is_visible() and not (b.get_attribute("disabled") is not None):
                                                                b.click(timeout=2500)
                                                                clicked = True
                                                                break
                                                        except Exception:
                                                            pass
                                                        i += 1
                                                    if clicked:
                                                        break
                                            except Exception:
                                                pass
                                        if not clicked:
                                            try:
                                                page.click('#identifierNext button', timeout=2000)
                                                clicked = True
                                            except Exception:
                                                pass
                                        # Intentar dentro de iframes
                                        if not clicked:
                                            try:
                                                for fr in page.frames:
                                                    try:
                                                        for name in ["Siguiente","Next","Continuar","Continue"]:
                                                            try:
                                                                btn = fr.get_by_role("button", name=name)
                                                                if btn and btn.count() > 0:
                                                                    c = btn.count()
                                                                    i = 0
                                                                    while i < c:
                                                                        b = btn.nth(i)
                                                                        try:
                                                                            if b.is_visible() and not (b.get_attribute("disabled") is not None):
                                                                                b.click(timeout=2000)
                                                                                clicked = True
                                                                                break
                                                                        except Exception:
                                                                            pass
                                                                        i += 1
                                                                    if clicked:
                                                                        break
                                                            except Exception:
                                                                pass
                                                        if clicked:
                                                            break
                                                    except Exception:
                                                        pass
                                            except Exception:
                                                pass
                                        if not clicked:
                                            page.keyboard.press("Enter")
                                    except Exception:
                                        pass
                            except Exception:
                                pass
                        except Exception:
                            pass
                    else:
                        # En el formulario de creación: rellenar Username si se solicita (incluye iframes del flujo lifecycle)
                        try:
                            base = f"{first}{last}".lower().replace("á","a").replace("é","e").replace("í","i").replace("ó","o").replace("ú","u").replace("ñ","n").replace(" ","")
                            digits = f"{random.randint(10000,99999)}"
                            handle = f"{base}{digits}"
                            chosen_email = f"{handle}@gmail.com"

                            filled = False
                            selectors = [
                                'input[name="Username"]',
                                'input#username',
                                'input[name="username"]',
                                'input[aria-label*="usuario"]',
                                'input[aria-label*="Username"]',
                                'input[autocomplete="username"]',
                            ]

                            # 1) Intento directo en la página, con espera/reintentos
                            try_count = 0
                            while (not filled) and try_count < 12:  # ~12*250ms = 3s
                                try_count += 1
                                for sel in selectors:
                                    try:
                                        loc = page.locator(sel)
                                        if loc.count() > 0:
                                            el = loc.first
                                            try:
                                                el.click(timeout=1500)
                                            except Exception:
                                                pass
                                            # Escribir con type para disparar eventos
                                            try:
                                                el.clear()
                                            except Exception:
                                                pass
                                            el.type(handle, delay=20)
                                            filled = True
                                            break
                                    except Exception:
                                        pass
                                if not filled:
                                    try:
                                        page.get_by_label("Nombre de usuario", exact=False).type(handle, delay=20)
                                        filled = True
                                    except Exception:
                                        pass
                                if not filled:
                                    try:
                                        import re
                                        page.get_by_role("textbox", name=re.compile("Nombre de usuario|Username|Dirección de Gmail|Gmail address", re.I)).type(handle, delay=20)
                                        filled = True
                                    except Exception:
                                        pass
                                if not filled:
                                    page.wait_for_timeout(250)

                            # 2) Si no se pudo, buscar dentro de iframes (nuevo flujo lifecycle)
                            if not filled:
                                try:
                                    target_frame = None
                                    for fr in page.frames:
                                        try:
                                            urlf = (fr.url or "")
                                            if ("accounts.google.com" in urlf) and ("signup" in urlf or "lifecycle" in urlf):
                                                target_frame = fr
                                                break
                                        except Exception:
                                            pass
                                    frames_to_check = [f for f in [target_frame] if f] or list(page.frames)
                                    for fr in frames_to_check:
                                        try:
                                            for sel in selectors:
                                                loc = fr.locator(sel)
                                                if loc.count() > 0:
                                                    el = loc.first
                                                    try:
                                                        el.click(timeout=1500)
                                                    except Exception:
                                                        pass
                                                    try:
                                                        el.clear()
                                                    except Exception:
                                                        pass
                                                    el.type(handle, delay=20)
                                                    try:
                                                        el.focus()
                                                    except Exception:
                                                        pass
                                                    filled = True
                                                    break
                                            if filled:
                                                break
                                            # Por etiqueta accesible
                                            try:
                                                loc2 = fr.get_by_label("Nombre de usuario", exact=False)
                                                if loc2.count() > 0:
                                                    el2 = loc2.first
                                                    try:
                                                        el2.click(timeout=1500)
                                                    except Exception:
                                                        pass
                                                    try:
                                                        el2.clear()
                                                    except Exception:
                                                        pass
                                                    el2.type(handle, delay=20)
                                                    try:
                                                        el2.focus()
                                                    except Exception:
                                                        pass
                                                    filled = True
                                            except Exception:
                                                pass
                                            # Fallback muy agresivo: primer input de texto del frame
                                            if not filled:
                                                try:
                                                    any_input = fr.locator('input[type="text"], input:not([type])').first
                                                    if any_input and any_input.count() > 0:
                                                        try:
                                                            any_input.click(timeout=1500)
                                                        except Exception:
                                                            pass
                                                        try:
                                                            any_input.clear()
                                                        except Exception:
                                                            pass
                                                        any_input.type(handle, delay=20)
                                                        try:
                                                            any_input.focus()
                                                        except Exception:
                                                            pass
                                                        filled = True
                                                except Exception:
                                                    pass
                                        except Exception:
                                            pass
                                except Exception:
                                    pass

                            # 2b) Si aparecen sugerencias dentro del mismo paso, elegir la primera
                            try:
                                if filled:
                                    # Buscar listbox/option en página
                                    opts = page.locator('[role="listbox"] [role="option"]')
                                    if opts.count() > 0:
                                        try:
                                            chosen_email = (opts.first.inner_text() or '').strip()
                                        except Exception:
                                            pass
                                        try:
                                            opts.first.click(timeout=1500)
                                        except Exception:
                                            pass
                                    else:
                                        # Buscar en iframes
                                        for fr in page.frames:
                                            try:
                                                fr_opts = fr.locator('[role="listbox"] [role="option"]')
                                                if fr_opts.count() > 0:
                                                    try:
                                                        chosen_email = (fr_opts.first.inner_text() or '').strip()
                                                    except Exception:
                                                        pass
                                                    try:
                                                        fr_opts.first.click(timeout=1500)
                                                    except Exception:
                                                        pass
                                                    break
                                            except Exception:
                                                pass
                            except Exception:
                                pass

                            # Avanzar con Siguiente (página o iframe)
                            clicked = False
                            for name in ["Siguiente","Next","Continuar","Continue"]:
                                try:
                                    page.get_by_role("button", name=name).click(timeout=2500)
                                    clicked = True
                                    break
                                except Exception:
                                    pass
                            if not clicked:
                                try:
                                    page.locator('xpath=//span[contains(text(),"Siguiente") or contains(text(),"Next") or contains(text(),"Continuar") or contains(text(),"Continue")]/ancestor::button').first.click(timeout=2500, force=True)
                                    clicked = True
                                except Exception:
                                    pass
                            if not clicked:
                                try:
                                    page.click('button[type="submit"]', timeout=2500, force=True)
                                    clicked = True
                                except Exception:
                                    pass
                            if not clicked:
                                # Click dentro de iframes
                                try:
                                    for fr in page.frames:
                                        try:
                                            for name in ["Siguiente","Next","Continuar","Continue"]:
                                                try:
                                                    fr.get_by_role("button", name=name).click(timeout=2000)
                                                    clicked = True
                                                    break
                                                except Exception:
                                                    pass
                                            if clicked:
                                                break
                                        except Exception:
                                            pass
                                except Exception:
                                    pass
                            # Fallback de envío por teclado si el botón no responde
                            try:
                                if filled and not clicked:
                                    page.keyboard.press("Enter")
                            except Exception:
                                pass
                        except Exception:
                            pass
                except Exception:
                    pass

                # Establecer contraseña (dos campos iguales) con eventos y soporte iframe
                try:
                    if password:
                        # Rellenar en la página principal: todos los inputs de tipo password
                        try:
                            loc = page.locator('input[type="password"], input[name="Passwd"], input[name="ConfirmPasswd"]')
                            cnt = loc.count()
                            if cnt > 0:
                                for k in range(cnt):
                                    try:
                                        el = loc.nth(k)
                                        el.fill(password)
                                    except Exception:
                                        pass
                            else:
                                # Fallback por etiquetas accesibles
                                try:
                                    page.get_by_label('Contraseña', exact=False).fill(password)
                                except Exception:
                                    pass
                                try:
                                    page.get_by_label('Confirmar', exact=False).fill(password)
                                except Exception:
                                    pass
                        except Exception:
                            pass
                        # Disparar validación y blur en los campos
                        try:
                            page.evaluate("""
                                (pwd) => {
                                  const inputs = Array.from(document.querySelectorAll('input[type=password], input[name=Passwd], input[name=ConfirmPasswd]'));
                                  for (const el of inputs) {
                                    try { el.value = pwd; } catch{}
                                    el.dispatchEvent(new Event('input', {bubbles:true}));
                                    el.dispatchEvent(new Event('change', {bubbles:true}));
                                    if (typeof el.blur === 'function') el.blur();
                                  }
                                }
                            """, password)
                        except Exception:
                            pass
                        # Intentar dentro de iframes relevantes
                        try:
                            target_frames = []
                            for fr in page.frames:
                                try:
                                    uf = (fr.url or '')
                                    if ('accounts.google.com' in uf) and ('signup' in uf or 'lifecycle' in uf or 'identifier' in uf):
                                        target_frames.append(fr)
                                except Exception:
                                    pass
                            if not target_frames:
                                target_frames = list(page.frames)
                            for fr in target_frames:
                                try:
                                    # Rellenar ambos campos password si existen
                                    try:
                                        locf = fr.locator('input[type="password"], input[name="Passwd"], input[name="ConfirmPasswd"]')
                                        c2 = locf.count()
                                        if c2 > 0:
                                            for j in range(c2):
                                                try:
                                                    lf = locf.nth(j)
                                                    lf.fill(password)
                                                except Exception:
                                                    pass
                                        else:
                                            try:
                                                fr.get_by_label('Contraseña', exact=False).fill(password)
                                            except Exception:
                                                pass
                                            try:
                                                fr.get_by_label('Confirmar', exact=False).fill(password)
                                            except Exception:
                                                pass
                                    except Exception:
                                        pass
                                    # Disparar validación y blur
                                    try:
                                        fr.evaluate("""
                                            (pwd) => {
                                              const inputs = Array.from(document.querySelectorAll('input[type=password], input[name=Passwd], input[name=ConfirmPasswd]'));
                                              for (const el of inputs) {
                                                try { el.value = pwd; } catch{}
                                                el.dispatchEvent(new Event('input', {bubbles:true}));
                                                el.dispatchEvent(new Event('change', {bubbles:true}));
                                                if (typeof el.blur === 'function') el.blur();
                                              }
                                            }
                                        """, password)
                                    except Exception:
                                        pass
                                except Exception:
                                    pass
                        except Exception:
                            pass
                except Exception:
                    pass

                # Avanzar tras contraseña (incluye iframes y clic sintético)
                try:
                    clicked = False
                    for name in ["Siguiente","Next","Continuar","Continue"]:
                        try:
                            page.get_by_role("button", name=name).click(timeout=2000)
                            clicked = True
                            break
                        except Exception:
                            pass
                    if not clicked:
                        try:
                            page.click('button[type="submit"]', timeout=2000)
                            clicked = True
                        except Exception:
                            pass
                    if not clicked:
                        # Intentar en iframes
                        try:
                            for fr in page.frames:
                                try:
                                    done = False
                                    for name in ["Siguiente","Next","Continuar","Continue"]:
                                        try:
                                            fr.get_by_role("button", name=name).click(timeout=2000)
                                            clicked = True
                                            done = True
                                            break
                                        except Exception:
                                            pass
                                    if done:
                                        break
                                    if not clicked:
                                        try:
                                            fr.click('button[type="submit"]', timeout=2000)
                                            clicked = True
                                            break
                                        except Exception:
                                            pass
                                except Exception:
                                    pass
                        except Exception:
                            pass
                    if not clicked:
                        # Clic sintético con scroll/focus
                        try:
                            page.evaluate("""
                                () => {
                                  const texts = /Siguiente|Next|Continuar|Continue/i;
                                  let btn = Array.from(document.querySelectorAll('button,[role=button]')).find(b => texts.test(b.textContent||''));
                                  btn = btn || document.querySelector('button[type=submit]');
                                  if (!btn) return false;
                                  try { btn.scrollIntoView({block:'center', inline:'center'}); } catch {}
                                  try { btn.focus(); } catch {}
                                  const r = btn.getBoundingClientRect();
                                  const base = { bubbles:true, cancelable:true, view:window,
                                                 clientX: Math.floor(r.left + Math.min(10, r.width/2)),
                                                 clientY: Math.floor(r.top + Math.min(10, r.height/2)),
                                                 button:0 };
                                  const fire = (t) => btn.dispatchEvent(new MouseEvent(t, base));
                                  ['pointerover','pointerenter','mousemove','pointerdown','mousedown','pointerup','mouseup','click'].forEach(fire);
                                  return true;
                                }
                            """)
                            clicked = True
                        except Exception:
                            pass
                    if not clicked:
                        try:
                            page.keyboard.press('Enter')
                        except Exception:
                            pass
                except Exception:
                    pass

                # Intentar omitir correo de recuperación
                try:
                    for txt in ["Omitir", "Skip"]:
                        try:
                            page.get_by_text(txt, exact=False).click(timeout=2000)
                            break
                        except Exception:
                            pass
                except Exception:
                    pass

                # Aceptar nombre/email sugerido si se requiere confirmación
                try:
                    for txt in ["Sí", "Yes", "Aceptar", "Accept"]:
                        try:
                            page.get_by_role("button", name=txt).click(timeout=2000)
                            break
                        except Exception:
                            pass
                except Exception:
                    pass

                # Aceptar términos y condiciones (si aparece)
                try:
                    for txt in ["Acepto", "I agree", "Estoy de acuerdo"]:
                        try:
                            page.get_by_role("button", name=txt).click(timeout=2500)
                            break
                        except Exception:
                            pass
                except Exception:
                    pass

                try:
                    clicked = False
                    for name in ["Siguiente","Next"]:
                        try:
                            page.get_by_role("button", name=name).click(timeout=3000)
                            clicked = True
                            break
                        except Exception:
                            pass
                    if not clicked:
                        page.click('button[type="submit"]', timeout=3000)
                except Exception:
                    pass

                page.wait_for_timeout(1000)

                screenshot_path = os.path.join(artifacts_dir, f"google_sim_{i}.png")
                try:
                    page.screenshot(path=screenshot_path, full_page=True)
                except Exception:
                    pass

                try:
                    needs_phone = page.locator('input[name="phoneNumber"]').count() > 0
                except Exception:
                    needs_phone = False
                try:
                    captcha_present = page.locator('[role="presentation"] canvas').count() > 0
                except Exception:
                    captcha_present = False

                if needs_phone or captcha_present:
                    status = "verification_required"
                else:
                    status = "attempted"

            except Exception as e:
                status = "error"
                error = str(e)
            finally:
                try:
                    page.close()
                except Exception:
                    pass
                try:
                    context.close()
                except Exception:
                    pass

            items.append({
                "index": i,
                "status": status,
                "email": chosen_email,
                "password": password if chosen_email else None,
                "screenshot": screenshot_path,
                "elapsed_ms": int((time.time() - started) * 1000),
                "vpn_provider": vpn_provider if vpn is not None else None,
                "vpn_country": vpn_country if vpn is not None else None,
                "error": error,
            })

        try:
            browser.close()
        except Exception:
            pass
        # Cerrar proceso externo si conectamos por CDP
        try:
            ext = getattr(browser, "_external_proc", None)
            if ext:
                ext.terminate()
        except Exception:
            pass

    return {"status": "completed", "items": items}


def create_google_accounts(
    count: int = 1,
    incognito: bool = True,
    rotate_vpn_per_account: bool = False,
    vpn_provider: Optional[str] = None,
    vpn_country: Optional[str] = None,
    vpn_servers: Optional[List[str]] = None,
    vpn_wait_ms: int = 5000,
    password: str = "",
) -> Dict:
    """
    Versión mínima solicitada: solo abrir una ventana incógnito y navegar a
    https://www.google.com dentro de esa misma ventana. No realiza ninguna
    otra acción.
    """
    ensure_artifacts_dir()

    items = []

    with sync_playwright() as p:
        browser_type = p.chromium

        def launch_browser():
            """Intenta abrir una ventana visible en modo incógnito.

            1) Preferencia: Chrome real vía CDP conectando al puerto (incógnito visible).
            2) Fallback: lanzar Chrome/MS Edge con args de incógnito directamente desde Playwright.
            3) Último recurso: Chromium headful (sin garantía de "incognito", pero visible).
            """
            args_incog = ["--incognito", "--new-window", "--no-first-run", "--no-default-browser-check"]
            # 1) Chrome vía CDP
            try:
                ports = [int(os.environ.get("CHROME_REMOTE_DEBUG_PORT", "9223")), 9222, 9333]
                script_cmd = os.path.join(os.getcwd(), "chrome_incognito.cmd")
                proc = None
                for port in ports:
                    try:
                        if os.path.exists(script_cmd):
                            print(f"[incognito][cdp] Usando script local: {script_cmd} puerto {port}")
                            proc = subprocess.Popen([script_cmd, str(port), "https://www.google.com"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        else:
                            chrome_exe = find_chrome_executable()
                            if chrome_exe:
                                cmd = [
                                    chrome_exe,
                                    f"--remote-debugging-port={port}",
                                    "--new-window",
                                    "--incognito",
                                    "--no-first-run",
                                    "--no-default-browser-check",
                                    "--disable-extensions",
                                    "--disable-background-networking",
                                    "--disable-sync",
                                    "--disable-component-update",
                                ]
                                tmp_profile = os.path.join(os.getcwd(), "chrome_profile")
                                try:
                                    os.makedirs(tmp_profile, exist_ok=True)
                                except Exception:
                                    pass
                                cmd.append(f"--user-data-dir={tmp_profile}")
                                cmd.append("https://www.google.com")
                                print(f"[incognito][cdp] Lanzando Chrome en {chrome_exe} con puerto {port} …")
                                proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        time.sleep(0.6)
                        if proc:
                            for _ in range(10):
                                try:
                                    browser = p.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
                                    print("[incognito][cdp] Conectado a Chrome por CDP en modo incógnito.")
                                    browser.__dict__["_external_proc"] = proc
                                    return browser
                                except Exception:
                                    time.sleep(0.4)
                            # Si no conectó, termina y prueba con siguiente puerto
                            try:
                                proc.terminate()
                            except Exception:
                                pass
                    except Exception:
                        pass
            except Exception as e0:
                print(f"[incognito][cdp] Error al lanzar/conectar CDP: {e0}")

            # 2) Fallback directo: lanzar Chrome/Edge con incógnito desde Playwright (visible)
            try:
                try:
                    browser = browser_type.launch(headless=False, channel="chrome", args=args_incog)
                    print("[incognito][fallback] Lanzado Chrome via channel con incógnito.")
                    return browser
                except Exception:
                    chrome_exe = find_chrome_executable()
                    if chrome_exe:
                        try:
                            browser = browser_type.launch(headless=False, executable_path=chrome_exe, args=args_incog)
                            print("[incognito][fallback] Lanzado Chrome por ruta ejecutable con incógnito.")
                            return browser
                        except Exception:
                            pass
                    try:
                        browser = browser_type.launch(headless=False, channel="msedge", args=["--inprivate", "--new-window"])
                        print("[incognito][fallback] Lanzado Edge en modo InPrivate.")
                        return browser
                    except Exception:
                        pass
            except Exception:
                pass

            # 3) Último recurso: Chromium visible sin incógnito
            print("[incognito][fallback] Abriendo Chromium headful (no incógnito).")
            return browser_type.launch(headless=False)

        browser = launch_browser()

        try:
            # Reutilizar la MISMA ventana/pestaña incógnito: usar la página existente si está.
            started = time.time()
            status = "in_progress"
            error = None
            email_chosen = None
            screenshot_path = os.path.join(artifacts_dir, "google_incognito_open.png")
            try:
                context = browser.contexts[0] if getattr(browser, "contexts", None) else None
            except Exception:
                context = None
            page = None
            try:
                if context:
                    pages = context.pages if hasattr(context, 'pages') else []
                    if pages:
                        page = pages[0]
                if page is None and context is not None:
                    page = context.new_page()
            except Exception as e_page:
                error = f"No se pudo obtener/crear página: {e_page}"

            if page:
                try:
                    # Timeouts más estables
                    page.set_default_navigation_timeout(8000)
                    page.set_default_timeout(8000)
                except Exception:
                    pass

                # Bloquear recursos pesados para acelerar (imágenes/estilos/fuentes)
                try:
                    def _fast_route(route):
                        rt = route.request.resource_type
                        # Solo bloquear imágenes; permitir CSS y fuentes para evitar fallos de UI
                        if rt in ["image"]:
                            route.abort()
                        else:
                            route.continue_()
                    page.route("**/*", _fast_route)
                except Exception:
                    pass

                # Aceptar consentimientos comunes si aparecen
                try:
                    for txt in [
                        "Aceptar todo","Rechazar todo","Aceptar todas","Reject all",
                        "Agree","I agree","Accept","Accept all"
                    ]:
                        try:
                            page.get_by_text(txt, exact=False).click(timeout=1500)
                        except Exception:
                            pass
                except Exception:
                    pass

                # Traer al frente por si hay varias ventanas
                try:
                    page.bring_to_front()
                except Exception:
                    pass

                # Navegar al formulario de alta
                # Prioridad: ir directo a signup (sin escribir correo)
                fast_ok = False
                # Secuencia agresiva de URLs de alta para llegar más rápido
                try:
                    candidate_urls = [
                        "https://accounts.google.com/signup/v2/createaccount?service=mail&hl=es",
                        "https://accounts.google.com/signup/v2/createaccount?service=mail&hl=es-419",
                        "https://accounts.google.com/signup/v2/webcreateaccount?flowName=GlifWebSignIn&flowEntry=SignUp&hl=es",
                        "https://accounts.google.com/signup/v2/webcreateaccount?flowName=GlifWebSignIn&flowEntry=SignUp&hl=en",
                    ]
                    for u in candidate_urls:
                        try:
                            page.goto(u, wait_until="domcontentloaded", timeout=8000)
                            try:
                                page.wait_for_url(lambda x: ("signup" in (x or "")) or ("webcreateaccount" in (x or "")), timeout=3000)
                            except Exception:
                                pass
                            # Adoptar nueva pestaña si la navegación abrió popup
                            try:
                                curr_pages = list(context.pages) if hasattr(context, 'pages') else []
                                if curr_pages and page not in curr_pages:
                                    page = curr_pages[-1]
                                    page.bring_to_front()
                                    page.set_default_navigation_timeout(8000)
                                    page.set_default_timeout(8000)
                            except Exception:
                                pass
                            if ("signup" in (page.url or "")) or ("webcreateaccount" in (page.url or "")):
                                fast_ok = True
                                break
                        except Exception:
                            pass
                    # Empuje final por JavaScript si la URL no cambió
                    if not fast_ok:
                        try:
                            page.evaluate("location.href='https://accounts.google.com/signup/v2/createaccount?service=mail&hl=es'")
                            try:
                                page.wait_for_url(lambda x: ("signup" in (x or "")) or ("webcreateaccount" in (x or "")), timeout=6000)
                            except Exception:
                                pass
                            fast_ok = ("signup" in (page.url or "")) or ("webcreateaccount" in (page.url or ""))
                        except Exception:
                            pass
                except Exception:
                    pass
                # Si estamos en la home de Google, intentar pulsar "Acceder"/"Iniciar sesión" para llegar al flujo
                try:
                    if (not fast_ok) and ("google.com" in (page.url or "")) and ("accounts.google.com" not in (page.url or "")):
                        for name in ["Acceder", "Iniciar sesión", "Sign in", "SIGN IN", "ACCEDER"]:
                            for role in ["link", "button"]:
                                try:
                                    page.get_by_role(role, name=name).click(timeout=2500)
                                    break
                                except Exception:
                                    pass
                        # Fallback por texto libre
                        try:
                            page.get_by_text("Acceder", exact=False).click(timeout=2500)
                        except Exception:
                            pass
                        try:
                            page.wait_for_url(lambda u: ("accounts.google.com" in (u or "")) or ("signin" in (u or "")), timeout=4500)
                        except Exception:
                            pass
                except Exception:
                    pass
                # Fallback adicional: intentar desde Gmail
                try:
                    if not fast_ok and ("signup" not in (page.url or "")) and ("webcreateaccount" not in (page.url or "")):
                        for gm in [
                            "https://www.gmail.com/",
                            "https://mail.google.com/",
                        ]:
                            try:
                                page.goto(gm, wait_until="domcontentloaded", timeout=7000)
                                # Cerrar posibles consentimientos
                                for txt in ["Aceptar", "Aceptar todo", "Accept", "Agree", "Reject all", "Rechazar"]:
                                    try:
                                        page.get_by_text(txt, exact=False).click(timeout=1000)
                                    except Exception:
                                        pass
                                # Click en "Crear cuenta" y elegir "Para mí/uso personal"
                                clicked_create = False
                                for txt in ["Crear cuenta","Create account","Crear una cuenta","Create new account"]:
                                    for role in ["button","link"]:
                                        try:
                                            page.get_by_role(role, name=txt).click(timeout=2000)
                                            clicked_create = True
                                            break
                                        except Exception:
                                            pass
                                    if clicked_create:
                                        break
                                if not clicked_create:
                                    try:
                                        page.get_by_text("Crear cuenta", exact=False).click(timeout=2000)
                                        clicked_create = True
                                    except Exception:
                                        pass
                                # Seleccionar opción personal
                                if clicked_create:
                                    for opt in ["Para uso personal","Para mí","Para mi","For my personal use","For myself","For me"]:
                                        try:
                                            page.get_by_text(opt, exact=False).click(timeout=1500)
                                            break
                                        except Exception:
                                            pass
                                # Verificar si estamos ya en el flujo
                                try:
                                    page.wait_for_url(lambda u: ("signup" in (u or "")) or ("webcreateaccount" in (u or "")), timeout=5000)
                                    fast_ok = ("signup" in (page.url or "")) or ("webcreateaccount" in (page.url or ""))
                                except Exception:
                                    pass
                                if fast_ok:
                                    break
                            except Exception:
                                pass
                except Exception:
                    pass
                # Solo si falló signup, usar login para encontrar "Crear cuenta"
                try:
                    if (not fast_ok) or ("signup" not in (page.url or "") and "webcreateaccount" not in (page.url or "")):
                        page.goto(
                            "https://accounts.google.com/v3/signin/identifier?service=mail&hl=es-419&passive=true&flowName=GlifWebSignIn&flowEntry=ServiceLogin",
                            wait_until="load",
                            timeout=6500,
                        )
                        # Si estamos en login, intentar presionar "Crear cuenta" y elegir "Para mí"
                        page.wait_for_timeout(200)
                        # Variantes en ES/EN y distintos roles
                        for txt in ["Crear cuenta", "Crear una cuenta", "Create account", "Create a account", "Create new account"]:
                            for role in ["button", "link"]:
                                try:
                                    page.get_by_role(role, name=txt).click(timeout=2000)
                                    break
                                except Exception:
                                    pass
                        # Fallback por texto directo
                        try:
                            page.get_by_text("Crear cuenta", exact=False).click(timeout=2000)
                        except Exception:
                            pass
                        # Fallbacks adicionales por selectores de texto y roles comunes
                        try:
                            page.locator("text=/^\\s*Crear\\s+cuenta\\s*$/i").first.click(timeout=2000)
                        except Exception:
                            pass
                        try:
                            page.locator("a:has-text('Crear cuenta')").first.click(timeout=2000)
                        except Exception:
                            pass
                        try:
                            page.locator("button:has-text('Crear cuenta')").first.click(timeout=2000)
                        except Exception:
                            pass
                        try:
                            page.locator("[role='button']:has-text('Crear cuenta')").first.click(timeout=2000)
                        except Exception:
                            pass
                        # Fallback por href explícito hacia rutas de alta
                        try:
                            page.locator('a[href*="signup"], a[href*="webcreateaccount"], a[href*="createaccount"]').first.click(timeout=2000)
                        except Exception:
                            pass
                        # Elegir opción "Para mí" / "For myself" si se despliega menú
                        # Intentar seleccionar explícitamente el elemento del menú por rol
                        try:
                            selected_from_menu = False
                            for opt in [
                                "Para mí","Para mi","Para uso personal","Para ti",
                                "For myself","For me"
                            ]:
                                try:
                                    page.get_by_role("menuitem", name=opt).click(timeout=1500)
                                    selected_from_menu = True
                                    break
                                except Exception:
                                    pass
                            if not selected_from_menu:
                                for opt in [
                                    "Para mí","Para mi","Para uso personal","Para ti",
                                    "For myself","For me"
                                ]:
                                    try:
                                        page.get_by_text(opt, exact=False).click(timeout=1500)
                                        selected_from_menu = True
                                        break
                                    except Exception:
                                        pass
                            # Si al seleccionar la opción se abrió una nueva pestaña/popup, adoptarla
                            try:
                                prev_pages = list(context.pages) if hasattr(context, 'pages') else []
                                page.wait_for_timeout(400)
                                curr_pages = list(context.pages) if hasattr(context, 'pages') else []
                                if len(curr_pages) > len(prev_pages):
                                    try:
                                        page = curr_pages[-1]
                                        page.bring_to_front()
                                        # Reaplicar timeouts en la nueva página
                                        page.set_default_navigation_timeout(8000)
                                        page.set_default_timeout(8000)
                                    except Exception:
                                        pass
                            except Exception:
                                pass
                        except Exception:
                            pass
                        # Si no se detecta cambio de URL, intentar dentro de iframes del flujo de login
                        try:
                            if ("signup" not in (page.url or "")) and ("webcreateaccount" not in (page.url or "")):
                                target_frames = []
                                for fr in page.frames:
                                    try:
                                        uf = (fr.url or "")
                                        if ("accounts.google.com" in uf) and ("signin" in uf or "identifier" in uf or "v3/signin" in uf):
                                            target_frames.append(fr)
                                    except Exception:
                                        pass
                                if not target_frames:
                                    target_frames = list(page.frames)
                                for fr in target_frames:
                                    # Click por rol y texto
                                    for txt in ["Crear cuenta", "Crear una cuenta", "Create account", "Create a account", "Create new account"]:
                                        for role in ["button", "link"]:
                                            try:
                                                fr.get_by_role(role, name=txt).click(timeout=1600)
                                                break
                                            except Exception:
                                                pass
                                    # Click por texto libre
                                    try:
                                        fr.get_by_text("Crear cuenta", exact=False).click(timeout=1600)
                                    except Exception:
                                        pass
                                    # Click por href
                                    try:
                                        fr.locator('a[href*="signup"], a[href*="webcreateaccount"], a[href*="createaccount"]').first.click(timeout=1600)
                                    except Exception:
                                        pass
                                    # Selección "Para mí" dentro del iframe si aparece menú
                                    for opt in ["Para mí","Para mi","Para uso personal","Para ti","For myself","For me"]:
                                        try:
                                            fr.get_by_text(opt, exact=False).click(timeout=1200)
                                            break
                                        except Exception:
                                            pass
                                    # Adoptar la pestaña nueva si el clic abre popup
                                    try:
                                        prev_pages = list(context.pages) if hasattr(context, 'pages') else []
                                        page.wait_for_timeout(300)
                                        curr_pages = list(context.pages) if hasattr(context, 'pages') else []
                                        if len(curr_pages) > len(prev_pages):
                                            try:
                                                page = curr_pages[-1]
                                                page.bring_to_front()
                                                page.set_default_navigation_timeout(8000)
                                                page.set_default_timeout(8000)
                                            except Exception:
                                                pass
                                    except Exception:
                                        pass
                        except Exception:
                            pass
                        # Verificar que la URL cambió efectivamente a flujo de alta
                        try:
                            page.wait_for_url(lambda u: ("signup" in (u or "")) or ("webcreateaccount" in (u or "")), timeout=5000)
                        except Exception:
                            pass
                        # Forzar redirección si aún seguimos en login
                        try:
                            if ("signup" not in (page.url or "")) and ("webcreateaccount" not in (page.url or "")):
                                page.evaluate("location.assign('https://accounts.google.com/signup/v2/createaccount?service=mail&hl=es-419')")
                                try:
                                    page.wait_for_url(lambda u: ("signup" in (u or "")) or ("webcreateaccount" in (u or "")), timeout=6000)
                                except Exception:
                                    pass
                        except Exception:
                            pass
                except Exception:
                    pass
                if not fast_ok:
                    try:
                        page.goto(
                            "https://accounts.google.com/signup/v2/createaccount?service=mail&hl=es-419",
                            wait_until="load",
                            timeout=8000,
                        )
                        fast_ok = True
                    except Exception:
                        try:
                            page.goto(
                                "https://accounts.google.com/signup/v2/webcreateaccount?flowName=GlifWebSignIn&flowEntry=SignUp",
                                wait_until="load",
                                timeout=8500,
                            )
                            fast_ok = True
                        except Exception:
                            pass
                # Si seguimos en login, empujar a la ruta de alta explícita
                try:
                    if ("signup" not in (page.url or "")) and ("webcreateaccount" not in (page.url or "")):
                        page.goto(
                            "https://accounts.google.com/signup/v2/createaccount?service=mail&hl=es-419",
                            wait_until="load",
                            timeout=6000,
                        )
                        try:
                            page.wait_for_url(lambda u: ("signup" in (u or "")) or ("webcreateaccount" in (u or "")), timeout=6000)
                        except Exception:
                            pass
                except Exception:
                    pass
                try:
                    page.wait_for_selector('input[name="firstName"]', timeout=6000)
                except Exception:
                    pass

                # Generar datos básicos
                male_names_local = ["Pablo","Juan","Luis","Carlos","Diego","Miguel","Nicolás","Sergio","Andrés","Hernán"]
                female_names_local = ["María","Lucía","Sofía","Valentina","Camila","Julieta","Agustina","Carolina","Paula","Florencia"]
                last_names_local = ["González","Rodríguez","García","Fernández","López","Martínez","Pérez","Sánchez","Romero","Díaz"]
                gender_flag = random.choice(["male","female"])
                first = random.choice(male_names_local) if gender_flag == "male" else random.choice(female_names_local)
                last = random.choice(last_names_local)

                # Rellenar nombre y apellido
                try:
                    page.fill('input[name="firstName"]', first)
                except Exception:
                    pass
                try:
                    page.fill('input[name="lastName"]', last)
                except Exception:
                    pass
                try:
                    # Avanzar rápido
                    for name_btn in ["Siguiente","Next","Continuar","Continue"]:
                        try:
                            page.get_by_role("button", name=name_btn).click(timeout=2500)
                            break
                        except Exception:
                            pass
                except Exception:
                    pass

                # Rellenar Información básica: Mes/Día/Año y Género
                try:
                    # Generar fecha y género como antes
                    try:
                        year, month, day = rand_birth()
                    except Exception:
                        year = random.randint(1980, 2000)
                        month = random.randint(1, 12)
                        day = random.randint(1, 28)

                    # Seleccionar mes usando helper Material Design con fallbacks
                    try:
                        months_es = {
                            1: "Enero", 2: "Febrero", 3: "Marzo", 4: "Abril", 5: "Mayo", 6: "Junio",
                            7: "Julio", 8: "Agosto", 9: "Septiembre", 10: "Octubre", 11: "Noviembre", 12: "Diciembre",
                        }
                        months_en = {
                            1: "January", 2: "February", 3: "March", 4: "April", 5: "May", 6: "June",
                            7: "July", 8: "August", 9: "September", 10: "October", 11: "November", 12: "December",
                        }
                        label_month_es = months_es.get(month, str(month))
                        label_month_en = months_en.get(month, str(month))
                        label_month_es_caps = label_month_es.upper()
                        label_month_en_caps = label_month_en.upper()

                        def select_material_combo(combo_labels, option_labels, fallback_select_name=None):
                            try:
                                # Abrir el combobox por rol/label
                                cb = None
                                for lab in combo_labels:
                                    try:
                                        el = page.get_by_role("combobox", name=lab)
                                        if el and el.count() > 0:
                                            cb = el.first
                                            break
                                    except Exception:
                                        pass
                                if cb is None:
                                    for lab in combo_labels:
                                        try:
                                            el = page.get_by_label(lab)
                                            if el and el.count() > 0:
                                                cb = el.first
                                                break
                                        except Exception:
                                            pass
                                if cb is not None:
                                    try:
                                        cb.click(timeout=1500)
                                    except Exception:
                                        pass
                                    # Seleccionar por texto visible
                                    for opt in option_labels:
                                        try:
                                            page.locator('[role="option"]').filter(has_text=opt).first.click(timeout=1500)
                                            return True
                                        except Exception:
                                            # Intento adicional: click por texto directo (sin limitar al rol)
                                            try:
                                                page.get_by_text(opt, exact=False).first.click(timeout=1500)
                                                return True
                                            except Exception:
                                                pass
                                    # Regex insensible a mayúsculas
                                    try:
                                        import re
                                        for opt in option_labels:
                                            try:
                                                regex = re.compile(rf"^{re.escape(opt)}$", re.IGNORECASE)
                                                page.get_by_role("option", name=regex).first.click(timeout=1200)
                                                return True
                                            except Exception:
                                                try:
                                                    regex2 = re.compile(re.escape(opt), re.IGNORECASE)
                                                    page.get_by_role("option", name=regex2).first.click(timeout=1200)
                                                    return True
                                                except Exception:
                                                    pass
                                    except Exception:
                                        pass
                                    try:
                                        page.keyboard.press('Escape')
                                    except Exception:
                                        pass
                                # Fallback: <select name="...">
                                if fallback_select_name:
                                    try:
                                        page.wait_for_selector(f'select[name="{fallback_select_name}"]', timeout=1500)
                                        for opt in option_labels:
                                            try:
                                                page.select_option(f'select[name="{fallback_select_name}"]', label=opt)
                                                return True
                                            except Exception:
                                                pass
                                    except Exception:
                                        pass
                            except Exception:
                                pass
                            return False

                        # Preferir español primero
                        # Construir opciones ES priorizando mayúsculas y variantes regionales
                        opts_es = [label_month_es, label_month_es_caps, label_month_es.lower()]
                        try:
                            if str(month) == '9':
                                opts_es += ["Setiembre", "SETIEMBRE", "setiembre"]
                        except Exception:
                            pass
                        selected_month = select_material_combo([
                            "Mes", "MES", "Fecha de nacimiento"
                        ], opts_es, fallback_select_name="month")
                        if not selected_month:
                            # Fallback si la UI está en inglés
                            selected_month = select_material_combo([
                                "Month", "Date of birth"
                            ], [label_month_en, label_month_en_caps, label_month_en.lower()], fallback_select_name="month")
                        if not selected_month:
                            try:
                                page.evaluate(
                                    "(lab) => { const sel = document.querySelector('select[name=month]'); if(!sel) return; const opt = Array.from(sel.options).find(o => (o.textContent||'').trim().toLowerCase() === lab.toLowerCase()); if(opt){ sel.value = opt.value; sel.dispatchEvent(new Event('change',{bubbles:true})); } }",
                                    label_month_es,
                                )
                            except Exception:
                                pass
                        if not selected_month:
                            # Fallback por teclado: abrir combo y navegar por flechas
                            try:
                                cb = None
                                for lab in ["Mes","Fecha de nacimiento","Month","Date of birth"]:
                                    try:
                                        el = page.get_by_role("combobox", name=lab)
                                        if el and el.count() > 0:
                                            cb = el.first
                                            break
                                    except Exception:
                                        pass
                                if cb is None:
                                    for lab in ["Mes","Fecha de nacimiento","Month","Date of birth"]:
                                        try:
                                            el = page.get_by_label(lab)
                                            if el and el.count() > 0:
                                                cb = el.first
                                                break
                                        except Exception:
                                            pass
                                if cb is not None:
                                    try:
                                        cb.click(timeout=1200)
                                    except Exception:
                                        pass
                                    # Mover con flechas hasta el mes deseado
                                    try:
                                        page.keyboard.press('Home')
                                    except Exception:
                                        pass
                                    try:
                                        steps = max(0, int(month) - 1)
                                        for _ in range(steps):
                                            page.keyboard.press('ArrowDown')
                                    except Exception:
                                        pass
                                    try:
                                        page.keyboard.press('Enter')
                                    except Exception:
                                        pass
                                    selected_month = True
                            except Exception:
                                pass
                    except Exception:
                        pass

                    # Día
                    try:
                        filled_day = False
                        # Intentar por label accesible, placeholder y combinaciones frecuentes de name/id
                        for sel in [
                            lambda: page.get_by_label("Día"),
                            lambda: page.get_by_label("Day"),
                            lambda: page.get_by_placeholder("Día"),
                            lambda: page.locator('input[aria-label="Día"]'),
                            lambda: page.locator('input[name="birthday-day"]'),
                            lambda: page.locator('input[name="birthDay"]'),
                            lambda: page.locator('input[name="Day"]'),
                            lambda: page.locator('input[id="day"]'),
                            lambda: page.locator('input[name="day"]')
                        ]:
                            try:
                                el = sel()
                                if el and hasattr(el, 'count') and el.count() > 0:
                                    el.first.fill(str(day))
                                    filled_day = True
                                    break
                                # Para locators directos (sin count)
                                if el and not hasattr(el, 'count'):
                                    try:
                                        el.fill(str(day))
                                        filled_day = True
                                        break
                                    except Exception:
                                        pass
                            except Exception:
                                pass
                        if not filled_day:
                            try:
                                page.fill('input[name="day"]', str(day))
                            except Exception:
                                pass
                        # Disparar eventos para validación en cualquiera de los campos posibles
                        try:
                            page.evaluate("""
                                () => {
                                    const sels = [
                                        'input[name=day]','input[name="birthday-day"]','input[name="birthDay"]','input[name="Day"]','input[id=day]','input[aria-label="Día"]'
                                    ];
                                    for (const s of sels) {
                                        const d = document.querySelector(s);
                                        if (!d) continue;
                                        d.dispatchEvent(new Event('input',{bubbles:true}));
                                        d.dispatchEvent(new Event('change',{bubbles:true}));
                                        if (typeof d.blur === 'function') d.blur();
                                    }
                                }
                            """)
                        except Exception:
                            pass
                    except Exception:
                        pass

                    # Año
                    try:
                        filled_year = False
                        for sel in [
                            lambda: page.get_by_label("Año"),
                            lambda: page.get_by_label("Year"),
                            lambda: page.get_by_placeholder("Año"),
                            lambda: page.locator('input[aria-label="Año"]'),
                            lambda: page.locator('input[name="birthYear"]'),
                            lambda: page.locator('input[name="Year"]'),
                            lambda: page.locator('input[id="year"]'),
                            lambda: page.locator('input[name="year"]')
                        ]:
                            try:
                                el = sel()
                                if el and hasattr(el, 'count') and el.count() > 0:
                                    el.first.fill(str(year))
                                    filled_year = True
                                    break
                                if el and not hasattr(el, 'count'):
                                    try:
                                        el.fill(str(year))
                                        filled_year = True
                                        break
                                    except Exception:
                                        pass
                            except Exception:
                                pass
                        if not filled_year:
                            try:
                                page.fill('input[name="year"]', str(year))
                            except Exception:
                                pass
                        try:
                            page.evaluate("""
                                () => {
                                    const sels = [
                                        'input[name=year]','input[name="birthYear"]','input[name="Year"]','input[id=year]','input[aria-label="Año"]'
                                    ];
                                    for (const s of sels) {
                                        const y = document.querySelector(s);
                                        if (!y) continue;
                                        y.dispatchEvent(new Event('input',{bubbles:true}));
                                        y.dispatchEvent(new Event('change',{bubbles:true}));
                                        if (typeof y.blur === 'function') y.blur();
                                    }
                                }
                            """)
                        except Exception:
                            pass
                    except Exception:
                        pass

                    # Género
                    try:
                        labels_gender = ['Hombre','Masculino','Male'] if gender_flag == 'male' else ['Mujer','Femenino','Female']
                        ok_gender = False
                        try:
                            ok_gender = select_material_combo([
                                "Género", "Genero", "Sexo", "Sex", "Gender"
                            ], labels_gender + ['1','2'], fallback_select_name="gender")
                        except Exception:
                            ok_gender = False
                        if not ok_gender:
                            try:
                                page.wait_for_selector('select[name="gender"]', timeout=1500)
                                for g in labels_gender:
                                    try:
                                        page.select_option('select[name="gender"]', label=g)
                                        ok_gender = True
                                        break
                                    except Exception:
                                        pass
                                if not ok_gender:
                                    page.select_option('select[name="gender"]', '1' if gender_flag == 'male' else '2')
                            except Exception:
                                pass
                        # Fallback adicional: radios Material por rol/nombre
                        try:
                            if not ok_gender:
                                for g in labels_gender + ['Prefiero no decirlo','Prefer not to say']:
                                    chosen = False
                                    try:
                                        loc = page.get_by_role('radio', name=g)
                                        if loc and loc.count() > 0:
                                            loc.first.click(timeout=1200)
                                            ok_gender = True
                                            chosen = True
                                    except Exception:
                                        pass
                                    if chosen:
                                        break
                                if not ok_gender:
                                    for g in labels_gender + ['Prefiero no decirlo','Prefer not to say']:
                                        try:
                                            page.get_by_text(g, exact=False).click(timeout=1000)
                                            ok_gender = True
                                            break
                                        except Exception:
                                            pass
                        except Exception:
                            pass
                        try:
                            page.evaluate("() => { const sel = document.querySelector('select[name=gender]'); if(sel){ sel.dispatchEvent(new Event('change',{bubbles:true})); } const checked = document.querySelector('[role=radio][aria-checked=true]'); if(checked){ checked.dispatchEvent(new Event('change',{bubbles:true})); if (typeof checked.blur === 'function') checked.blur(); } }")
                        except Exception:
                            pass
                    except Exception:
                        pass

                    # Avanzar tras completar información básica
                    try:
                        import re
                        progressed = False
                        # Intentar hasta 3 veces con ligeras variaciones de fecha
                        for _ in range(3):
                            # Asegurar blur de campos
                            try:
                                page.keyboard.press('Tab')
                                page.keyboard.press('Tab')
                            except Exception:
                                pass
                            # Pre-habilitar el botón por si la UI lo deja deshabilitado
                            try:
                                page.evaluate("""
                                    () => {
                                        const texts = /Siguiente|Next|Continuar|Continue/i;
                                        const btn = Array.from(document.querySelectorAll('button,[role=button]')).find(b => texts.test(b.textContent||''));
                                        if (btn) { btn.removeAttribute('disabled'); btn.setAttribute('aria-disabled','false'); }
                                    }
                                """)
                            except Exception:
                                pass
                            # Clic robusto en "Siguiente"
                            clicked = False
                            try:
                                loc = page.locator("button:has-text('Siguiente'), [role='button']:has-text('Siguiente')")
                                if loc.count() > 0:
                                    try:
                                        loc.first.scroll_into_view_if_needed()
                                    except Exception:
                                        pass
                                    loc.first.click(timeout=3000)
                                    clicked = True
                            except Exception:
                                pass
                            if not clicked:
                                try:
                                    btn = page.get_by_role("button", name=re.compile("Siguiente|Next|Continuar|Continue", re.IGNORECASE))
                                    if btn and btn.count() > 0:
                                        try:
                                            btn.first.scroll_into_view_if_needed()
                                        except Exception:
                                            pass
                                        btn.first.click(timeout=3000)
                                        clicked = True
                                except Exception:
                                    pass
                            if not clicked:
                                try:
                                    page.get_by_text(re.compile("Siguiente|Next|Continuar|Continue", re.IGNORECASE), exact=False).first.click(timeout=3000)
                                    clicked = True
                                except Exception:
                                    pass
                            # Comprobar avance
                            try:
                                page.wait_for_load_state("domcontentloaded")
                            except Exception:
                                pass
                            try:
                                page.wait_for_selector("input[name='Username'], input#username, input[name='username']", timeout=3500)
                                progressed = True
                            except Exception:
                                progressed = False
                            if progressed:
                                break
                            # Si no avanzó, ajustar la fecha y reintentar
                            try:
                                year2 = random.randint(1991, 2002)
                                day2 = random.randint(2, 27)
                                # Evitar Feb 29
                                month2 = random.choice([1,2,3,4,5,6,7,8,10,11,12,9])
                                try:
                                    page.fill('input[name="day"]', str(day2))
                                except Exception:
                                    pass
                                try:
                                    page.fill('input[name="year"]', str(year2))
                                except Exception:
                                    pass
                                # Intentar cambiar el mes por etiqueta conocida
                                try:
                                    months_es = {
                                        1: "Enero", 2: "Febrero", 3: "Marzo", 4: "Abril", 5: "Mayo", 6: "Junio",
                                        7: "Julio", 8: "Agosto", 9: "Septiembre", 10: "Octubre", 11: "Noviembre", 12: "Diciembre",
                                    }
                                    label_month_es = months_es.get(month2, str(month2))
                                    page.get_by_role("combobox", name=re.compile("Mes|Month|Fecha", re.IGNORECASE)).click(timeout=1200)
                                    try:
                                        page.locator('[role="option"]').filter(has_text=label_month_es).first.click(timeout=1200)
                                    except Exception:
                                        page.get_by_text(label_month_es, exact=False).first.click(timeout=1200)
                                except Exception:
                                    pass
                            except Exception:
                                pass
                            try:
                                page.keyboard.press('Tab')
                            except Exception:
                                pass
                            # Alternar género si seguimos sin avanzar o aparece mensaje "Selecciona género"
                            try:
                                if not progressed:
                                    try:
                                        err = page.get_by_text(re.compile("Selecciona(\s+el|\s+tu)?\s+género|Selecciona género", re.IGNORECASE))
                                        if err and err.count() > 0:
                                            progressed = False
                                    except Exception:
                                        pass
                                    gender_flag = 'female' if gender_flag == 'male' else 'male'
                                    labels_gender_alt = ['Hombre','Masculino','Male'] if gender_flag == 'male' else ['Mujer','Femenino','Female']
                                    ok_g2 = False
                                    try:
                                        page.wait_for_selector('select[name="gender"]', timeout=800)
                                        for g in labels_gender_alt:
                                            try:
                                                page.select_option('select[name="gender"]', label=g)
                                                ok_g2 = True
                                                break
                                            except Exception:
                                                pass
                                        if not ok_g2:
                                            try:
                                                page.select_option('select[name="gender"]', '1' if gender_flag == 'male' else '2')
                                                ok_g2 = True
                                            except Exception:
                                                pass
                                    except Exception:
                                        pass
                                    if not ok_g2:
                                        try:
                                            for g in labels_gender_alt:
                                                loc = page.get_by_role('radio', name=g)
                                                if loc and loc.count() > 0:
                                                    loc.first.click(timeout=800)
                                                    ok_g2 = True
                                                    break
                                        except Exception:
                                            pass
                                    try:
                                        page.evaluate("() => { const sel = document.querySelector('select[name=gender]'); if(sel){ sel.dispatchEvent(new Event('change',{bubbles:true})); } const checked = document.querySelector('[role=radio][aria-checked=true]'); if(checked){ checked.dispatchEvent(new Event('change',{bubbles:true})); if (typeof checked.blur === 'function') checked.blur(); } }")
                                    except Exception:
                                        pass
                                    try:
                                        page.keyboard.press('Tab')
                                    except Exception:
                                        pass
                            except Exception:
                                pass
                        # Si la navegación retrocede al login, reencaminar al alta
                        try:
                            if not progressed:
                                u = page.url or ""
                                if ("signin" in u) or ("identifier" in u):
                                    page.goto(
                                        "https://accounts.google.com/signup/v2/webcreateaccount?flowName=GlifWebSignIn&flowEntry=SignUp&hl=es",
                                        wait_until="domcontentloaded",
                                        timeout=8000,
                                    )
                                    try:
                                        page.wait_for_selector('input[name="firstName"]', timeout=5000)
                                    except Exception:
                                        pass
                                # Fallback agresivo: avanzar directamente al paso de Username
                                try:
                                    page.goto(
                                        "https://accounts.google.com/lifecycle/steps/signup/username?flowName=GlifWebSignIn&flowEntry=SignUp&hl=es",
                                        wait_until="domcontentloaded",
                                        timeout=6000,
                                    )
                                    try:
                                        page.wait_for_selector("input[name='Username'], input#username, input[name='username']", timeout=5000)
                                        progressed = True
                                    except Exception:
                                        progressed = progressed or False
                                except Exception:
                                    pass
                        except Exception:
                            pass
                    except Exception:
                        pass
                except Exception:
                    pass

                # Username: intentar tomar sugerencia o construir uno
                handle = None
                try:
                    # Si hay lista de opciones, escoger la primera
                    if page.locator('[role="listbox"] [role="option"]').count() > 0:
                        try:
                            opt = page.locator('[role="listbox"] [role="option"]').first
                            suggested = opt.text_content() or ""
                            if suggested:
                                handle = suggested.strip().split("@")[0]
                                opt.click(timeout=2000)
                        except Exception:
                            pass
                except Exception:
                    pass
                if not handle:
                    # Construir username básico
                    base = f"{first}{last}".lower()
                    base = base.replace("á","a").replace("é","e").replace("í","i").replace("ó","o").replace("ú","u").replace("ñ","n")
                    handle = f"{base}{random.randint(100,999)}"
                    try:
                        for sel in [
                            'input[name="Username"]',
                            'input#username',
                            'input[name="username"]',
                            'input[aria-label*="Username"]',
                            'input[autocomplete="username"]',
                        ]:
                            if page.locator(sel).count() > 0:
                                page.fill(sel, handle)
                                break
                    except Exception:
                        pass

                # Contraseña
                chosen_pass = password or "P4bl0_1427"
                try:
                    for sel in [
                        'input[name="Passwd"]',
                        'input[name="ConfirmPasswd"]',
                        'input[type="password"]',
                    ]:
                        if page.locator(sel).count() > 0:
                            page.fill(sel, chosen_pass)
                except Exception:
                    pass
                try:
                    # Confirmación si hay segundo input
                    if page.locator('input[name="ConfirmPasswd"]').count() > 0:
                        page.fill('input[name="ConfirmPasswd"]', chosen_pass)
                except Exception:
                    pass

                try:
                    # Avanzar tras credenciales
                    for name_btn in ["Siguiente","Next","Continuar","Continue"]:
                        try:
                            page.get_by_role("button", name=name_btn).click(timeout=2500)
                            break
                        except Exception:
                            pass
                except Exception:
                    pass

                # Screenshot del estado
                try:
                    page.screenshot(path=screenshot_path, full_page=True)
                except Exception:
                    pass

                # Determinar estado final básico
                email_chosen = f"{handle}@gmail.com" if handle else None
                status = "verification_required" if "verify" in (page.url or "") else "attempted"

            items.append({
                "index": 0,
                "status": status,
                "email": email_chosen,
                "password": password or "P4bl0_1427",
                "screenshot": screenshot_path,
                "elapsed_ms": int((time.time() - started) * 1000),
                "vpn_provider": None,
                "vpn_country": None,
                "error": error,
            })
        finally:
            try:
                browser.close()
            except Exception:
                pass
            try:
                ext = getattr(browser, "_external_proc", None)
                if ext:
                    ext.terminate()
            except Exception:
                pass

    return {"status": "completed", "items": items}