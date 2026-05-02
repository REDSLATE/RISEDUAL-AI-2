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

## 3rd-party API keys in backend/.env
- `ALPHA_VANTAGE_API_KEY` — primary key (War Room + NEWS_SHOCK sentiment feeder)
- `ALPHA_VANTAGE_API_KEY_2` — fallback
- `BENZINGA_API_KEY` — News API (free tier). Companion envs:
  `BENZINGA_DAILY_CALL_CEILING=500`, `BENZINGA_MIN_INTERVAL_SECONDS=2`,
  `BENZINGA_CACHE_TTL_SECONDS=300`
- `AV_NEWS_SENTIMENT_DAILY_CEILING=600`,
  `AV_NEWS_SENTIMENT_MIN_INTERVAL_SECONDS=1.5` — sentiment feeder budget
- `EMERGENT_LLM_KEY`, `SLACK_WEBHOOK_URL`, `LANGFUSE_*` (optional)
- `TEST_API_KEY` — Pro-tier developer API key for test suite
