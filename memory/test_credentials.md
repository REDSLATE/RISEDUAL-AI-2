# Test Credentials

## Super Admin (merged account — the only active privileged account)
- Email: admin@risedual.ai
- Password: RiseDual2026!
- Role: owner (promoted from admin on Feb 18, 2026 during Red Slate merge)
- Subscription: pro (Pro Max plan, 50k monthly credits, 948 balance)
- Powers: full admin panel, user activate/deactivate, grant/revoke Pro, LIVE broker
  access (Kraken already connected), live-trade gate
- Kraken: LIVE connected (12 coins — BTC, ETH, ETH2.S, HBAR, USDC, etc.)
- Paper trading: $80,886 cash, 6 positions, 32 trades

## Red Slate (DEACTIVATED — merged into admin@risedual.ai)
- Email: managingdirector@redslateholdings.com
- Password: RiseDual2026!  (cannot log in — is_active=false)
- Role: merged (inert audit shell, does not pass any permission check)
- DO NOT use for testing — all its data was migrated to admin@risedual.ai.
  Kept only as audit trail.

## Auth Method
- httpOnly secure cookies (primary)
- POST /api/auth/login → sets access_token + refresh_token cookies
- CORS: credentials: 'include' required on all fetch calls
- Also accepts Authorization: Bearer <access_token> for API/testing
