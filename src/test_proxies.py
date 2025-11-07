import sys
from urllib.request import build_opener, ProxyHandler
from urllib.error import URLError, HTTPError


def load_proxies(path: str = "proxies.txt"):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return [line.strip() for line in f if line.strip()]
    except FileNotFoundError:
        return []


def test_proxy(proxy: str):
    pr = {"http": proxy, "https": proxy}
    opener = build_opener(ProxyHandler(pr))
    opener.addheaders = [("User-Agent", "Mozilla/5.0")]  # simple UA
    url = "https://api.ipify.org?format=json"
    try:
        with opener.open(url, timeout=8) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return True, body
    except (URLError, HTTPError) as e:
        return False, f"{type(e).__name__}: {e}"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def main():
    proxies = load_proxies()
    if not proxies:
        print("No proxies found in proxies.txt")
        return 1
    for i, p in enumerate(proxies, 1):
        ok, info = test_proxy(p)
        status = "OK" if ok else "FAIL"
        print(f"{i}. {p} -> {status} | {info}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())