"""Search War Room — Synthesizer builds a unified brief from engine results."""
from services.search_war_room.schemas import SearchBrief


def build_brief(query: str, results) -> SearchBrief:
    ok = [r for r in results if r.status in {'ok', 'cached'}]
    degraded = any(r.status in {'error', 'timeout'} for r in results)
    sources = [r.engine for r in ok]

    summaries = [r.summary for r in ok if r.summary]
    top = ok[0].title if ok and ok[0].title else f'Search brief: {query}'

    signals = []
    risks = []
    for r in ok:
        if r.authoritative:
            signals.append(f'{r.engine}: authoritative data returned')
        if r.engine == 'sec' and r.items:
            signals.append(f'SEC: {len(r.items)} recent filings found')
        if r.engine == 'fred' and r.items:
            latest = r.items[0] if r.items else {}
            signals.append(f'FRED: latest value {latest.get("value", "N/A")} ({latest.get("date", "")})')
        if r.engine == 'yahoo' and r.items:
            p = r.items[0] if r.items else {}
            signals.append(f'Price: ${p.get("price", "?")} ({p.get("change_pct", 0):+.2f}%)')
        if r.engine in ('ddg', 'ddg_news') and r.items:
            signals.append(f'{r.engine}: {len(r.items)} results')

    if degraded:
        risks.append('Some engines unavailable — partial brief')

    return SearchBrief(
        headline=top,
        summary=' | '.join(summaries[:4]) if summaries else f'Partial research brief for {query}.',
        signals=signals[:8],
        risks=risks,
        sources_used=sources,
    )
