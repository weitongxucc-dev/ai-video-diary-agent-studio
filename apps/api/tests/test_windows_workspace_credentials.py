"""Native credential isolation and refusal; never use real user keys."""
import sys
from uuid import uuid4

import pytest

from app import workspace_settings as store

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows Credential Manager")


@pytest.fixture
def owned_workspaces(monkeypatch):
    monkeypatch.setattr(store, "_WINDOWS_CREDENTIALS", True)
    workspaces = [f"credential-test-{uuid4().hex}" for _ in range(2)]
    try:
        yield workspaces
    finally:
        for workspace in workspaces:
            store.delete_workspace_secret(workspace, "llm_api_key")


def test_native_credentials_preserve_values_isolate_workspaces_and_evict_cache(owned_workspaces):
    first, second = owned_workspaces
    assert store.read_workspace_secret_fresh(first, "llm_api_key") is None
    value = "synthetic-密钥\nwith-newline"
    store.save_workspace_secret(first, "llm_api_key", value)
    store.save_workspace_secret(second, "llm_api_key", "synthetic-other")
    assert store.read_workspace_secret(first, "llm_api_key") == value
    assert store.read_workspace_secret_fresh(first, "llm_api_key", recoverable=True) == value
    store.save_workspace_secret(first, "llm_api_key", "synthetic-updated")
    assert store.read_workspace_secret(first, "llm_api_key") == "synthetic-updated"
    assert store.delete_workspace_secret(first, "llm_api_key") is True
    assert store.delete_workspace_secret(first, "llm_api_key") is False
    assert store.read_workspace_secret(first, "llm_api_key") is None
    assert store.read_workspace_secret(second, "llm_api_key") == "synthetic-other"


@pytest.mark.parametrize("operation", ["CredRead", "CredWrite", "CredDelete"])
def test_native_store_refusal_is_not_missing_or_plaintext_fallback(monkeypatch, owned_workspaces, operation):
    import pywintypes
    import win32cred

    def denied(*args):
        raise pywintypes.error(5, operation, "synthetic access denied")

    # Scope the refusal so fixture cleanup still uses the real native API.
    with monkeypatch.context() as patch:
        patch.setattr(win32cred, operation, denied)
        with pytest.raises(store.WorkspaceSecretStoreError):
            if operation == "CredRead":
                store.read_workspace_secret_fresh(owned_workspaces[0], "llm_api_key")
            elif operation == "CredWrite":
                store.save_workspace_secret(owned_workspaces[0], "llm_api_key", "synthetic-only")
            else:
                store.delete_workspace_secret(owned_workspaces[0], "llm_api_key")
