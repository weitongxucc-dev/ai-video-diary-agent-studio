from __future__ import annotations

from app.app_factory import create_app, install_runtime_module_proxy


app = create_app()
install_runtime_module_proxy(__name__, app)
