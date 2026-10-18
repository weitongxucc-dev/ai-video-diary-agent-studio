"""Exercise the packaged entry without binding a socket or loading app data."""
import sys

import pytest
import uvicorn

import server


@pytest.mark.parametrize('configured,expected', [(None, '127.0.0.1'), ('127.0.0.1', '127.0.0.1'), ('0.0.0.0', '0.0.0.0')])
def test_server_is_loopback_by_default_and_preserves_explicit_host(monkeypatch, configured, expected):
    if configured is None:
        monkeypatch.delenv('API_HOST', raising=False)
    else:
        monkeypatch.setenv('API_HOST', configured)
    monkeypatch.setattr(sys, 'argv', ['api-server'])
    monkeypatch.setattr(server, '_configure_env', lambda: None)
    captured = []
    monkeypatch.setattr(uvicorn, 'run', lambda *args, **kwargs: captured.append((args, kwargs)))
    server.main()
    assert captured[0][0] == ('app.main:create_app',)
    assert captured[0][1]['host'] == expected and captured[0][1]['factory'] is True
