"""Presigned-URL transfer for the tribute render worker.

The worker holds NO AWS credentials. Node mints presigned GET (prime photo) +
PUT (video, pdf) URLs and stores them on the row; this module only does plain
HTTP GET/PUT through them. (Node must sign the PUTs for the content-types below,
or sign without enforcing content-type.)
"""
from __future__ import annotations

import io
import urllib.request

from PIL import Image


def download_image(url: str, *, timeout: float = 30.0) -> Image.Image:
    """GET a presigned URL and return it as an RGB image."""
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        data = resp.read()
    return Image.open(io.BytesIO(data)).convert("RGB")


def upload_file(url: str, path: str, *, content_type: str,
                timeout: float = 180.0) -> int:
    """PUT a local file to a presigned URL. Returns the HTTP status."""
    with open(path, "rb") as f:
        data = f.read()
    req = urllib.request.Request(
        url, data=data, method="PUT",
        headers={"Content-Type": content_type, "Content-Length": str(len(data))},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return int(resp.status)
