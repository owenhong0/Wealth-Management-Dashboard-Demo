"""App configuration, loaded from the repo-root .env file."""
import os
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(REPO_ROOT / ".env")

DATA_DIR = REPO_ROOT / "data"
GENERATED_DIR = DATA_DIR / "generated"
CACHE_DIR = DATA_DIR / "cache"


def _flag(name: str, default: str) -> bool:
    return os.getenv(name, default).strip().lower() not in {"false", "0", "no"}


def _database_url() -> str:
    url = os.getenv("DATABASE_URL", "sqlite:///data/basis_trail.db")
    # Resolve relative SQLite paths against the repo root, not the cwd.
    prefix = "sqlite:///"
    if url.startswith(prefix) and not url[len(prefix):].startswith("/"):
        url = prefix + str(REPO_ROOT / url[len(prefix):])
    return url


DATABASE_URL = _database_url()
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY") or None
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5-5")
USE_CACHED_LLM = _flag("USE_CACHED_LLM", "true")