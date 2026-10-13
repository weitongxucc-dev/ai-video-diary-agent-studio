"""Dynamic runtime packages must be present even without static imports."""

import ast
from pathlib import Path


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
