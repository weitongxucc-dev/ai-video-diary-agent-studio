"""Trusted operator CLI for provisioning invite-only desktop accounts."""

from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import os
import secrets
import string
import sys
import unicodedata
import urllib.error
import urllib.request

ACCOUNT_PATTERN = "letters, numbers, dot, dash or underscore"
ACCOUNT_DOMAIN = "account.ai-video-diary.local"


def validate_account(account: str) -> str:
    normalized = unicodedata.normalize("NFKC", account.strip()).lower()
    if not 3 <= len(normalized) <= 32 or any(
        character not in string.ascii_lowercase + string.digits + "._-"
        for character in normalized
    ):
        raise ValueError(f"account must be 3-32 {ACCOUNT_PATTERN}")
    return normalized


def account_email(account: str) -> str:
    normalized = validate_account(account)
    digest = hashlib.sha256(normalized.encode()).hexdigest()[:24]
    return f"u_{digest}@{ACCOUNT_DOMAIN}"


def generated_password() -> str:
    alphabet = string.ascii_letters + string.digits + "-_.!@"
    while True:
        value = "".join(secrets.choice(alphabet) for _ in range(18))
        if (
            any(char.islower() for char in value)
            and any(char.isupper() for char in value)
            and any(char.isdigit() for char in value)
        ):
            return value


def create_account(
    *,
    url: str,
    admin_secret: str,
    account: str,
    password: str,
) -> str:
    if len(password) < 10:
        raise ValueError("password must contain at least 10 characters")
    headers = {
        "apikey": admin_secret,
        "Content-Type": "application/json",
    }
    # Legacy service_role keys are JWTs and need the bearer header. New
    # sb_secret keys are API keys, not JWTs, and must stay in apikey only.
    if not admin_secret.startswith("sb_secret_"):
        headers["Authorization"] = f"Bearer {admin_secret}"
    request = urllib.request.Request(
        f"{url.rstrip('/')}/auth/v1/admin/users",
        data=json.dumps(
            {
                "email": account_email(account),
                "password": password,
                "email_confirm": True,
                "user_metadata": {"account": validate_account(account)},
            }
        ).encode(),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            payload = json.loads(response.read())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        raise RuntimeError(f"Supabase account creation failed: HTTP {exc.code} {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Supabase account service is unreachable: {exc.reason}") from exc
    user_id = payload.get("id") if isinstance(payload, dict) else None
    if not user_id:
        raise RuntimeError("Supabase did not return a user id")
    return str(user_id)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Create an invite-only AI Video Diary account.",
    )
    parser.add_argument("account", help="Product account name, not an email")
    parser.add_argument(
        "--generate-password",
        action="store_true",
        help="Generate and print a one-time initial password",
    )
    args = parser.parse_args()

    url = os.getenv("SUPABASE_URL", "").strip()
    admin_secret = os.getenv("SUPABASE_ADMIN_SECRET_KEY", "").strip()
    if not url or not admin_secret:
        parser.error("set SUPABASE_URL and SUPABASE_ADMIN_SECRET_KEY in this terminal")

    password = generated_password() if args.generate_password else getpass.getpass(
        "Initial password (10+ characters): "
    )
    if not args.generate_password:
        confirmation = getpass.getpass("Confirm password: ")
        if password != confirmation:
            parser.error("passwords do not match")

    try:
        normalized = validate_account(args.account)
        user_id = create_account(
            url=url,
            admin_secret=admin_secret,
            account=normalized,
            password=password,
        )
    except (ValueError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(f"Created account: {normalized}")
    print(f"User id: {user_id}")
    if args.generate_password:
        print(f"Initial password (show once): {password}")
    print("The tester only needs the account name and initial password.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
