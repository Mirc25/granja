from urllib.parse import urlparse
from urllib import robotparser


def is_allowed_by_robots(url: str, user_agent: str) -> bool:
    parsed = urlparse(url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    rp = robotparser.RobotFileParser()
    rp.set_url(robots_url)
    try:
        rp.read()
    except Exception:
        # Si robots no es accesible, ser conservador y permitir (ajusta a False si prefieres bloquear)
        return True
    return rp.can_fetch(user_agent, url)