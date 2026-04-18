# Test Credentials

## Admin (primary owner)
- Email: admin@risedual.ai
- Password: RiseDual2026!
- Role: owner
- Subscription: pro

## DEACTIVATED (do NOT re-enable)
- managingdirector@redslateholdings.com — account is deactivated per user directive.

## Auth Method
- httpOnly secure cookies (primary)
- POST /api/auth/login → sets access_token + refresh_token cookies
- CORS: credentials: 'include' required on all fetch calls
