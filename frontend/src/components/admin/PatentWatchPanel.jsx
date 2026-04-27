import React, { useCallback, useEffect, useState } from 'react';
import { FileText, RefreshCw, Plus, Trash2, ExternalLink, AlertTriangle } from 'lucide-react';
import { Button } from '../ui/button';
import { Input } from '../ui/input';
import { toast } from '../ui/sonner';
import { authFetch } from '../../contexts/AuthContext';
import { getApiBase } from '../../utils/apiBase';
import logger from '../../utils/logger';

const API = `${getApiBase()}/api`;

const formatDateTime = (iso) => {
  if (!iso) return '—';
  try { return new Date(iso).toLocaleString(); } catch { return iso; }
};

const PatentWatchPanel = () => {
  const [queries, setQueries] = useState([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(null);
  const [results, setResults] = useState([]);
  const [resultsLoading, setResultsLoading] = useState(false);
  const [activeQueryId, setActiveQueryId] = useState(null);
  const [config, setConfig] = useState({ api_key_configured: true });

  const [form, setForm] = useState({
    label: '',
    assignee: '',
    inventor_last: '',
    keyword: '',
  });
  const [creating, setCreating] = useState(false);

  const fetchConfig = useCallback(async () => {
    try {
      const res = await authFetch(`${API}/admin/patents/config`);
      if (res.ok) setConfig(await res.json());
    } catch (e) { logger.error('Patent config fetch error', e); }
  }, []);

  const fetchQueries = useCallback(async () => {
    setLoading(true);
    try {
      const res = await authFetch(`${API}/admin/patents/queries`);
      if (res.ok) {
        const data = await res.json();
        setQueries(data.queries || []);
      } else {
        toast.error('Failed to load patent queries');
      }
    } catch (e) {
      logger.error('Patent queries fetch error', e);
    } finally {
      setLoading(false);
    }
  }, []);

  const fetchResults = useCallback(async (queryId) => {
    setResultsLoading(true);
    try {
      const url = queryId
        ? `${API}/admin/patents/results?query_id=${encodeURIComponent(queryId)}&limit=50`
        : `${API}/admin/patents/results?limit=50`;
      const res = await authFetch(url);
      if (res.ok) {
        const data = await res.json();
        setResults(data.results || []);
      }
    } catch (e) {
      logger.error('Patent results fetch error', e);
    } finally {
      setResultsLoading(false);
    }
  }, []);

  useEffect(() => { fetchQueries(); }, [fetchQueries]);
  useEffect(() => { fetchConfig(); }, [fetchConfig]);
  useEffect(() => { fetchResults(activeQueryId); }, [activeQueryId, fetchResults]);

  const handleCreate = async (e) => {
    e?.preventDefault?.();
    if (!form.label.trim()) {
      toast.error('Label is required');
      return;
    }
    if (!form.assignee && !form.inventor_last && !form.keyword) {
      toast.error('Provide at least one of: assignee, inventor last name, or keyword');
      return;
    }
    setCreating(true);
    try {
      const res = await authFetch(`${API}/admin/patents/queries`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(form),
      });
      if (res.ok) {
        toast.success('Query saved');
        setForm({ label: '', assignee: '', inventor_last: '', keyword: '' });
        fetchQueries();
      } else {
        const d = await res.json();
        toast.error(d.detail || 'Save failed');
      }
    } catch (e) {
      toast.error('Save failed');
    } finally {
      setCreating(false);
    }
  };

  const handleDelete = async (id) => {
    if (!window.confirm('Delete this watch query and its cached patents?')) return;
    try {
      const res = await authFetch(`${API}/admin/patents/queries/${id}`, { method: 'DELETE' });
      if (res.ok) {
        toast.success('Query deleted');
        if (activeQueryId === id) setActiveQueryId(null);
        fetchQueries();
        fetchResults(activeQueryId === id ? null : activeQueryId);
      } else {
        toast.error('Delete failed');
      }
    } catch (e) {
      toast.error('Delete failed');
    }
  };

  const handleRefresh = async (id) => {
    setRefreshing(id);
    try {
      const res = await authFetch(`${API}/admin/patents/refresh/${id}`, { method: 'POST' });
      if (res.ok) {
        const data = await res.json();
        if (data.error) {
          toast.error(`USPTO fetch error: ${data.error}`);
        } else {
          toast.success(`Fetched ${data.fetched} patents`);
        }
        fetchQueries();
        if (activeQueryId === id || !activeQueryId) fetchResults(activeQueryId);
      } else {
        toast.error('Refresh failed');
      }
    } catch (e) {
      toast.error('Refresh failed');
    } finally {
      setRefreshing(null);
    }
  };

  return (
    <div className="p-4 sm:p-6 space-y-6" data-testid="patent-watch-panel">
      {/* Setup banner — shown when USPTO_API_KEY isn't configured */}
      {!config.api_key_configured && (
        <div className="bg-amber-500/10 border border-amber-500/40 rounded-lg p-3 flex items-start gap-3" data-testid="patent-watch-setup-banner">
          <AlertTriangle className="w-4 h-4 text-amber-400 mt-0.5 shrink-0" />
          <div className="text-xs text-amber-100 space-y-1">
            <div className="font-semibold">USPTO API key not configured</div>
            <div>
              Patent fetches will fail until <code className="bg-slate-900/60 px-1 rounded">USPTO_API_KEY</code> is set in <code className="bg-slate-900/60 px-1 rounded">backend/.env</code> and the backend is restarted.
            </div>
            <div>
              Get a key:{' '}
              <a
                href={config.setup_url || 'https://data.uspto.gov/apis/getting-started'}
                target="_blank"
                rel="noopener noreferrer"
                className="underline hover:text-white"
                data-testid="patent-watch-setup-link"
              >
                data.uspto.gov/apis/getting-started
              </a>
              {' '}(MyUSPTO account + ID.me linkage required).
            </div>
          </div>
        </div>
      )}

      {/* Add-query form */}
      <form onSubmit={handleCreate} className="bg-slate-800/50 rounded-xl p-4 border border-slate-700/50 space-y-3" data-testid="patent-watch-form">
        <h3 className="text-white text-sm font-semibold flex items-center gap-2">
          <Plus className="w-4 h-4" />
          Add a watch query
        </h3>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <Input
            placeholder="Label (e.g. 'OpenAI filings')"
            value={form.label}
            onChange={(e) => setForm({ ...form, label: e.target.value })}
            className="bg-slate-900/60 border-slate-600 text-white text-sm"
            data-testid="patent-watch-label-input"
          />
          <Input
            placeholder="Assignee org (optional)"
            value={form.assignee}
            onChange={(e) => setForm({ ...form, assignee: e.target.value })}
            className="bg-slate-900/60 border-slate-600 text-white text-sm"
            data-testid="patent-watch-assignee-input"
          />
          <Input
            placeholder="Inventor last name (optional)"
            value={form.inventor_last}
            onChange={(e) => setForm({ ...form, inventor_last: e.target.value })}
            className="bg-slate-900/60 border-slate-600 text-white text-sm"
            data-testid="patent-watch-inventor-input"
          />
          <Input
            placeholder="Title keyword (optional)"
            value={form.keyword}
            onChange={(e) => setForm({ ...form, keyword: e.target.value })}
            className="bg-slate-900/60 border-slate-600 text-white text-sm"
            data-testid="patent-watch-keyword-input"
          />
        </div>
        <Button
          type="submit"
          disabled={creating}
          className="bg-[#3DE8D9] hover:bg-[#2dcdc0] text-slate-900 text-sm"
          data-testid="patent-watch-create-btn"
        >
          {creating ? 'Saving…' : 'Save query'}
        </Button>
      </form>

      {/* Saved queries */}
      <div className="space-y-2">
        <div className="flex items-center justify-between">
          <h3 className="text-white text-sm font-semibold flex items-center gap-2">
            <FileText className="w-4 h-4" />
            Saved queries ({queries.length})
          </h3>
          {activeQueryId && (
            <button
              onClick={() => setActiveQueryId(null)}
              className="text-xs text-slate-400 hover:text-white"
              data-testid="patent-watch-clear-filter-btn"
            >
              Show all results
            </button>
          )}
        </div>
        {loading ? (
          <div className="text-slate-400 text-sm">Loading…</div>
        ) : queries.length === 0 ? (
          <div className="text-slate-400 text-sm bg-slate-800/30 p-4 rounded-lg">
            No queries yet. Add one above to start tracking USPTO filings.
          </div>
        ) : (
          <div className="space-y-2">
            {queries.map((q) => (
              <div
                key={q.id}
                className={`bg-slate-800/50 border rounded-lg p-3 flex items-center justify-between gap-3 ${
                  activeQueryId === q.id ? 'border-[#3DE8D9]' : 'border-slate-700/50'
                }`}
                data-testid={`patent-watch-query-row-${q.id}`}
              >
                <button
                  onClick={() => setActiveQueryId(q.id)}
                  className="flex-1 text-left min-w-0"
                  data-testid={`patent-watch-query-select-${q.id}`}
                >
                  <div className="text-white text-sm font-medium truncate">{q.label}</div>
                  <div className="text-slate-400 text-xs truncate">
                    {[
                      q.assignee && `assignee: ${q.assignee}`,
                      q.inventor_last && `inventor: ${q.inventor_last}`,
                      q.keyword && `keyword: ${q.keyword}`,
                    ].filter(Boolean).join(' · ')}
                  </div>
                  <div className="text-slate-500 text-[11px] mt-0.5 flex items-center gap-2">
                    <span>Last fetch: {formatDateTime(q.last_fetch_at)}</span>
                    <span>·</span>
                    <span>{q.last_fetch_count || 0} cached</span>
                    {q.last_error && (
                      <span className="text-amber-400 inline-flex items-center gap-1">
                        <AlertTriangle className="w-3 h-3" />
                        {q.last_error}
                      </span>
                    )}
                  </div>
                </button>
                <div className="flex items-center gap-1 shrink-0">
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => handleRefresh(q.id)}
                    disabled={refreshing === q.id}
                    className="bg-slate-900 border-slate-600 text-white"
                    data-testid={`patent-watch-refresh-${q.id}`}
                    title="Fetch now"
                  >
                    <RefreshCw className={`w-3.5 h-3.5 ${refreshing === q.id ? 'animate-spin' : ''}`} />
                  </Button>
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => handleDelete(q.id)}
                    className="bg-slate-900 border-red-500/40 text-red-400 hover:bg-red-500/10"
                    data-testid={`patent-watch-delete-${q.id}`}
                    title="Delete"
                  >
                    <Trash2 className="w-3.5 h-3.5" />
                  </Button>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Results */}
      <div className="space-y-2">
        <h3 className="text-white text-sm font-semibold">
          Patents {activeQueryId ? '(filtered)' : '(all queries)'} — {results.length}
        </h3>
        {resultsLoading ? (
          <div className="text-slate-400 text-sm">Loading patents…</div>
        ) : results.length === 0 ? (
          <div className="text-slate-400 text-sm bg-slate-800/30 p-4 rounded-lg">
            No cached patents yet. Click the refresh button on a query to fetch from USPTO.
          </div>
        ) : (
          <div className="space-y-2">
            {results.map((p) => (
              <div
                key={`${p.query_id}-${p.patent_number}`}
                className="bg-slate-800/40 border border-slate-700/50 rounded-lg p-3"
                data-testid={`patent-watch-result-${p.patent_number}`}
              >
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0 flex-1">
                    <div className="text-white text-sm font-medium">
                      {p.title || '(untitled)'}
                    </div>
                    <div className="text-slate-400 text-xs mt-1 flex flex-wrap gap-x-3 gap-y-0.5">
                      <span>#{p.patent_number}</span>
                      {p.patent_date && <span>· {p.patent_date}</span>}
                      {p.assignee && <span>· {p.assignee}</span>}
                      {p.inventor && <span>· {p.inventor}</span>}
                    </div>
                    {p.abstract && (
                      <div className="text-slate-300 text-xs mt-2 line-clamp-3">
                        {p.abstract}
                      </div>
                    )}
                  </div>
                  {p.url && (
                    <a
                      href={p.url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-[#3DE8D9] hover:text-white shrink-0"
                      data-testid={`patent-watch-link-${p.patent_number}`}
                      title="Open on Google Patents"
                    >
                      <ExternalLink className="w-4 h-4" />
                    </a>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
};

export default PatentWatchPanel;
