"""Process-wide startup tweaks for local development.

On Windows, psycopg's async connections require a selector event loop.
Python imports this module before ``python -m uvicorn`` creates its
event loop, which keeps the documented local boot command working when
the package is installed in editable mode.
"""

from __future__ import annotations

import asyncio
import sys

from flashback.env import load_dotenv_local

load_dotenv_local()

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
