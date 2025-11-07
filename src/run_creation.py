r"""
Archivo: run_creation.py

Script de ejecución para automatizar la creación de cuentas de Gmail
usando la lógica existente en src/browser_tasks.py.

Ejecutar desde la raíz del proyecto:
  - Instalar dependencias:
      venv\Scripts\pip.exe install playwright
      venv\Scripts\playwright.exe install chromium
  - Correr el script:
      venv\Scripts\python.exe -m src.run_creation
"""

try:
    # Cuando se ejecuta como paquete: python -m src.run_creation
    from .browser_tasks import create_google_accounts_backup  # type: ignore
except Exception:
    try:
        # Import absoluto si el entorno carga paquetes por nombre
        from src.browser_tasks import create_google_accounts_backup  # type: ignore
    except Exception:
        # Fallback: ejecución directa del archivo
        from browser_tasks import create_google_accounts_backup  # type: ignore
import os
import sys

# ==================================
#         ⚙️ CONFIGURACIÓN
# ==================================

# ⚠️ Importante: Debe usar una contraseña que cumpla con los requisitos de Google
# (8+ caracteres, mayúsculas, minúsculas, números y símbolos).
CONTRASEÑA_SEGURA = "SuContraseñaFuerte123!"

# Número de cuentas de Gmail que desea intentar crear.
NUM_CUENTAS = 5

# ==================================


def run_automation():
    """Ejecuta el proceso de creación de cuentas de Gmail de forma automatizada."""
    # Verifica que el archivo de lógica exista en src/
    logic_path = os.path.join(os.path.dirname(__file__), "browser_tasks.py")
    if not os.path.exists(logic_path):
        print(
            "ERROR: El archivo 'browser_tasks.py' no se encuentra en 'src/'. "
            "Asegúrese de que esté guardado en c\\Users\\stefa\\Desktop\\GRANJA\\src\\"
        )
        sys.exit(1)

    print(f"Iniciando intento de creación de {NUM_CUENTAS} cuentas de Gmail...")
    print(
        "¡El script rellenará el formulario! Esté listo para intervenir cuando "
        "Google solicite el NÚMERO DE TELÉFONO o CAPTCHA."
    )

    try:
        # Llamada a la función de automatización de su script.
        resultados = create_google_accounts_backup(
            count=NUM_CUENTAS,
            password=CONTRASEÑA_SEGURA,
            incognito=True,
        )

        print("\n--- RESUMEN DE RESULTADOS ---")
        print(f"Se completó la simulación con el estado: {resultados.get('status')}\n")

        for item in resultados.get("items", []):
            status = item.get("status")
            email = item.get("email", "N/A")

            if status == "verification_required":
                print(
                    f"➡️ Intento: {email} | Estado: DETENIDO. Requirió su intervención para "
                    "verificación telefónica/CAPTCHA."
                )
            elif status == "error":
                print(
                    f"❌ Intento: N/A | ERROR CRÍTICO. Detalle: {item.get('error', 'Error desconocido')}"
                )
            elif status == "attempted":
                print(
                    f"✅ Intento: {email} | Estado: CREADO o LISTO para el último paso. "
                    f"Contraseña: {CONTRASEÑA_SEGURA}"
                )

        print("\nRevise la carpeta 'artifacts' para ver las capturas de pantalla de cada paso.")

    except Exception as e:
        print(f"\nFATAL: Error al ejecutar la automatización: {e}")


if __name__ == "__main__":
    run_automation()