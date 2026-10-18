"""Short-lived same-account pairing; no member, workspace or seat creation."""
from contextlib import closing, contextmanager
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
import hashlib
from pathlib import Path
import secrets
import sqlite3
from collections.abc import Callable

from app.auth import AuthPrincipal
from app.companion.identity import CompanionIdentity
from app.device_accounts import DeviceAccountError, DeviceAccountService
from app.models import utc_now


@dataclass(frozen=True)
class PairingTicket:
    token: str = field(repr=False)
    expires_at: str
    workspace_id: str
    device_id: str


class CompanionPairingService:
    def __init__(self, database_path: str | Path, *, clock: Callable[[], datetime] = utc_now):
        self.database_path, self.clock = Path(database_path), clock
        with self._connect() as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS companion_pairing_tickets (
                token_sha256 TEXT PRIMARY KEY, device_id TEXT NOT NULL,
                workspace_id TEXT NOT NULL, member_id TEXT NOT NULL,
                created_at TEXT NOT NULL, expires_at TEXT NOT NULL,
                accepted_at TEXT, revoked_at TEXT)""")

    @contextmanager
    def _connect(self):
        with closing(sqlite3.connect(self.database_path)) as conn:
            conn.row_factory = sqlite3.Row
            with conn:
                yield conn

    def create(self, identity: CompanionIdentity) -> PairingTicket:
        token, now = secrets.token_urlsafe(32), self.clock()
        expires_at = (now + timedelta(minutes=10)).isoformat()
        with self._connect() as conn:
            conn.execute("INSERT INTO companion_pairing_tickets "
                "(token_sha256,device_id,workspace_id,member_id,created_at,expires_at) VALUES (?,?,?,?,?,?)",
                (hashlib.sha256(token.encode()).hexdigest(), identity.device_id,
                 identity.workspace_id, identity.member_id, now.isoformat(), expires_at))
        return PairingTicket(token, expires_at, identity.workspace_id, identity.device_id)

    def accept(self, principal: AuthPrincipal, token: str, accounts: DeviceAccountService) -> dict[str, str]:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM companion_pairing_tickets WHERE token_sha256=?",
                (hashlib.sha256(token.encode()).hexdigest(),)).fetchone()
            if (row is None or not principal.authenticated or not principal.access_token
                    or row['member_id'] != principal.user_id or row['device_id'] != accounts.device_id):
                raise DeviceAccountError('forbidden', 'pairing ticket does not belong to this account and device')
            if row['revoked_at']:
                raise DeviceAccountError('conflict', 'pairing ticket has expired or was revoked')
            # The ticket supplies the original scope; the cloud permission owner
            # rechecks this same account/device/workspace before accepting/replaying.
            accounts.require_workspace_access(replace(principal, workspace_id=row['workspace_id']))
            if row['accepted_at'] is None:
                if datetime.fromisoformat(row['expires_at']) <= self.clock():
                    raise DeviceAccountError('conflict', 'pairing ticket has expired or was revoked')
                conn.execute('UPDATE companion_pairing_tickets SET accepted_at=? WHERE token_sha256=?',
                    (self.clock().isoformat(), row['token_sha256']))
            return {'target_workspace_id': row['workspace_id'], 'member_id': row['member_id'], 'device_id': row['device_id']}

    def revoke(self, identity: CompanionIdentity, token: str) -> bool:
        with self._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            row = conn.execute('SELECT * FROM companion_pairing_tickets WHERE token_sha256=?',
                (hashlib.sha256(token.encode()).hexdigest(),)).fetchone()
            if row is None or (row['device_id'], row['workspace_id'], row['member_id']) != (
                identity.device_id, identity.workspace_id, identity.member_id,
            ):
                raise DeviceAccountError('forbidden', 'pairing ticket does not belong to this binding')
            if row['revoked_at'] is not None:
                return False
            conn.execute('UPDATE companion_pairing_tickets SET revoked_at=? WHERE token_sha256=?',
                (self.clock().isoformat(), row['token_sha256']))
            return True
