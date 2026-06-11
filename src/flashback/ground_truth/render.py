"""Render persons.ground_truth for each consumer (one helper, many readers).

Renders only fields that exist — silent on unknowns, never
"region: unknown". Audiences:

* ``extraction`` / ``responder`` — line-per-field text for an XML block.
* ``portrait``  — comma-joinable descriptor fragments for the portrait
  prompt. Ethnicity is never stated; the image model derives it from
  region + era + cultural context (spec §1).
* ``scene``     — one short "Setting context: ..." line appended on
  scene compose/regenerate.
"""

from __future__ import annotations

from typing import Any, Literal

Audience = Literal["extraction", "portrait", "scene", "responder"]

_PORTRAIT_ORDER = (
    "region", "birth_era", "cultural_context", "attire",
    "distinctive_features", "build", "setting_type",
)
_TEXT_ORDER = (
    "region", "birth_era", "setting_type", "attire",
    "distinctive_features", "build", "cultural_context",
    "era_span", "languages",
)


def render_ground_truth_block(
    ground_truth: dict[str, Any] | None, audience: Audience
) -> str:
    values = _known_values(ground_truth)
    if not values:
        return ""
    if audience in ("extraction", "responder"):
        return "\n".join(
            f"{key}: {_as_text(values[key])}"
            for key in _TEXT_ORDER
            if key in values
        )
    if audience == "portrait":
        return ", ".join(_portrait_fragments(values))
    return _scene_line(values)


def _known_values(ground_truth: dict[str, Any] | None) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, entry in (ground_truth or {}).items():
        if isinstance(entry, dict) and entry.get("value") not in (None, "", []):
            out[key] = entry["value"]
    return out


def _as_text(value: Any) -> str:
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    return str(value)


def _portrait_fragments(values: dict[str, Any]) -> list[str]:
    fragments: list[str] = []
    for key in _PORTRAIT_ORDER:
        if key not in values:
            continue
        text = _as_text(values[key])
        if key == "region":
            fragments.append(f"from {text}")
        elif key == "birth_era":
            fragments.append(f"born in the {text}")
        elif key == "attire":
            fragments.append(f"typically wearing {text}")
        elif key == "build":
            fragments.append(f"{text} build")
        elif key == "setting_type":
            fragments.append(f"{text} background")
        else:  # cultural_context, distinctive_features
            fragments.append(text)
    return fragments


def _scene_line(values: dict[str, Any]) -> str:
    parts: list[str] = []
    if "region" in values:
        parts.append(_as_text(values["region"]))
    era = values.get("era_span") or values.get("birth_era")
    if era:
        parts.append(f"{_as_text(era)} era")
    if "setting_type" in values:
        parts.append(f"{_as_text(values['setting_type'])} setting")
    if "cultural_context" in values:
        parts.append(_as_text(values["cultural_context"]))
    if not parts:
        return ""
    return "Setting context: " + ", ".join(parts) + "."
