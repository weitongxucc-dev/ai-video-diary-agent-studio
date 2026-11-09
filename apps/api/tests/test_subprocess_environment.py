import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from app.subprocess_environment import subprocess_environment


def test_selected_environment_does_not_inherit_secrets_or_node_options(monkeypatch, tmp_path):
    monkeypatch.setenv("AI_VIDEO_DIARY_LLM_KEY", "must-not-inherit")
    monkeypatch.setenv("NODE_OPTIONS", "--require=must-not-load")
    selected = {"PATH": os.environ["PATH"], "TMPDIR": str(tmp_path)}
    actual = subprocess_environment(selected)
    assert "AI_VIDEO_DIARY_LLM_KEY" not in actual and "NODE_OPTIONS" not in actual
    assert selected == {"PATH": os.environ["PATH"], "TMPDIR": str(tmp_path)}
    if sys.platform == "win32":
        assert actual["SYSTEMROOT"] == os.environ["SYSTEMROOT"]
        assert actual["TEMP"] == actual["TMP"] == str(tmp_path)
    else:
        assert actual == selected


def test_real_node_crypto_and_temporary_directory_work_with_isolated_environment(tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required for the actual child environment check")
    env = subprocess_environment({"PATH": os.environ["PATH"], "TMPDIR": str(tmp_path)})
    result = subprocess.run([node, "-e", "console.log(JSON.stringify({random:require('node:crypto').randomBytes(8).toString('hex'),temporary:require('node:os').tmpdir()}))"],
                            env=env, capture_output=True, text=True, check=True, timeout=10)
    data = json.loads(result.stdout)
    assert len(data["random"]) == 16
    assert Path(data["temporary"]).resolve() == tmp_path.resolve()
