"""Assemble a print PDF + cover poster from Remotion's per-scene still PNGs.

The stills ARE the render's source of truth for print (spec §6). Mirrors the
geometry/resolution the legacy renderer used (render.py: resolution=150.0,
poster = first page as JPEG q88).

``Image.init()`` is called first: Pillow's PDF writer looks up
``Image.SAVE["JPEG"]`` directly, and in a fresh process only ``preinit`` may
have run — the spike hit a ``KeyError: 'JPEG'`` without this.
"""
from __future__ import annotations

from PIL import Image


def assemble_pdf_from_stills(still_paths: list[str], pdf_path: str,
                             poster_path: str | None = None) -> int:
    if not still_paths:
        raise ValueError("assemble_pdf_from_stills: no stills provided")
    Image.init()  # ensure all save plugins (incl. JPEG) are registered
    pages = [Image.open(p).convert("RGB") for p in still_paths]
    pages[0].save(pdf_path, save_all=True, append_images=pages[1:],
                  resolution=150.0)
    if poster_path is not None:
        pages[0].save(poster_path, format="JPEG", quality=88)
    return len(pages)
