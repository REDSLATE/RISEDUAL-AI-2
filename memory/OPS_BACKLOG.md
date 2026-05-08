# Operational Backlog — Known Limits & Accepted Trade-offs

Tracking document for operational characteristics that are **NOT
bugs to fix** but **accepted trade-offs** or **future-work items**.
Each entry carries a deliberate rationale so future operators
don't re-panic over known behaviour.

Scope: things that would show up in a code review or deployment
readiness check as "warnings" but have been consciously accepted
or deferred behind a specific trigger condition.

---

## OPS-001 — `/tmp/uploads/` chunked-upload loss on pod restart

**Discovered**: 2026-04-30 deployment readiness audit
**Owner**: Backend / infra
**Status**: **ACCEPTED (not scheduled)**
**Severity**: Low

### What happens

`routes/media.py` uses `/tmp/uploads/{upload_id}/` for chunked
file uploads during the assembly phase. Kubernetes wipes `/tmp`
on pod restart (node scheduling events, rolling deploys,
supervisor restart cycle). If a pod restart occurs between the
first chunk and the last, the in-flight chunks are lost and the
uploader sees a 500 on the next chunk's POST.

### Why this is acceptable

1. **Admin-only endpoint.** The chunked upload path is protected
   behind the admin-media router; no end-user exposure. Blast
   radius is one operator re-uploading one file.
2. **Short critical window.** A typical 100 MB upload completes
   in < 30 seconds on normal connectivity. The probability of a
   pod restart intersecting that window is effectively zero
   during normal operations.
3. **Graceful failure mode.** The client already treats the
   upload as atomic — a failed chunk surfaces a clean error,
   not corruption. No need for resume-from-last-good-chunk
   logic because restart-from-zero is fast enough.
4. **Fix cost outweighs benefit.** Moving chunks to persistent
   storage (a PVC-backed volume, or streaming directly to S3)
   adds a new failure mode and ~200 lines of code to solve a
   problem that has never once been observed in practice.

### Escalation trigger

**Revisit if any of the following happen:**

- A real operator reports losing an upload due to a restart
- Upload file size SLO moves above 500 MB (where 30-second
  uploads become 3-minute uploads — restart window is no longer
  effectively zero)
- The endpoint is exposed beyond admin scope (e.g. a
  user-facing "upload your trading journal" feature)

### Reference

- File: `routes/media.py:75-105`
- Guarded by: path-traversal regex + realpath prefix check (see
  CHANGELOG 2026-04-30 "Path-traversal hardening")

---

## OPS-002 — External RSS feed DNS blocked in preview pod

**Discovered**: 2026-04-30 deployment readiness audit
**Owner**: Data / operations
**Status**: **ACCEPTED (mitigated by fallback)**
**Severity**: Low (preview) / unknown (production — TBD on first deploy)

### What happens

`services/world_events_service.py` scrapes RSS from
`feeds.reuters.com` and `feeds.apnews.com`. DNS resolution for
both fails inside the Emergent preview pod with `[Errno -2]
Name or service not known`. The scraper catches the exception
cleanly and moves on; the world-events feature continues
running from its other sources.

Log spam rate: ~4 error lines per hour per source (2 sources,
30-minute fetch interval, 2 attempts per fetch).

### Why this is acceptable

1. **The scraper is multi-source by design.** Reuters and AP
   are two of ~8 configured sources. The remaining sources
   (WSJ, Bloomberg, FT — via their own endpoints — and some
   direct SEC/Fed feeds) cover the same information space.
   World-events continues to produce meaningful output with
   Reuters + AP blocked.
2. **Not security-critical or trading-critical.** World events
   inform narrative context in the admin dashboard; they do
   not gate any trading decision. Zero P&L impact if this
   stays broken forever.
3. **Log spam is visibility, not corruption.** The errors are
   logged at the right level (WARNING, not ERROR on the bot
   loop). Grep-based log analysis still finds real problems.

### Escalation trigger

**Revisit if any of the following happen:**

- The same DNS block exists in the production deployment
  (TBD — verify after first deploy)
- World-events source diversity drops below 5 working sources
  (i.e., more than half the providers are blocked)
- A trading feature becomes dependent on world-events output

### Optional mitigation (if we choose to)

Suppress the WARNING-level spam for these two hosts via
log-filter config. Keep the fetch attempts (so if DNS is ever
unblocked we pick them up automatically), just stop writing to
`backend.err.log` for these specific hosts. ~10 lines of
logging-filter config. Not worth it right now.

### Reference

- File: `services/world_events_service.py`
- Observed rate: ~4 WARNINGs/hour/source, zero downstream
  failure, zero P&L impact

---

## OPS-003 — ML dependency container size

**Discovered**: 2026-04-30 deployment readiness audit
**Owner**: Infra / platform
**Status**: **ACCEPTED (no SLO impact)**
**Severity**: Low

### What happens

`requirements.txt` includes heavy ML packages used at runtime
for inference against pre-trained `.joblib` models stored in
`backend/models/`:

| Package | Approximate wheel size |
|---|---|
| `scikit-learn==1.8.0` | ~40 MB |
| `scipy` | ~90 MB |
| `xgboost` | ~40 MB |
| `chromadb` + deps | ~200 MB |
| `lancedb` | ~60 MB |
| `huggingface_hub` + `tokenizers` | ~200 MB |
| `torch` (if pulled transitively) | ~800 MB |

Total container size impact: **+500-800 MB** over a pure
FastAPI+Mongo baseline. Cold-start time: **+5-10 seconds** for
package import graph vs. a minimal image.

### Why this is acceptable

1. **Inference only — no training runtime.** The bot does not
   train models in the request path. Pre-trained models are
   loaded once at startup (~2 seconds) and held in memory.
   Per-request latency is not affected beyond the inference
   kernel itself (~5-20ms per prediction).
2. **Cold starts are a deploy-time concern, not a
   request-time concern.** The container cold-starts at most
   once per deploy (rolling update replaces the pod). During
   normal operation the process stays hot for hours.
3. **RAM, not disk, is the actual constraint.** ChromaDB
   holds ~1027 embeddings (~50MB) plus model weights
   (~200MB). Steady-state RAM is well under 1 GB — inside
   normal Emergent pod limits.
4. **No SLO impact.** P95 API latency, bot tick cadence
   (15-min crypto, 1-min equity), and startup recovery time
   (<30s) all stay inside their targets with the current
   image size.

### Escalation trigger

**Revisit if any of the following happen:**

- Deploy duration exceeds 5 minutes (today: <2 min typical)
- Cold-start time exceeds 30 seconds (today: ~10 sec)
- Pod RAM approaches its limit (`kubectl top pods` showing
  >80% sustained utilization)
- A feature adds a new ML model family that doesn't share the
  existing dependency graph

### Optional future mitigation

If the size ever becomes a real constraint:

1. **Multi-stage Docker build** separating training-only deps
   from inference-only deps (currently mixed).
2. **Swap `chromadb` for `lancedb`-only** backend (we already
   have lancedb in the dep graph — consolidating would cut
   ~200 MB but requires a data migration).
3. **Move inference to a sidecar** so the main FastAPI pod
   stays lean and the ML pod scales independently.

None of these are justified today.

### Reference

- File: `backend/requirements.txt` (232 pinned packages)
- Models loaded: `backend/models/*.joblib`
- Observed RAM: steady-state ~700 MB per pod

---

## How to add a new entry

When a future readiness audit or code review flags a "warning"
that's actually acceptable, add an `OPS-NNN` entry using the
template below. **Don't** file it in CHANGELOG (that's for
shipped code) or in ROADMAP (that's for planned features) —
this file is specifically for **"known, accepted, tracked."**

```markdown
## OPS-NNN — <one-line title>

**Discovered**: <date + trigger>
**Owner**: <team or role>
**Status**: ACCEPTED (<scheduled|not scheduled|mitigated by ...>)
**Severity**: Low | Medium | High

### What happens
<plain description of the behavior>

### Why this is acceptable
<numbered list of reasons>

### Escalation trigger
<what would make us revisit>

### Reference
<file paths, commit hashes, or linked audits>
```
