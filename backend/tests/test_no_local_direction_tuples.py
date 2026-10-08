"""
Codebase-wide lint: ban local hardcoded direction tuples.

The 2026-05-01 direction-token bug was caused by this exact pattern,
scattered across 5+ files:

    direction in ("BUY", "LONG", "BULLISH")     # missing STRONG_BUY
    side in ("LONG", "BUY")                     # missing WEAK_BUY
    v in ("buy", "strong_buy", "long")          # missing weak_buy
    direction_upper in {"BUY", "BULLISH", "LONG", "UP"}   # missing STRONG_*

Each one silently mis-classified verdict tokens for ~4 weeks before
the operator caught it via downstream toxic-spike alerts. The fix
was to centralise into ``services.prediction_tracker.canonical_ai_dir``.

This test scans every Python file under ``backend/`` (excluding
``backend/tests/`` and the canonical helper itself) and FAILS if it
finds a comparison containing two-or-more known direction tokens.
That makes the regression class structurally impossible to reintroduce
without an explicit allowlist entry below.

Adding a new permitted occurrence
─────────────────────────────────
If you have a *legitimate* reason to keep a local direction tuple
(e.g. broker-side BUY/SELL guard that should NEVER receive AI verdict
tokens), add the file path + a one-line justification to ``ALLOWLIST``
below. The test will then permit that file. Reviewers are responsible
for ensuring the justification holds.

Why AST and not regex
─────────────────────
A regex-based scan would mis-flag string literals inside comments,
docstrings, and unrelated code. An AST walk only inspects real
``Compare`` and ``Set``/``Tuple``/``List`` constant nodes, so the
signal-to-noise ratio is high.
"""

from __future__ import annotations

import ast
import os
from pathlib import Path

import pytest


# ── Configuration ──────────────────────────────────────────────────

# Tokens that, when two or more appear together in a single literal
# tuple/set/list inside a comparison, signal a direction-classification
# pattern that should go through `canonical_ai_dir` instead.
DIRECTION_TOKENS = frozenset({
    "BUY", "SELL", "LONG", "SHORT", "BULLISH", "BEARISH",
    "UP", "DOWN", "STRONG_BUY", "STRONG_SELL", "WEAK_BUY", "WEAK_SELL",
    # Lowercase variants too — historic bug had `v in ("buy", "strong_buy", "long")`.
    "buy", "sell", "long", "short", "bullish", "bearish",
    "up", "down", "strong_buy", "strong_sell", "weak_buy", "weak_sell",
})

# Files allowed to contain local direction tuples. Each entry MUST be
# accompanied by a one-line justification — anyone who adds a new
# entry without one fails review.
ALLOWLIST: dict[str, str] = {
    "services/alpha_exit_router.py": (
        "Pure position-aware execution boundary: distinguishes SELL (may close long) "
        "from SHORT (open-short intent), includes STRONG/WEAK verdict variants, "
        "and validates broker LONG/BUY and SHORT/SELL aliases."
    ),
    "services/public_short_executor.py": (
        "Public REST orderSide validator accepts only BUY/SELL wire enums; "
        "AI verdict tokens must never be accepted as broker order sides."
    ),
    # Vendored RISEDUAL System Atlas package — its models.py accepts
    # only the post-canonicalised BUY/SELL execution direction set as
    # the broker-side fingerprint. Cannot import canonical_ai_dir
    # without breaking the package's zero-dependency contract.
    "risedual_atlas/models.py": (
        "Vendored zero-dependency Atlas package — the fingerprint "
        "layer only accepts post-canonicalised BUY/SELL to build a "
        "stable identity key. Portability requires no imports from "
        "services.*"
    ),
    "services/atlas_bridge.py": (
        "Atlas fingerprint adapter — maps every RISEDUAL direction "
        "variant to the canonical BUY/SELL side that the vendored "
        "Atlas ledger accepts. This is the single translation seam "
        "between our internal directions and Atlas's execution "
        "vocabulary; canonicalising elsewhere would just move the "
        "tuple to a different site."
    ),
    "services/council_consultation.py": (
        "Council vocabulary shim — normalises every direction variant "
        "(LONG/BUY/UP/BULLISH/STRONG_BUY/... etc.) coming from three "
        "different brain sources into a compact {up, down, flat} space "
        "used by the modulator. Same pattern as atlas_bridge — a "
        "translation seam at the boundary between external vocabularies "
        "and this service's internal 3-symbol space. Cannot be moved "
        "into canonical_direction because the mapping targets a "
        "council-specific vocabulary ({up, down, flat}) that's narrower "
        "than the canonical AI direction set."
    ),
    "services/operator_watchlist.py": (
        "Non-ticker stoplist — the tokens BUY/SELL/LONG/SHORT/UP/DOWN "
        "are listed here as words that LOOK like tickers when uppercase "
        "but never are. This is a lexical filter for the free-text "
        "parser, not a direction semantic. The lint would need "
        "structural awareness to distinguish; adding the file here is "
        "the correct scope."
    ),
    # Portable platform survival layer — deliberately zero-dependency
    # so it can be forked into sibling services / run on Railway /
    # Render / VPS without dragging the rest of the codebase along.
    # Uses raw BUY/SELL because those are the only directions a
    # broker accepts; importing canonical_ai_dir here would defeat
    # the portability contract.
    "shared/runtime/platform_survival.py": (
        "Portable broker-side gate — accepts only BUY/SELL as the "
        "post-canonicalised execution direction set. Cannot import "
        "services.prediction_tracker without breaking portability."
    ),
    "services/prediction_tracker.py": (
        "Source of truth — defines DIRECTION_BULLISH/BEARISH/NEUTRAL "
        "and canonical_ai_dir."
    ),
    # Engine-token map intentionally narrow; STRONG_*/WEAK_* are folded
    # in via the central helper as a fallback in normalize_action.
    "services/council_risk_modulator.py": (
        "Engine-specific token map (LONG/SHORT_OR_AVOID/etc.); falls "
        "back to canonical_ai_dir for AI verdict tokens."
    ),
    # Receives broker-side BUY/SELL only — never AI verdict tokens.
    "services/manual_order_guard.py": (
        "Broker-side side guard — receives Alpaca BUY/SELL, not AI "
        "verdict tokens. Has explicit is_buy/is_sell + non-directional "
        "fall-through."
    ),
    "services/smart_order_service.py": (
        "Broker-side side from filled order objects, not AI verdicts."
    ),
    # Form 4 / SEC insider transaction codes ('S', 'S-SALE', etc.) —
    # different namespace entirely.
    "services/war_room_service.py": (
        "Form 4 insider transaction codes (S/S-SALE), not direction tokens."
    ),
    # Validates an input is already in canonical {LONG, SHORT} form
    # AFTER upstream canonicalisation.
    "services/snapshot_enricher.py": (
        "Post-canonicalisation validator — input is already LONG/SHORT."
    ),
    "services/research_router.py": (
        "Post-canonicalisation validator — input is already LONG/SHORT."
    ),
    "services/web_research_service.py": (
        "Post-canonicalisation validator — input is already LONG/SHORT."
    ),
    "services/research_shadow.py": (
        "Source-of-truth canonical map for shadow engine; STRONG_*/WEAK_* "
        "explicitly enumerated. Also has post-canonicalisation validators."
    ),
    "services/crypto_signal_audit.py": (
        "Post-canonicalisation check for canonical {LONG, SHORT}."
    ),
    "services/ml_retrain_service.py": (
        "Post-canonicalisation pandas filter for canonical {LONG, SHORT}."
    ),
    "services/crypto_paper_trading_service.py": (
        "Broker-side BUY/SELL validation."
    ),
    "services/public_equity_live_executor.py": (
        "Broker-side LONG-only validation — direction is already "
        "canonicalised upstream in ml_orchestrator; the (BUY, LONG) "
        "tuple is the accepted equity entry token set, mirroring "
        "crypto_live_executor's broker-side gate."
    ),
    "services/paper_trading_service.py": (
        "Broker-side BUY/SELL validation."
    ),
    "ai_core/models.py": (
        "Validates already-canonical {LONG, SHORT}; verdict-token "
        "canonicalisation routes through canonical_ai_dir."
    ),
    "services/adversarial_enforcer.py": (
        "Validates already-canonical {BUY, SELL, HOLD} from the "
        "council_risk_modulator output."
    ),
    "services/confidence_weighting.py": (
        "Disagreement classifier reads already-canonical verdict "
        "tokens emitted by the hypothesis brains (BUY/SELL/HOLD/"
        "NEUTRAL). No conversion happens here — only counting which "
        "directional camps are present so the bounded penalty can be "
        "applied. Patent M-adjacent doctrine module."
    ),
    "shared/memory_modulator.py": (
        "Symmetric across all 4 brains — doctrine demands the same "
        "code path for Alpha/Camaro/Chevelle/REDEYE. Accepts the "
        "common AI verdict tokens (BUY/SHORT/SELL/UP/DOWN) and "
        "normalises to the canonical up/down stored in this repo's "
        "decision logs. Importing canonical_ai_dir from services."
        "prediction_tracker would couple shared/ to services/ and "
        "break the drop-in contract the operator requires."
    ),
    "services/multi_model_hypothesis_service.py": (
        "Consensus assembly reads already-canonical verdict tokens "
        "from each brain's structured JSON output. The DIRECTIONAL_FLOOR "
        "filter only checks membership ({BUY, SELL}) — it does not "
        "translate tokens. Verdict canonicalisation is upstream in "
        "_parse_json_response."
    ),
    "services/model_adaptation.py": (
        "Iteration set ['LONG','SHORT'] for parametric adaptation "
        "scan — not classification, output direction is already canonical."
    ),
    "services/risedual_ip_logic.py": (
        "Post-canonicalisation invariant check — signal.action MUST "
        "be one of canonical {BUY, SELL, HOLD} or the IP gate rejects."
    ),
    "services/risedual_learning_core.py": (
        "Post-canonicalisation trade-side check — input has already "
        "been routed through canonical_ai_dir at the top of "
        "evaluate_context, so the {LONG, SHORT} compare is purely "
        "validating the canonical output. Patent M layer."
    ),
    "services/learning_core_consumer.py": (
        "Post-canonicalisation gate — consumer reads "
        "learning_core.direction_canonical (already a canonical "
        "{LONG, SHORT, UNKNOWN} token from the orchestrator) and "
        "refuses to mutate the payload for non-trade sides. "
        "Patent M Phase 3."
    ),
    "services/shelly_ingest_adapter.py": (
        "Local pre-filter before the canonical engine — only "
        "{LONG, SHORT, BUY, SELL} eligible for Shelly's regime "
        "memory bank. The canonical engine still runs its own "
        "canonical_ai_dir check downstream. Patent M ingest."
    ),
    "services/shelly_shadow_logger.py": (
        "Post-canonicalisation gate — shadow logger reads "
        "learning_core.direction_canonical and only logs "
        "hypothetical mutations for {LONG, SHORT}. Patent M "
        "rollout step 3."
    ),
    "services/shelly_backfill_service.py": (
        "{BUY, SELL} are paper_trades fill-side tokens (not "
        "directions). Used to FIFO-pair entry/exit fills from "
        "the paper_trades collection during historical backfill. "
        "Direction canonicalisation happens downstream in "
        "shelly_ingest_adapter.paper_trade_to_memory."
    ),
    "services/position_reconciler.py": (
        "Broker-side side comparison ('buy'/'long' from order objects, "
        "not AI verdict tokens)."
    ),
    "services/crypto_paper_trader.py": (
        "Broker-side output-action checks ('BUY'/'SELL' for the risk "
        "budget/proof-chain action field). Verdict-token canonicalisation "
        "above already routes through canonical_ai_dir."
    ),
    # Engine-specific synonym folding to canonical LONG/SHORT/HOLD inside
    # a pure compare-and-return brake decision. The folding sets are the
    # local source of truth for the broker-side spelling variants this
    # surface accepts (LONG/BUY/STRONG_BUY); canonical_ai_dir would
    # require a network round-trip-style indirection inside what is
    # documented as a microsecond-level pure function.
    "services/commander_phase2_brake.py": (
        "Engine-specific strategist action-synonym folding to canonical "
        "LONG/SHORT/HOLD inside a pure brake decision (no I/O)."
    ),
    "services/smart_money_verification.py": (
        "Engine-specific action-synonym folding (mirrors "
        "commander_phase2_brake) for the smart-money proof block."
    ),
    # NL ticker-extraction stopword list. Includes BUY/SELL/HOLD as
    # English words that must NOT be treated as tickers — this is a
    # vocabulary filter, not a direction classifier.
    "services/natural_language_trading.py": (
        "Stopword set for ticker extraction (filters BUY/SELL/HOLD as "
        "English words from regex matches) — not a direction classifier."
    ),
    # Top-actions terminal aggregator: every direction comparison runs
    # AFTER canonical_ai_dir has folded the upstream verdict token into
    # canonical {LONG, SHORT}. The remaining literal sets are the
    # post-canonicalisation invariant checks. The sentiment-label
    # check on `news_shock.sentiment_label` is news-sentiment, not an
    # AI verdict, so the 2026-05-01 token-bug class doesn't apply.
    "services/terminal_aggregator.py": (
        "Post-canonicalisation invariant checks (canonical_ai_dir output "
        "compared to canonical {LONG, SHORT}) plus a news-sentiment "
        "label check that's not an AI verdict token."
    ),
    # Same post-canonicalisation pattern as ``terminal_aggregator.py``:
    # ``_TRADE_SIDES = {"LONG", "SHORT"}`` is the *output* side of
    # ``canonical_ai_dir``, used to filter HOLD/UNKNOWN out of the risk
    # context. Not an upstream verdict-token classifier.
    "services/regime_memory_retrieval.py": (
        "Post-canonicalisation invariant set ({LONG, SHORT}) used to "
        "filter canonical_ai_dir output. Not an AI-verdict classifier."
    ),
    # REFACTOR DEBT (pre-2026-05-05): three engine modules still hold
    # local direction tuples instead of routing through canonical_ai_dir.
    # Tracked here so the regression surface stays visible — ROADMAP.md
    # entry "centralise direction routing in scanner/slippage/autopsy".
    "services/day_trade_scanner.py": (
        "REFACTOR DEBT: bear-list comparison at calibration step still "
        "uses a local tuple. Behaviour is correct (post-canonicalisation), "
        "but should route through canonical_ai_dir for consistency."
    ),
    "services/slippage_simulator.py": (
        "REFACTOR DEBT: ``_LONG_ALIASES`` / ``_SHORT_ALIASES`` are the "
        "broker-execution side maps used to stamp entry/exit fill side. "
        "Functionally a superset of canonical_ai_dir's input; should be "
        "replaced by a thin ``canonical_ai_dir`` call in a follow-up."
    ),
    "services/post_trade_autopsy.py": (
        "REFACTOR DEBT: post-trade direction comparison vs Commander vote "
        "uses local tuples. Should route through canonical_ai_dir; behaviour "
        "is correct because all upstream callers already canonicalise."
    ),
    # Sovereign sidecar — Mission Control protocol uses its own vocabularies
    # (BUY/SELL/HOLD as action tokens to MC's contribution schema, and
    # long/short/abstain as MC's stance vocabulary). These are NOT AI-verdict
    # direction tokens — they are the canonical wire format for a different
    # runtime (Mission Control), validated against MC's verified schema.
    "sovereign/smoke_test.py": (
        "MC protocol vocabulary assertions — sovereign-side action/stance "
        "tokens are MC's wire vocabulary, not RISEDUAL AI verdict tokens."
    ),
}


# ── AST helpers ────────────────────────────────────────────────────


def _literal_strings(node: ast.AST) -> list[str]:
    """Return the string literals inside a Tuple/Set/List node, or []."""
    if not isinstance(node, (ast.Tuple, ast.Set, ast.List)):
        return []
    out: list[str] = []
    for elt in node.elts:
        if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
            out.append(elt.value)
    return out


def _direction_token_count(strings: list[str]) -> int:
    return sum(1 for s in strings if s in DIRECTION_TOKENS)


# ── Scan ───────────────────────────────────────────────────────────


def _backend_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _python_files_to_scan() -> list[Path]:
    root = _backend_root()
    skip_dirs = {"tests", "scripts", "data", "__pycache__", ".pytest_cache",
                 "risedual_core.egg-info"}
    files: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in skip_dirs]
        for fn in filenames:
            if fn.endswith(".py"):
                files.append(Path(dirpath) / fn)
    return files


def _scan_file(path: Path) -> list[tuple[int, str, list[str]]]:
    """Return [(lineno, snippet, tokens)] for every offending node."""
    try:
        src = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    try:
        tree = ast.parse(src, filename=str(path))
    except SyntaxError:
        return []

    src_lines = src.splitlines()
    offenses: list[tuple[int, str, list[str]]] = []

    for node in ast.walk(tree):
        # Only inspect comparisons and assignments where a literal
        # tuple/set/list of strings appears.
        candidates: list[ast.AST] = []
        if isinstance(node, ast.Compare):
            candidates.extend(node.comparators)
        elif isinstance(node, ast.Assign):
            candidates.append(node.value)
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            candidates.append(node.value)

        for c in candidates:
            strs = _literal_strings(c)
            count = _direction_token_count(strs)
            # Two-or-more direction tokens in the same literal collection
            # is the signature of a direction-classification pattern.
            if count >= 2:
                lineno = getattr(c, "lineno", getattr(node, "lineno", 0))
                snippet = src_lines[lineno - 1].strip() if 0 < lineno <= len(src_lines) else ""
                offenses.append((lineno, snippet, [s for s in strs if s in DIRECTION_TOKENS]))

    return offenses


# ── The actual test ────────────────────────────────────────────────


def test_no_local_direction_tuples_outside_allowlist():
    """Fail loudly if a new file introduces a hardcoded direction tuple.

    To fix a failure here, do ONE of:
      1. Replace the local tuple with `canonical_ai_dir(...)` from
         `services.prediction_tracker` (preferred).
      2. If the comparison genuinely receives broker-side BUY/SELL
         (NOT AI verdict tokens), add the file path + justification
         to ``ALLOWLIST`` above.

    Do NOT silence this test by widening ``DIRECTION_TOKENS`` — that
    defeats the entire point.
    """
    backend_root = _backend_root()
    failures: list[str] = []

    for path in _python_files_to_scan():
        rel = path.relative_to(backend_root).as_posix()
        if rel in ALLOWLIST:
            continue
        for lineno, snippet, tokens in _scan_file(path):
            failures.append(
                f"{rel}:{lineno}  tokens={tokens}\n    {snippet}"
            )

    if failures:
        msg = (
            "Local direction tuples detected. Route these through "
            "`services.prediction_tracker.canonical_ai_dir` or add "
            "an allowlist entry with justification.\n\n"
            + "\n\n".join(failures)
        )
        pytest.fail(msg)


# ── Self-tests for the lint test itself ────────────────────────────


def test_direction_token_count_finds_two_tokens_in_tuple():
    """Sanity check: the helper detects the exact pattern that caused
    the original bug."""
    src = 'x = direction in ("BUY", "LONG", "BULLISH")'
    tree = ast.parse(src)
    cmp_node = tree.body[0].value  # type: ignore[attr-defined]
    assert isinstance(cmp_node, ast.Compare)
    strs = _literal_strings(cmp_node.comparators[0])
    assert _direction_token_count(strs) >= 2


def test_allowlist_entries_all_exist():
    """Every allowlist entry must point at an actual file. Stale
    allowlist rows would silently weaken the lint."""
    backend_root = _backend_root()
    missing = [p for p in ALLOWLIST if not (backend_root / p).is_file()]
    assert not missing, f"Stale ALLOWLIST entries: {missing}"


def test_allowlist_justifications_are_non_empty():
    """Anyone adding a new allowlist entry without a justification
    fails review here."""
    bad = [p for p, j in ALLOWLIST.items() if not j or len(j.strip()) < 20]
    assert not bad, (
        f"ALLOWLIST entries without a real justification: {bad}. "
        f"Each entry needs a one-line explanation of why the local "
        f"tuple is safe."
    )
