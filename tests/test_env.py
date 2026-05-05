from __future__ import annotations

from flashback.env import _parse_env_line


def test_parse_env_line_strips_utf8_bom_from_key():
    assert _parse_env_line("\ufeffDATABASE_URL=postgres://example") == (
        "DATABASE_URL",
        "postgres://example",
    )
