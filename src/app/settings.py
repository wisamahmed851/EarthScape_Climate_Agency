"""App configuration from environment variables (optionally the git-ignored .env). No secrets in code."""
import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def load_env_file(path=ROOT / ".env"):
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip().strip("\"'"))


@dataclass
class Settings:
    mongo_uri: str = "mongodb://localhost:27017"
    mongo_db: str = "earthscape"
    cookie_secure: bool = False   # set COOKIE_SECURE=1 behind TLS
    session_hours: int = 8
    poll_enabled: bool = False        # background polling thread; POLL_ENABLED=1 (default when run from the environment)
    poll_minutes: int = 15
    log_file: Path | None = None      # set from the environment for real runs; tests leave logging alone
    openaq_minutes: int = 60
    cache_dir: Path = ROOT / "data" / "processed" / "app_cache"

    @classmethod
    def from_env(cls):
        load_env_file()
        e = os.environ
        return cls(mongo_uri=e.get("MONGODB_URI", cls.mongo_uri), mongo_db=e.get("MONGODB_DB", cls.mongo_db),
                   cookie_secure=e.get("COOKIE_SECURE", "0") == "1",
                   session_hours=int(e.get("SESSION_HOURS", cls.session_hours)),
                   poll_enabled=e.get("POLL_ENABLED", "1") == "1", log_file=ROOT / "logs" / "earthscape.log", poll_minutes=int(e.get("POLL_MINUTES", cls.poll_minutes)),
                   openaq_minutes=int(e.get("OPENAQ_MINUTES", cls.openaq_minutes)))
