"""Settings for pidcheck: environment variables, then the repo's .env.

All settings are optional. See .env.example for the list.
"""

import os
import re
from functools import cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = ROOT / ".env"


@cache
def _dotenv(path: Path = ENV_FILE) -> dict[str, str]:
    """Parse KEY=value lines (# comments, optional quotes, 'export ')."""
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for line in path.read_text("utf-8").splitlines():
        m = re.match(
            r"\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$", line
        )
        if not m or line.lstrip().startswith("#"):
            continue
        key, value = m.groups()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key] = value
    return values


def setting(name: str) -> str | None:
    """A setting's value: the environment variable if set, else .env.
    Empty values count as unset."""
    return os.environ.get(name) or _dotenv().get(name) or None


def cache_dir() -> Path:
    return Path(setting("PIDCHECK_CACHE_DIR") or ROOT / ".cache" / "pidcheck")


def cache_days() -> float:
    return float(setting("PIDCHECK_CACHE_DAYS") or 30)
