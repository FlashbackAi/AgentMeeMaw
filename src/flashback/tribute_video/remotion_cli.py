"""Invoke the bundled Remotion Node project as a subprocess (like ffmpeg).

No DB/S3/secrets — pure props.json -> mp4 + stills. The worker owns art
generation, prop assembly, and the S3 PUTs; this module only shells out.
``FLASHBACK_REMOTION_DIR`` overrides the project location (deployed hosts).
"""
from __future__ import annotations

import os
import subprocess


class RemotionError(RuntimeError):
    pass


def default_project_dir() -> str:
    env = os.environ.get("FLASHBACK_REMOTION_DIR")
    if env:
        return env
    # this file: <repo>/src/flashback/tribute_video/remotion_cli.py -> 4 up = repo
    here = os.path.abspath(__file__)
    repo = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(here))))
    return os.path.join(repo, "remotion")


def run_remotion(*, props_path: str, public_dir: str, out_mp4: str,
                 stills_dir: str, project_dir: str | None = None,
                 node_bin: str = "node", timeout: float = 900.0) -> None:
    proj = project_dir or default_project_dir()
    script = os.path.join(proj, "render.mjs")
    argv = [node_bin, script, "--props", props_path, "--public-dir", public_dir,
            "--out-mp4", out_mp4, "--stills-dir", stills_dir]
    proc = subprocess.run(argv, check=False, capture_output=True,
                          timeout=timeout, cwd=proj)
    if proc.returncode != 0:
        stderr = (proc.stderr or b"")[-1200:].decode("utf-8", "replace")
        raise RemotionError(f"remotion exit {proc.returncode}: {stderr}")
