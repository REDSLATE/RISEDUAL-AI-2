# Test Credentials

## Admin
- Email: admin@risedual.ai
- Password: RiseDual2026!
- Role: admin
- Subscription: pro

## Test User
- Register any new user via POST /api/auth/register
- Default subscription_status: free

## Auth Endpoints
- POST /api/auth/register {email, password, name}
- POST /api/auth/login {email, password}
- POST /api/auth/logout
- GET /api/auth/me (Authorization: Bearer <token>)
- POST /api/auth/refresh {refresh_token}

## Auth Method
- Bearer token via localStorage (NOT cookies)
- Login/register returns access_token + refresh_token in response body
- Send Authorization: Bearer <access_token> header for authenticated requests
