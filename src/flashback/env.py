"""Local environment-file loading.

The service is still configured through environment variables. For local
development, we also load ``.env.local`` into missing process environment
keys so commands such as ``python -m pytest`` and ``uvicorn ...`` work
without shell-specific preloading.
"""

from __future__ import annotations

import os
from pathlib import Path


def load_dotenv_local(start: Path | None = None) -> None:
    """Load ``.env.local`` from the repo root if it exists.

    Existing environment variables win. This keeps deployed environments and
    explicitly exported shell values authoritative while making local defaults
    automatic.
    """

    env_path = _find_env_local(start or Path.cwd())
    if env_path is None:
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        parsed = _parse_env_line(raw_line)
        if parsed is None:
            continue
        key, value = parsed
        os.environ.setdefault(key, value)


def _find_env_local(start: Path) -> Path | None:
    current = start.resolve()
    if current.is_file():
        current = current.parent
    for directory in (current, *current.parents):
        candidate = directory / ".env.local"
        if candidate.is_file():
            return candidate
    return None


def _parse_env_line(line: str) -> tuple[str, str] | None:
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or "=" not in stripped:
        return None
    if stripped.startswith("export "):
        stripped = stripped.removeprefix("export ").lstrip()
    key, value = stripped.split("=", 1)
    key = key.strip()
    if not key:
        return None
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        value = value[1:-1]
    return key, value
