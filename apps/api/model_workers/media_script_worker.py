"""Single-process Python entrypoint. OS Seatbelt, not this wrapper, isolates code."""

import json
import ctypes
import resource
import os
import sys
from pathlib import Path


def main() -> None:
    if os.getpid() != os.getsid(0) or os.getpid() != os.getpgrp():
        raise RuntimeError("media script must retain the registered session leader")
    request = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    # Trusted bootstrap applies the entire policy exactly once. No model source,
    # inputs or Pillow code is evaluated before this succeeds. sandbox_init is
    # a deprecated macOS interface; failure never falls back to unrestricted exec.
    profile = Path(sys.argv[2]).read_bytes()
    sandbox = ctypes.CDLL("/usr/lib/libsandbox.1.dylib")
    sandbox.sandbox_init.argtypes = [ctypes.c_char_p, ctypes.c_uint64, ctypes.POINTER(ctypes.c_char_p)]
    sandbox.sandbox_init.restype = ctypes.c_int
    error = ctypes.c_char_p()
    if sandbox.sandbox_init(profile, 0, ctypes.byref(error)) != 0:
        raise RuntimeError("cannot install media script OS isolation")
    resource.setrlimit(resource.RLIMIT_FSIZE, (request["max_output_file_bytes"], request["max_output_file_bytes"]))
    # -I -S prevents user-site, .pth and sitecustomize execution. Only the
    # trusted Pillow parent is added; sibling packages remain OS-inaccessible.
    sys.path.append(request["pillow_parent"])
    os.chdir(request["output_directory"])
    namespace = {
        "__name__": "__main__",
        "__file__": request["script_path"],
        "parameters": request["parameters"],
        "inputs": request["inputs"],
        "outputs": request["outputs"],
    }
    source = Path(request["script_path"]).read_text(encoding="utf-8")
    exec(compile(source, request["script_path"], "exec"), namespace)


if __name__ == "__main__":
    main()
