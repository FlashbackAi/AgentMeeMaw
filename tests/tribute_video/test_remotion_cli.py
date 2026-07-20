import subprocess

import pytest

from flashback.tribute_video import remotion_cli


def test_builds_expected_argv(monkeypatch, tmp_path):
    seen = {}

    def fake_run(argv, check=False, capture_output=True, timeout=None, cwd=None):
        seen["argv"] = argv
        seen["cwd"] = cwd
        return subprocess.CompletedProcess(argv, 0, stdout=b"", stderr=b"")

    monkeypatch.setattr(remotion_cli.subprocess, "run", fake_run)
    remotion_cli.run_remotion(
        props_path=str(tmp_path / "p.json"), public_dir=str(tmp_path / "pub"),
        out_mp4=str(tmp_path / "o.mp4"), stills_dir=str(tmp_path / "st"),
        project_dir=str(tmp_path / "remotion"), node_bin="node")
    argv = seen["argv"]
    assert argv[0] == "node"
    assert argv[1].endswith("render.mjs")
    for flag in ("--props", "--public-dir", "--out-mp4", "--stills-dir"):
        assert flag in argv
    assert seen["cwd"] == str(tmp_path / "remotion")


def test_nonzero_exit_raises(monkeypatch, tmp_path):
    def fake_run(argv, check=False, capture_output=True, timeout=None, cwd=None):
        return subprocess.CompletedProcess(argv, 2, stdout=b"", stderr=b"boom")

    monkeypatch.setattr(remotion_cli.subprocess, "run", fake_run)
    with pytest.raises(remotion_cli.RemotionError):
        remotion_cli.run_remotion(
            props_path="p", public_dir="pub", out_mp4="o", stills_dir="st",
            project_dir=str(tmp_path))


def test_default_project_dir_points_at_repo_remotion():
    d = remotion_cli.default_project_dir()
    assert d.replace("\\", "/").endswith("/remotion")
