import os

redis_url = os.getenv("RQ_REDIS_URL", "redis://localhost:6379/0")
queue_name = os.getenv("QUEUE_NAME", "bot_queue")
default_user_agent = os.getenv("BOT_USER_AGENT", "TestBot/1.0 (+contact@example.com)")
proxies_file = os.getenv("PROXIES_FILE", "proxies.txt")
artifacts_dir = os.getenv("ARTIFACTS_DIR", "artifacts")