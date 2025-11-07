import argparse
import json
import os
import time
from typing import List

from .browser_tasks import visit_links_with_rotation, ensure_artifacts_dir


def read_urls_from_file(path: str) -> List[str]:
    if not os.path.exists(path):
        raise FileNotFoundError(f"No existe el archivo: {path}")
    with open(path, "r", encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip() and not line.strip().startswith("#")]


def main():
    parser = argparse.ArgumentParser(description="Granja de bots de QA (modo local, sin Redis). Ejemplos de uso:")
    parser.add_argument(
        "-u", "--urls", nargs="*",
        help="Lista de URLs a visitar. Ejemplo: -u https://example.com https://example.com/docs"
    )
    parser.add_argument(
        "-f", "--file",
        help="Archivo de texto con URLs (una por línea). Ejemplo: -f urls.txt"
    )
    parser.add_argument(
        "--bots", type=int, default=1,
        help="Número de bots (sesiones paralelas). Ejemplo: --bots 3"
    )
    parser.add_argument(
        "--max", type=int, default=5,
        help="Páginas por proxy antes de rotar. Ejemplo: --max 3"
    )
    parser.add_argument(
        "--no-screenshot", action="store_true",
        help="No guardar capturas. Ejemplo: --no-screenshot"
    )
    parser.add_argument(
        "--no-headless", action="store_true",
        help="Mostrar la ventana del navegador (por defecto headless). Ejemplo: --no-headless"
    )
    parser.add_argument(
        "--respect-robots", action="store_true",
        help="Respetar robots.txt (bloquea URLs no permitidas). Ejemplo: --respect-robots"
    )
    parser.add_argument(
        "--user-agent", type=str, default=None,
        help="User-Agent personalizado. Ejemplo: --user-agent 'TestBot/1.0 (+contacto@ejemplo.com)'"
    )
    parser.add_argument(
        "--min-dwell-ms", type=int, default=3000,
        help="Permanencia mínima por página (ms). Ejemplo: --min-dwell-ms 5000"
    )
    parser.add_argument(
        "--max-dwell-ms", type=int, default=15000,
        help="Permanencia máxima por página (ms). Ejemplo: --max-dwell-ms 15000"
    )

    args = parser.parse_args()

    urls: List[str] = []
    if args.file:
        urls.extend(read_urls_from_file(args.file))
    if args.urls:
        urls.extend(args.urls)

    if not urls:
        print("Debes proporcionar URLs con -u o un archivo con -f")
        print("Ejemplo directo: python -m src.cli -u https://example.com https://example.com/docs --bots 2 --min-dwell-ms 5000 --max-dwell-ms 15000")
        print("Ejemplo archivo: python -m src.cli -f urls.txt --bots 2 --max 3")
        return 1

    ensure_artifacts_dir()

    headless = not args.no_headless

    if args.min_dwell_ms > args.max_dwell_ms:
        args.min_dwell_ms, args.max_dwell_ms = args.max_dwell_ms, args.min_dwell_ms

    # Ejecuta una o varias sesiones en paralelo
    from concurrent.futures import ThreadPoolExecutor, as_completed

    def run_once(idx: int):
        res = visit_links_with_rotation(
            urls=urls,
            max_pages_per_proxy=args.max,
            screenshot=not args.no_screenshot,
            user_agent=args.user_agent,
            headless=headless,
            respect_robots=args.respect_robots,
            min_dwell_ms=args.min_dwell_ms,
            max_dwell_ms=args.max_dwell_ms,
        )
        return {"bot_id": idx, **res}

    bots = max(1, args.bots)

    results = []
    with ThreadPoolExecutor(max_workers=bots) as ex:
        futures = [ex.submit(run_once, i) for i in range(bots)]
        for fut in as_completed(futures):
            results.append(fut.result())

    aggregated = {"status": "completed", "bots": bots, "sessions": results}

    ts = time.strftime("%Y%m%d_%H%M%S")
    out_path = os.path.join("artifacts", f"results_{ts}_agg.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(aggregated, f, ensure_ascii=False, indent=2)

    print(f"Resultados guardados en: {out_path}")
    print("Resumen:")
    for sess in aggregated.get("sessions", []):
        ok = sum(1 for r in sess.get("results", []) if r.get("status") == "ok")
        total = len(sess.get("results", []))
        print(f"- Bot {sess['bot_id']}: {ok}/{total} páginas ok")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())