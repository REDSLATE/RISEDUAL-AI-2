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
- httpOnly secure cookies (primary)
- POST /api/auth/login → sets access_token + refresh_token cookies
- CORS: credentials: 'include' required on all fetch calls
