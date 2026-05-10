import React from 'react';
import {
  Brain, RefreshCw, AlertTriangle, Search, Database, Sparkles,
  Download, BookMarked, FileText,
} from 'lucide-react';
import { authFetch } from '../../contexts/AuthContext';
import { getApiBase } from '../../utils/apiBase';

const API = `${getApiBase()}/api`;

/**
 * AlphaKnowledgePanel — operator surface for the read-only Python KB.
 *
 *   GET  /api/admin/alpha-knowledge/status
 *   GET  /api/admin/alpha-knowledge/manifest
 *   POST /api/admin/alpha-knowledge/ingest
 *   GET  /api/admin/alpha-knowledge/retrieve?q=...
 *
 * Doctrine: this panel ONLY observes + triggers ingest. It does
 * not mutate retrieved content. The KB cannot reach the Code
 * Evolution gate by construction (firewall enforced server-side).
 */

const CAT_COLOR = {
  language_reference: 'bg-cyan-500/10 text-cyan-400 border-cyan-500/30',
  library_reference:  'bg-emerald-500/10 text-emerald-400 border-emerald-500/30',
  tutorial:           'bg-amber-500/10 text-amber-400 border-amber-500/30',
  howto:              'bg-fuchsia-500/10 text-fuchsia-400 border-fuchsia-500/30',
  operator_curated:   'bg-slate-500/10 text-slate-300 border-slate-500/30',
};

function CatPill({ category }) {
  return (
    <span
      data-testid={`alpha-kb-cat-${category}`}
      className={`inline-block px-2 py-0.5 rounded-full text-[10px] uppercase tracking-wider font-mono border ${CAT_COLOR[category] || 'bg-slate-800 text-slate-400 border-slate-700/40'}`}
    >
      {category}
    </span>
  );
}

function StatTile({ label, value, accent = 'slate' }) {
  const palette = {
    slate:   'text-slate-300',
    cyan:    'text-cyan-400',
    emerald: 'text-emerald-400',
  };
  return (
    <div className="bg-slate-900/60 border border-slate-800 rounded-lg p-3">
      <div className="text-[10px] uppercase tracking-widest text-slate-500">
        {label}
      </div>
      <div className={`text-xl font-semibold ${palette[accent] || palette.slate}`}>
        {value}
      </div>
    </div>
  );
}

export default function AlphaKnowledgePanel() {
  const [status, setStatus] = React.useState(null);
  const [manifest, setManifest] = React.useState(null);
  const [error, setError] = React.useState(null);
  const [busy, setBusy] = React.useState(null);
  const [query, setQuery] = React.useState('asyncio task gather');
  const [results, setResults] = React.useState(null);
  const [ingestSummary, setIngestSummary] = React.useState(null);

  const fetchStatus = React.useCallback(async () => {
    try {
      const [sR, mR] = await Promise.all([
        authFetch(`${API}/admin/alpha-knowledge/status`),
        authFetch(`${API}/admin/alpha-knowledge/manifest`),
      ]);
      const sJ = await sR.json();
      const mJ = await mR.json();
      if (!sR.ok) throw new Error(sJ.detail || `status (${sR.status})`);
      if (!mR.ok) throw new Error(mJ.detail || `manifest (${mR.status})`);
      setStatus(sJ);
      setManifest(mJ);
    } catch (e) {
      setError(e.message);
    }
  }, []);

  React.useEffect(() => { fetchStatus(); }, [fetchStatus]);

  const onIngest = async () => {
    setBusy('ingest');
    setError(null);
    setIngestSummary(null);
    try {
      const r = await authFetch(`${API}/admin/alpha-knowledge/ingest`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({}),
      });
      const json = await r.json();
      if (!r.ok) throw new Error(json.detail || `ingest (${r.status})`);
      setIngestSummary(json);
      await fetchStatus();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(null);
    }
  };

  const onSearch = async () => {
    if (!query.trim()) return;
    setBusy('search');
    setError(null);
    setResults(null);
    try {
      const url = `${API}/admin/alpha-knowledge/retrieve?q=${encodeURIComponent(query)}&limit=8`;
      const r = await authFetch(url);
      const json = await r.json();
      if (!r.ok) throw new Error(json.detail || `retrieve (${r.status})`);
      setResults(json);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(null);
    }
  };

  return (
    <div data-testid="alpha-knowledge-panel" className="space-y-5">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h2 className="text-2xl font-semibold text-slate-100 mb-1 flex items-center gap-2">
            <Brain className="text-cyan-400" size={22} />
            Alpha Python Knowledge Base
          </h2>
          <p className="text-sm text-slate-400 max-w-2xl">
            Read-only Python corpus Alpha consults via the{' '}
            <code className="text-cyan-400 font-mono text-xs">/py</code>{' '}
            prefix in chat. Sourced from docs.python.org. The KB is
            firewalled from execution &amp; the Code Evolution gate —
            it can be read, never written by the runtime.
          </p>
        </div>
        <button
          type="button"
          data-testid="alpha-kb-refresh-btn"
          onClick={fetchStatus}
          className="px-4 py-2 rounded-md text-sm font-semibold bg-cyan-500 hover:bg-cyan-400 text-slate-950 flex items-center gap-2"
        >
          <RefreshCw size={14} />
          Refresh
        </button>
      </div>

      {error && (
        <div
          className="bg-rose-500/10 border border-rose-500/30 rounded-lg px-4 py-3 text-sm text-rose-300 flex items-start gap-2"
          data-testid="alpha-kb-error"
        >
          <AlertTriangle size={16} className="flex-shrink-0 mt-0.5" />
          <div>{error}</div>
        </div>
      )}

      {/* Stats row */}
      {status && (
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3" data-testid="alpha-kb-stats">
          <StatTile label="Total chunks" value={status.total_chunks} accent="cyan" />
          <StatTile label="Sources" value={status.total_sources} accent="emerald" />
          <StatTile
            label="Manifest URLs"
            value={manifest?.total_urls ?? '—'}
          />
          <StatTile
            label="Schema"
            value={`v${status.schema_version}`}
          />
        </div>
      )}

      {/* Per-category breakdown */}
      {status?.by_category && Object.keys(status.by_category).length > 0 && (
        <div className="bg-slate-900/40 border border-slate-800 rounded-lg p-4">
          <div className="flex items-center gap-2 text-xs uppercase tracking-widest text-slate-500 mb-3">
            <BookMarked size={14} /> Coverage by category
          </div>
          <div className="flex flex-wrap gap-2">
            {Object.entries(status.by_category).map(([cat, n]) => (
              <div
                key={cat}
                className="flex items-center gap-1.5"
                data-testid={`alpha-kb-cat-stat-${cat}`}
              >
                <CatPill category={cat} />
                <span className="text-xs font-mono text-slate-300">{n}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Ingest panel */}
      <div className="bg-slate-900/40 border border-slate-800 rounded-lg p-4">
        <div className="flex items-center justify-between gap-3 mb-3">
          <div className="flex items-center gap-2 text-xs uppercase tracking-widest text-slate-500">
            <Download size={14} /> Ingest from manifest
          </div>
          <button
            type="button"
            data-testid="alpha-kb-ingest-btn"
            disabled={busy === 'ingest'}
            onClick={onIngest}
            className={`px-3 py-1.5 rounded text-xs font-semibold flex items-center gap-1.5 transition-colors ${
              busy === 'ingest'
                ? 'bg-slate-800 text-slate-500 cursor-not-allowed'
                : 'bg-emerald-500/10 hover:bg-emerald-500/20 text-emerald-400 border border-emerald-500/30'
            }`}
          >
            <Database size={12} className={busy === 'ingest' ? 'animate-pulse' : ''} />
            {busy === 'ingest' ? 'Fetching…' : 'Run full ingest'}
          </button>
        </div>
        <p className="text-xs text-slate-500">
          Pulls all {manifest?.total_urls ?? '~80'} URLs from{' '}
          <code className="text-slate-400 font-mono">docs.python.org</code>,
          chunks the HTML, and upserts to Mongo. Idempotent — safe to
          re-run. Typical full run completes in ~30–60s.
        </p>

        {ingestSummary && (
          <div
            className="mt-3 bg-slate-950 border border-slate-800 rounded p-3 text-xs font-mono"
            data-testid="alpha-kb-ingest-summary"
          >
            <div className="text-emerald-400">
              ✓ {ingestSummary.sources_fetched}/{ingestSummary.sources_attempted} sources fetched ·{' '}
              {ingestSummary.chunks_written} new chunks ·{' '}
              corpus now {ingestSummary.chunks_total_after}
            </div>
            {ingestSummary.failures?.length > 0 && (
              <div className="mt-2 text-rose-400">
                {ingestSummary.failures.length} failure(s):
                <ul className="list-disc pl-5 mt-1 space-y-0.5">
                  {ingestSummary.failures.slice(0, 5).map((f, i) => (
                    <li key={i}>{f.url} — {f.error}</li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        )}
      </div>

      {/* Search panel */}
      <div className="bg-slate-900/40 border border-slate-800 rounded-lg p-4 space-y-3">
        <div className="flex items-center gap-2 text-xs uppercase tracking-widest text-slate-500">
          <Search size={14} /> Test retrieval
        </div>
        <div className="flex gap-2">
          <input
            type="text"
            data-testid="alpha-kb-search-input"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') onSearch(); }}
            placeholder="e.g. asyncio task gather"
            className="flex-1 bg-slate-950 border border-slate-700 rounded-md px-3 py-2 text-sm text-slate-200 font-mono focus:outline-none focus:border-cyan-500"
          />
          <button
            type="button"
            data-testid="alpha-kb-search-btn"
            disabled={busy === 'search' || !query.trim()}
            onClick={onSearch}
            className={`px-4 py-2 rounded-md text-sm font-semibold flex items-center gap-2 transition-colors ${
              busy === 'search' || !query.trim()
                ? 'bg-slate-800 text-slate-500 cursor-not-allowed'
                : 'bg-cyan-500 hover:bg-cyan-400 text-slate-950'
            }`}
          >
            <Search size={14} className={busy === 'search' ? 'animate-pulse' : ''} />
            Search
          </button>
        </div>

        {results && (
          <div data-testid="alpha-kb-results">
            <div className="text-[11px] text-slate-500 mb-2 font-mono">
              {results.results.length} result{results.results.length === 1 ? '' : 's'} ·
              {' '}corpus size {results.total_corpus_size}
            </div>
            {results.results.length === 0 && (
              <div className="text-sm text-slate-500 italic">
                No matches. Try ingesting first if the corpus is empty.
              </div>
            )}
            <ul className="space-y-2">
              {results.results.map((r) => (
                <li
                  key={r.chunk_id}
                  className="border border-slate-800 rounded p-3 hover:bg-slate-900/60 transition-colors"
                  data-testid={`alpha-kb-result-${r.chunk_id}`}
                >
                  <div className="flex items-start justify-between gap-2 mb-1.5">
                    <div className="flex items-center gap-2">
                      <CatPill category={r.source_category} />
                      <a
                        href={r.source_url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="text-sm text-cyan-400 hover:text-cyan-300 font-mono"
                      >
                        {r.source_title}
                      </a>
                    </div>
                    <span className="text-[11px] text-slate-500 font-mono">
                      score {r.score.toFixed(2)} · #{r.chunk_index}
                    </span>
                  </div>
                  <div className="text-xs text-slate-300 line-clamp-3">{r.text}</div>
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>

      <div className="text-xs text-slate-600 pt-2 border-t border-slate-800/40 flex items-center gap-2">
        <Sparkles size={12} className="text-slate-700" />
        <span>
          Operator chat prefix:{' '}
          <code className="text-cyan-400 font-mono">/py &lt;question&gt;</code>{' '}
          consults this KB before the LLM answers.
        </span>
      </div>
    </div>
  );
}
