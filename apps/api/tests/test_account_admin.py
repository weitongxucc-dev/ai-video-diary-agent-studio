import io
import json
from unittest import mock

import pytest

from app.account_admin import account_email, create_account, generated_password


def test_account_email_matches_product_signin_mapping():
    assert account_email(" Tester01 ") == (
        "u_9217416aa8bf36f275775392@account.ai-video-diary.local"
    )


@pytest.mark.parametrize("account", ["ab", "has space", "测试者", "a" * 33])
def test_account_email_rejects_invalid_product_accounts(account):
    with pytest.raises(ValueError):
        account_email(account)


def test_generated_password_meets_product_minimum():
    password = generated_password()
    assert len(password) == 18
    assert any(char.islower() for char in password)
    assert any(char.isupper() for char in password)
    assert any(char.isdigit() for char in password)


def test_create_account_uses_admin_api_without_exposing_product_email():
    response = mock.MagicMock()
    response.__enter__.return_value = response
    response.read.return_value = json.dumps({"id": "user-123"}).encode()

    with mock.patch("app.account_admin.urllib.request.urlopen", return_value=response) as urlopen:
        user_id = create_account(
            url="https://example.supabase.co/",
            admin_secret="service-secret",
            account="tester01",
            password="StrongPass123!",
        )

    assert user_id == "user-123"
    request = urlopen.call_args.args[0]
    assert request.full_url == "https://example.supabase.co/auth/v1/admin/users"
    assert request.headers["Authorization"] == "Bearer service-secret"
    assert request.headers["User-agent"] == "AI-Video-Diary-Account-Admin/0.1"
    payload = json.loads(io.BytesIO(request.data).read())
    assert payload["email_confirm"] is True
    assert payload["user_metadata"] == {"account": "tester01"}
    assert payload["email"].endswith("@account.ai-video-diary.local")


def test_create_account_does_not_send_new_secret_key_as_bearer():
    response = mock.MagicMock()
    response.__enter__.return_value = response
    response.read.return_value = b'{"id":"user-456"}'

    with mock.patch("app.account_admin.urllib.request.urlopen", return_value=response) as urlopen:
        create_account(
            url="https://example.supabase.co",
            admin_secret="sb_secret_admin-test",
            account="tester02",
            password="StrongPass123!",
        )

    request = urlopen.call_args.args[0]
    assert request.headers["Apikey"] == "sb_secret_admin-test"
    assert "Authorization" not in request.headers
