"""Themes layer for Legacy Mode.

User-facing groupings of moments — universal (seeded per person) and
emergent (auto-discovered by the Thread Detector). See CLAUDE.md and
the migration 0020 for the full contract.
"""

from flashback.themes.universal import (
    UNIVERSAL_THEME_SLUGS,
    UNIVERSAL_THEMES,
    UniversalTheme,
)

__all__ = [
    "UNIVERSAL_THEME_SLUGS",
    "UNIVERSAL_THEMES",
    "UniversalTheme",
]
