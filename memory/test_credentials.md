# Test Credentials

## Admin / Owner (single account)
- Email: admin@risedual.ai
- Password: RiseDual2026!
- Role: owner
- Subscription: pro

## Auth Method
- httpOnly secure cookies (primary)
- POST /api/auth/login → sets access_token + refresh_token cookies
- CORS: credentials: 'include' required on all fetch calls

## Historical
- `managingdirector@redslateholdings.com` — **DELETED** (Feb 2026).
  Was the only `role: owner` account; after user-directed deactivation it
  broke broker live-execution because `_is_execution_allowed` strictly checks
  `role == "owner"`. Startup cleanup in `seed_admin()` removes any lingering
  row automatically.
