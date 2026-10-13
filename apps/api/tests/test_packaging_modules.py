"""Dynamic runtime packages must be present even without static imports."""

import ast
import hashlib
import shutil
from pathlib import Path

import pytest


def test_hidden_imports_include_nested_runtime_packages(tmp_path):
    application = tmp_path / "app"
    (application / "media" / "executors").mkdir(parents=True)
    for relative in (
        "__init__.py", "main.py", "media/__init__.py",
        "media/jobs.py", "media/executors/__init__.py",
        "media/executors/registered_preview.py",
    ):
        (application / relative).write_text("")
    (application / "media" / "fixture.json").write_text("{}")

    spec = Path(__file__).parents[1] / "pyinstaller.spec"
    tree = ast.parse(spec.read_text())
    function = next(node for node in tree.body
                    if isinstance(node, ast.FunctionDef) and node.name == "_application_modules")
    namespace = {"Path": Path}
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(spec), "exec"), namespace)

    assert namespace["_application_modules"](application) == [
        "app.main", "app.media", "app.media.executors",
        "app.media.executors.registered_preview", "app.media.jobs",
    ]


def declared_data_inputs():
    api_root = Path(__file__).parents[1]
    spec = api_root / "pyinstaller.spec"
    tree = ast.parse(spec.read_text())
    declaration = next(node for node in tree.body if isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name) and node.target.id == "_ASSET_DIRS")
    resources = eval(compile(ast.Expression(declaration.value), str(spec), "eval"), {"_SPEC_DIR": api_root})
    analysis = next(node for node in tree.body if isinstance(node, ast.Assign)
        and isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Name) and node.value.func.id == "Analysis")
    data_argument = next(keyword.value for keyword in analysis.value.keywords if keyword.arg == "datas")
    assert any(isinstance(node, ast.Name) and node.id == "_ASSET_DIRS" for node in ast.walk(data_argument))
    return [(Path(source), Path(destination)) for source, destination in resources]


def test_media_script_bare_sources_have_exact_frozen_layout_and_bytes(tmp_path):
    inputs = declared_data_inputs()
    assert [(source.relative_to(Path(__file__).parents[1]), destination) for source, destination in inputs] == [
        (Path("app/media_script.py"), Path("app")),
        (Path("model_workers/media_script_worker.py"), Path("model_workers")),
    ]
    frozen = tmp_path / "_MEIPASS"
    for source, relative in inputs:
        target = frozen / relative / source.name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        assert hashlib.sha256(target.read_bytes()).digest() == hashlib.sha256(source.read_bytes()).digest()
    module_file = frozen / "app" / "media_script.py"
    worker = module_file.parents[1] / "model_workers" / "media_script_worker.py"
    assert worker.is_file()
    # Missing resources fail as missing files, with no invented fingerprint.
    worker.unlink()
    with pytest.raises(FileNotFoundError):
        worker.read_bytes()
