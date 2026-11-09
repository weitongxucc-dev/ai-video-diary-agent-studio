from pathlib import Path
import shutil
import subprocess
import pytest
from app.assistant.runtime_checks import runtime_bundle, runtime_bundle_readable, runtime_checks


@pytest.mark.parametrize("directory", ["agent_tools", "agent-tools"])
def test_source_and_deployed_bundle_share_extension_parent(tmp_path, directory):
    source = Path(__file__).parents[1]
    extension = tmp_path / directory / "diary_tools.ts"
    shutil.copytree(source / "agent_tools", extension.parent)
    shutil.copytree(source / "agent_runtime", tmp_path / "agent_runtime")
    assert runtime_bundle(extension) == tmp_path / "agent_runtime"
    assert runtime_bundle_readable(extension)
    (tmp_path / "agent_runtime/skills/local-edit/SKILL.md").unlink()
    assert not runtime_bundle_readable(extension)


def test_pi_executable_alone_does_not_enable_sdk(tmp_path, monkeypatch):
    pi = tmp_path / "pi"
    pi.write_text("fixture")
    pi.chmod(0o755)
    monkeypatch.setattr("app.assistant.runtime_checks.shutil.which", lambda *args, **kwargs: "/fixture/node")
    monkeypatch.setattr("app.assistant.runtime_checks.subprocess.run", lambda cmd, **kwargs:
                        subprocess.CompletedProcess(cmd, 0, "1.5.0" if cmd[0] == str(pi) else "v24.16.0", ""))
    check = runtime_checks(Path(__file__).parents[1] / "agent_tools/diary_tools.ts", lambda: str(pi))[0]
    assert check["status"] == "missing"
    assert "SDK" in check["reason"]
