"""Complete an explicitly selected child environment with Windows OS paths."""
import os
import sys
from collections.abc import Mapping


def subprocess_environment(selected: Mapping[str, str]) -> dict[str, str]:
    environment = dict(selected)
    if sys.platform == "win32":
        for name in ("SYSTEMROOT", "WINDIR"):
            if name in os.environ:
                environment.setdefault(name, os.environ[name])
        for name in ("TEMP", "TMP"):
            temporary = environment.get("TMPDIR", os.environ.get(name))
            if temporary is not None:
                environment.setdefault(name, temporary)
    return environment
