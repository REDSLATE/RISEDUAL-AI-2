# Test Credentials

## Owner (REDSLATE)
- Email: managingdirector@redslateholdings.com
- Password: RedSlate2026!
- Role: owner
- Subscription: pro
- Can activate/deactivate users and grant/revoke Pro

## Admin
- Email: admin@risedual.ai
- Password: RiseDual2026!
- Role: admin
- Subscription: pro

## Auth Method
- Primary: httpOnly cookies (access_token + refresh_token) set by server on login/register
- Fallback: Bearer token via localStorage (backward compat)
- POST /api/auth/login with credentials:'include' returns JSON body AND sets cookies
- All authenticated endpoints accept both cookie and Bearer token auth
