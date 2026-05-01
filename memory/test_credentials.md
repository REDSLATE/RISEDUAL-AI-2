# Test Credentials

## Owner (RISEDUAL)
- Email: admin@risedual.ai
- Password: RiseDual2026!
- Role: owner
- Subscription: pro
- Can activate/deactivate users and grant/revoke Pro

## Notes on the "Admin" tier
The Feb 2026 single-owner consolidation removed the separate
``role=admin`` tier — all privileged actions are gated on
``role=owner``. The canonical credential above holds owner rights.
Tests that historically asserted a non-owner "admin" is forbidden
should use a freshly-registered regular user's token instead
(see ``tests/test_admin_workspace.py`` for the pattern).

## Auth Method
- httpOnly secure cookies (primary)
- POST /api/auth/login → sets access_token + refresh_token cookies
- CORS: credentials: 'include' required on all fetch calls

## Brute-force rate limit
- 5 failed (client_ip, email) attempts → 15 min lockout
- Client IP is extracted via ``X-Forwarded-For`` (first entry) →
  ``X-Real-IP`` → ``request.client.host`` (in that order) so the
  ingress pod's IP never collapses distinct users into one bucket.
- The pytest session autouse fixture
  ``_clear_brute_force_lockouts_at_session_start`` wipes
  ``login_attempts`` at the start of every test session so stale
  lockouts never bleed between runs.

## Env vars backing the seed (backend/.env)
- ``OWNER_EMAIL`` — defaults to ``admin@risedual.ai``
- ``OWNER_PASSWORD`` — ``RiseDual2026!``
- ``JWT_SECRET`` — 64-char hex, unchanged across restarts
