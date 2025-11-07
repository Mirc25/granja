import subprocess
import time
from typing import List, Optional


class VpnController:
    def connect_next(self) -> bool:
        raise NotImplementedError

    def disconnect(self) -> None:
        raise NotImplementedError


class NordVpnController(VpnController):
    """Control básico del cliente NordVPN en Windows usando su CLI.
    Requiere tener `nordvpn.exe` en PATH y sesión iniciada.
    """

    def __init__(self, country: Optional[str] = None, servers: Optional[List[str]] = None, wait_seconds: int = 5):
        self.country = country or "Argentina"
        self.servers = servers or []
        self.index = 0
        self.wait_seconds = wait_seconds

    def _run(self, args: List[str]) -> subprocess.CompletedProcess:
        return subprocess.run(["nordvpn", *args], capture_output=True, text=True, shell=False)

    def connect_next(self) -> bool:
        try:
            # Si hay lista de servidores, usa rotación explícita
            if self.servers:
                server = self.servers[self.index % len(self.servers)]
                self.index += 1
                proc = self._run(["connect", server])
            else:
                # Conecta por país (elige server automáticamente)
                proc = self._run(["-c", "-g", self.country])
            ok = proc.returncode == 0
            # Pequeña espera para estabilizar el túnel
            time.sleep(self.wait_seconds)
            return ok
        except Exception:
            return False

    def disconnect(self) -> None:
        try:
            self._run(["disconnect"])
            time.sleep(1)
        except Exception:
            pass


def get_vpn_controller(provider: Optional[str], country: Optional[str], servers: Optional[List[str]], wait_ms: int) -> Optional[VpnController]:
    if not provider:
        return None
    wait_seconds = max(1, int(wait_ms / 1000))
    name = provider.strip().lower()
    if name in ("nord", "nordvpn"):
        return NordVpnController(country=country, servers=servers, wait_seconds=wait_seconds)
    # Se pueden agregar más proveedores aquí (Surfshark, Proton, OpenVPN)
    return None