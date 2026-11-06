import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = Path(os.environ.get("ENGINE_CONFIG_DIR", ROOT / "config"))


def load_dotenv(path: Path = ROOT / ".env") -> None:
    """tiny .env reader. sets vars that are not already set. never prints values."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def db_path() -> Path:
    load_dotenv()
    p = Path(os.environ.get("ENGINE_DB", "data/engine.db"))
    return p if p.is_absolute() else ROOT / p
