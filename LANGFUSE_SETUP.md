# Langfuse Self-Hosted Setup — Council v2 LLM Tracing

This guide takes you from zero to seeing per-call traces for the
Council v2 LLM panel (GPT-5.2 + Claude Sonnet 4.5 + Gemini 2.5
Flash) on your own infrastructure. ~10 minutes end-to-end.

## What this gives you

Every time the Council fires, Langfuse captures:

- The **prompt** sent to all three models (verbatim)
- Each model's **raw completion** + **parsed action / confidence /
  reason**
- Per-model **cost in USD** (the pre-computed cost we already
  track via `_LLM_COUNCIL_COST_USD` — Langfuse's auto-cost can't
  see Universal Key pricing, so we override it explicitly)
- The **weighted-vote winner** + per-model vote breakdown on the
  parent span
- **Failure mode visibility** — when one of the three models
  fails parsing or times out, the span is tagged `level=ERROR`
  with the exception type so you can see which provider is
  flaky over time

## Why self-hosted

The Council panel sees *every* signal that fires on the crypto
fleet — including signal confidence, regime tags, and the IP
contract's adversarial verdict. That data is IP-defensible
under Patent J / Dual-Stack architecture; it should not leave
your infrastructure. Self-hosted Langfuse keeps the trace data
inside the same trust boundary as your MongoDB and ChromaDB.

The downside: you run one more container. The compose file
makes that ~30 seconds of work.

## Setup

### 1. Start Langfuse

From the repo root:

```bash
docker compose -f docker-compose.langfuse.yml up -d
```

Wait ~15 seconds for the Postgres migrations:

```bash
docker logs -f langfuse-server | grep -E "(Listening|Error)"
# ✓ Look for: "Listening on port 3000"
# Press Ctrl+C once you see it.
```

### 2. Create your operator account

Open <http://localhost:3090> in a browser. The first signup is
the admin account — no email verification needed for the
self-hosted build.

After login, create a Project. Name doesn't matter; "RISEDUAL"
works.

### 3. Mint API keys

In the Langfuse UI: **Settings → API Keys → Create new API key**.

Copy the two values. They will look like:

- `pk-lf-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx` (public)
- `sk-lf-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx` (secret — only
  shown once)

### 4. Wire into the backend `.env`

Append to `/app/backend/.env`:

```bash
LANGFUSE_ENABLED=true
LANGFUSE_HOST=http://localhost:3090
LANGFUSE_PUBLIC_KEY=pk-lf-...
LANGFUSE_SECRET_KEY=sk-lf-...
```

> **Production note**: when running in Kubernetes / a real
> cluster, replace `localhost:3090` with the in-cluster service
> name (e.g. `http://langfuse-server.observability.svc:3000`).

### 5. Restart the backend

```bash
sudo supervisorctl restart backend
```

Watch the logs for the connection confirmation:

```
[langfuse] connected: http://localhost:3090
```

If you see `[langfuse] init failed (circuit-breaker active...)`,
the keys or host are wrong — fix the env and restart.

### 6. Trigger a Council round

The cleanest test is to manually run the crypto fleet with the
Council shadow engine enabled:

```bash
COOKIE=/tmp/admin_cookie.txt
API_URL=$(grep REACT_APP_BACKEND_URL /app/frontend/.env | cut -d '=' -f2)
curl -s -c $COOKIE -X POST "$API_URL/api/auth/login" \
  -H "Content-Type: application/json" \
  -d '{"email":"admin@risedual.ai","password":"RiseDual2026!"}' >/dev/null

# Flip Council shadow to LLM mode for one tick.
# (Default is "rule" for cost reasons — see PRD.md.)
COUNCIL_SHADOW_MODE=llm curl -s -b $COOKIE \
  -X POST "$API_URL/api/crypto/paper-bot/run"
```

Refresh <http://localhost:3090> → **Traces** tab. You should see
one `council_llm_panel` parent span per symbol that fired the
Council, with three `council_llm_*` generation children
(`council_llm_openai`, `council_llm_anthropic`,
`council_llm_gemini`).

Click any generation row to see the full prompt, raw completion,
parsed action/confidence/reason, and the pre-computed cost.

## What's instrumented

**Only the LLM path** of the Council shadow engine — the
`COUNCIL_SHADOW_MODE=llm` branch in
`services/research_shadow_engines.py`.

Specifically:

| Function | Span | Notes |
|----------|------|-------|
| `_run_council_llm` | `council_llm_panel` (`as_type=span`) | Parent — captures the symbol, weighted-vote winner, total cost, panel summary |
| `_run_single_council_model` | `council_llm_{provider}` (`as_type=generation`) | Child — captures prompt, raw completion, parsed action, per-model cost |

Other surfaces deliberately **not** instrumented (would add
complexity without value):

- The deterministic v1 rule-based council (no LLM, free already)
- The Adversarial Commander shadow path (also deterministic in
  shadow mode)
- The crypto bot's Strategist/Auditor (deterministic; LLM
  upgrade is on the roadmap but not built yet)

## Operations

### Disabling without removing the keys

Set `LANGFUSE_ENABLED=false` in `/app/backend/.env` and restart
the backend. The instrumentation becomes a no-op; bot
performance is unchanged. Re-flip to `true` to re-enable.

### Stopping the server

```bash
docker compose -f docker-compose.langfuse.yml down
```

Trace data is preserved in the `langfuse_db_data` volume.

### Wiping everything

```bash
docker compose -f docker-compose.langfuse.yml down -v
```

This drops the volume — all trace history is lost. Use only if
you want a clean slate (e.g. before a demo).

### Server outage / network partition

The instrumentation is wrapped in defensive try/except at every
level. If the Langfuse server goes down:

1. The first call after the outage fails (~5s timeout) and logs
   a single warning.
2. The circuit breaker activates for 5 minutes — no further
   reconnect attempts.
3. The bot keeps running normally with zero overhead.
4. After 5 minutes, one reconnect attempt is made; if it
   succeeds, tracing resumes.

This matches the discipline of every other observability
surface in the codebase: the bot loop is sacred.

### Cost budget

The Council LLM panel costs ~$0.011 per 3-model round (per
the existing `_LLM_COUNCIL_COST_USD` constants). Tracing adds
zero cost — Langfuse stores the trace, the LLM cost is the LLM
cost. The existing daily ceiling
(`SHADOW_COST_CEILING_USD_PER_DAY=5`) is unchanged.

The `langfuse-postgres` container disk usage grows by roughly
2 KB per Council round. At the steady-state crypto-fleet
cadence (~24 rounds/day × 7 symbols = 168 rounds/day), that's
~340 KB/day → ~125 MB/year. No retention pruning needed for
the foreseeable future; the Postgres volume should comfortably
hold years of traces.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| `[langfuse] init failed (circuit-breaker active...)` | Wrong keys, wrong host, or server down | Verify `curl http://localhost:3090/api/public/health` returns 200; double-check pk/sk in `.env`; restart backend |
| `[langfuse] disabled: LANGFUSE_PUBLIC_KEY/SECRET_KEY/HOST missing` | Env vars not set | Add all three keys to `backend/.env` and restart |
| Traces appear in UI but no costs | Pre-computed cost not flowing through | Check that `_LLM_COUNCIL_COST_USD[provider]` returns a non-zero value for the provider in question |
| Spans appear without children | Async context not propagating | Check Python version ≥ 3.11; the OpenTelemetry context propagation Langfuse v4.x relies on requires it |
| Backend startup hangs after adding keys | Wrong host (e.g. resolution failure) | Set `LANGFUSE_HOST` to `http://127.0.0.1:3090` instead of `localhost` to bypass DNS edge cases |

## What this is NOT

To set expectations crisply:

- **Not a security tool.** Use Semgrep / Bandit for SAST, gitleaks
  for secrets scanning. Langfuse won't catch a hardcoded API key
  or a SQL injection.
- **Not an APM.** Use Sentry for unhandled exceptions, Datadog or
  similar for infrastructure metrics. Langfuse doesn't track the
  bot's CPU, memory, or HTTP latency outside of LLM calls.
- **Not the audit chain.** Patent J's `decision_proof_chain`
  remains the legal-grade tamper-evident record. Langfuse stores
  the same data in plaintext for *operator debugging* —
  complementary, not redundant.
- **Not a replacement for tests.** A trace shows what *did*
  happen, not what *should* happen. Pytest is still the source
  of truth for correctness.

## When to revisit this

Two known limits we'll hit eventually:

1. **The Postgres volume needs an LRU policy after ~5 years** at
   the current cadence. Langfuse has built-in retention settings;
   apply them when total trace count crosses 5M (currently 0 →
   plenty of runway).
2. **If the Council moves to a per-user execution path** (i.e.
   end-users running their own bots), trace volume goes from
   ~168/day to ~168/day-per-user. At that point: enable
   `sample_rate=0.1` in the Langfuse client init (10% sampling)
   and add a dedicated retention job.

Until then, the setup above is fire-and-forget.
