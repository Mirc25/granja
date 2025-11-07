from typing import List, Optional, Dict
from itertools import cycle
import os

from .config import proxies_file


def load_proxies() -> List[str]:
    if not os.path.exists(proxies_file):
        return []
    with open(proxies_file, "r", encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip() and not line.strip().startswith("#")]


def parse_proxy_line(line: str) -> Optional[Dict[str, str]]:
    # Acepta formatos como:
    # - http://host:port
    # - http://user:pass@host:port
    return {"server": line.strip()}


class ProxyRotator:
    def __init__(self, proxies: Optional[List[str]] = None):
        if proxies is None:
            proxies = load_proxies()
        parsed = [p for p in (parse_proxy_line(s) for s in proxies) if p]
        self._cycle = cycle(parsed) if parsed else None

    def next(self) -> Optional[Dict[str, str]]:
        if self._cycle is None:
            return None
        return next(self._cycle)