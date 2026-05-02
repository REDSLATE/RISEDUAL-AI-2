# Test Credentials

## Owner (RISEDUAL)
- Email: admin@risedual.ai
- Password: RiseDual2026!
- Role: owner
- Subscription: pro
- Can activate/deactivate users and grant/revoke Pro

## Admin
- Email: admin@risedual.ai
- Password: 
- Role: admin
- Subscription: pro

## Auth Method
- httpOnly secure cookies (primary)
- POST /api/auth/login → sets access_token + refresh_token cookies
- CORS: credentials: 'include' required on all fetch calls

## Developer API (Pro tier)
- `TEST_API_KEY` seeded in `/app/backend/.env` for
  `test_iteration132_public_developer_api.py`

## 3rd-party API keys in backend/.env
- `ALPHA_VANTAGE_API_KEY` — Alpha Vantage primary
- `ALPHA_VANTAGE_API_KEY_2` — Alpha Vantage fallback
- `EMERGENT_LLM_KEY` — Universal LLM key (OpenAI/Anthropic/Gemini)
- `SLACK_WEBHOOK_URL` — Operator Slack alerts (optional)
- `LANGFUSE_*` — Self-hosted observability (optional)
- `BENZINGA_API_KEY` — **EMPTY** by default. User to paste in from
  their Benzinga dashboard. Free tier. Companion envs:
  `BENZINGA_DAILY_CALL_CEILING=500`, `BENZINGA_MIN_INTERVAL_SECONDS=2`,
  `BENZINGA_CACHE_TTL_SECONDS=300`.
