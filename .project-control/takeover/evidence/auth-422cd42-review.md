# QA01: AUTH invitation slice `422cd42`

## Frozen candidate and verdict

- `422cd4248c2a762a4024165627b66c04e39be8b3`, parent `96c28e2`; archived at `work/qa-011/output/auth-422cd42`.
- **PASS for the pre-provisioned-device invitation SQL and Python RPC adapter.** No device-registration root, native credential handoff, local API consumer, cloud migration, or end-to-end desktop acceptance is in this candidate.

## Evidence and compatibility

- Isolated actual PostgreSQL fixture applies all migration files and runs `test_device_accounts.py`: **6 passed**. The fixture checks accepted owner seat plus four members, concurrent fifth/sixth acceptance serialization on the device row, rejected sixth seat, repeated acceptance, wrong device/proof, revoked/expired token, direct table write denial, reserved workspace protection, and transaction rollback after a forced post-seat failure.
- The migration replaces `bootstrap_personal_workspace(text)` without changing its function signature or authenticated execute grant. It narrows selection to `kind='personal'`, retains the original personal workspace, and uses a partial personal-owner uniqueness index. Thus a reserved member space does not replace first-login personal bootstrap.
- Device insertion trigger creates its owner as the first active member. The invite and acceptance RPCs lock the device row before seat counts; acceptance moves the target owner to the accepting member, removes the former owner's workspace membership, and revokes competing invites for that target in one transaction.
- Ordinary authenticated role cannot write the device/invite tables directly. RPCs independently use `auth.uid()` and SHA-256 proof comparison; SQL functions are only granted to `authenticated`. The Python adapter sends the verified principal's bearer JWT plus native `DeviceCredential`; it has no service-role key. `DeviceCredential` and `DeviceInvite` hide proof/token from repr, but this is not a transport log guarantee.
- The current integration baseline `8ef879e` has the same earlier migration set as this candidate; the new migration is additive to that set. Existing `AuthPrincipal` contains the `authenticated` and `access_token` fields used by the adapter. No integration consumer was exercised.

## Remaining product boundary

`DeviceAccountService` is not wired into the local API, and `AuthService.authenticate()` currently requires a workspace membership before returning an `AuthPrincipal`. A first-time invitee without a personal workspace therefore cannot enter these calls through that existing local authentication method. A separate verified-token acceptance entry point or first-login path is needed for the 0.11 desktop journey. Direct Supabase RPC may work with a valid JWT and trusted proof, but no desktop path was tested. The cloud database and real native proof custody remain unverified.
