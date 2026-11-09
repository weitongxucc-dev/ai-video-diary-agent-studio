from pathlib import Path
import shutil
import subprocess
import json
import sys
import pytest
from app.assistant.runtime_checks import runtime_bundle, runtime_bundle_readable, runtime_checks


@pytest.mark.skipif(sys.platform != "win32", reason="Windows npm command shims")
@pytest.mark.parametrize("suffix", [".cmd", ".ps1"])
def test_windows_npm_installation_uses_declared_cli_with_node(tmp_path, suffix):
    from app.assistant.runtime_checks import PI_SDK_VERSION, sdk_module

    package = tmp_path / "npm prefix 中文/node_modules/@earendil-works/pi-coding-agent"
    (package / "dist/bundle").mkdir(parents=True)
    (package / "dist/index.js").write_text("// SDK fixture")
    (package / "dist/bundle/cli.js").write_text(f"console.log('{PI_SDK_VERSION}')")
    (package / "package.json").write_text(json.dumps({
        "version": PI_SDK_VERSION, "bin": {"pi": "dist/bundle/cli.js"},
    }))
    shim = tmp_path / ("npm prefix 中文/pi" + suffix)
    shim.write_text("must never be executed by a shell")
    assert sdk_module(str(shim)) == package / "dist/index.js"
    checks = runtime_checks(Path(__file__).parents[1] / "agent_tools/diary_tools.ts", lambda: str(shim))
    assert checks[0]["status"] == "ready", checks
    assert checks[0]["version"] == PI_SDK_VERSION


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
