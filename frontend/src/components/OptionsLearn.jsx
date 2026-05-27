import React from 'react';
import { Loader2, BookOpen } from 'lucide-react';
import { Input } from './ui/input';

/**
 * OptionsLearn — full glossary page for the Options → Learn tab.
 *
 * Single fetch of `GET /api/learn/options`, then renders a searchable
 * card list grouped by category. The tooltip widget reuses the same
 * cached payload so this page double-warms the cache.
 */
export default function OptionsLearn() {
  const [glossary, setGlossary] = React.useState(null);
  const [categories, setCategories] = React.useState([]);
  const [query, setQuery] = React.useState('');
  const [error, setError] = React.useState(null);

  React.useEffect(() => {
    const base = process.env.REACT_APP_BACKEND_URL || '';
    fetch(`${base}/api/learn/options`)
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then((body) => {
        setGlossary(body.terms || {});
        setCategories(body.categories || []);
        if (typeof window !== 'undefined') {
          window.__optionsGlossary = body.terms || {};
        }
      })
      .catch((e) => setError(String(e?.message || e)));
  }, []);

  const q = query.trim().toLowerCase();

  const grouped = React.useMemo(() => {
    if (!glossary) return {};
    const out = {};
    for (const cat of categories) out[cat] = [];
    for (const [key, doc] of Object.entries(glossary)) {
      if (q) {
        const blob = `${doc.term} ${doc.short} ${doc.long}`.toLowerCase();
        if (!blob.includes(q)) continue;
      }
      (out[doc.category] || (out[doc.category] = [])).push({ key, ...doc });
    }
    return out;
  }, [glossary, categories, q]);

  const totalShown = Object.values(grouped).reduce(
    (acc, arr) => acc + arr.length, 0,
  );

  if (error) {
    return (
      <div className="p-6 text-rose-300" data-testid="options-learn-error">
        Could not load options glossary: {error}
      </div>
    );
  }

  if (!glossary) {
    return (
      <div className="p-6 flex items-center gap-3 text-slate-400" data-testid="options-learn-loading">
        <Loader2 className="w-4 h-4 animate-spin" /> Loading glossary…
      </div>
    );
  }

  return (
    <div className="space-y-6" data-testid="options-learn">
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div>
          <h2 className="text-3xl font-semibold text-violet-200 flex items-center gap-2">
            <BookOpen className="w-6 h-6" /> Options Learn
          </h2>
          <p className="text-sm text-slate-400 mt-1 max-w-xl">
            Plain-language definitions for the options vocabulary used
            across this terminal. Hover any underlined term elsewhere
            in the Options hub for the same short blurb inline.
          </p>
        </div>
        <div className="w-full sm:w-80">
          <Input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search terms — e.g. delta, iron condor…"
            className="bg-slate-900 border-slate-700 text-slate-200"
            data-testid="options-learn-search"
          />
          <p className="text-xs text-slate-500 mt-1" data-testid="options-learn-count">
            {totalShown} term{totalShown === 1 ? '' : 's'} shown
          </p>
        </div>
      </div>

      {categories.map((cat) => {
        const items = grouped[cat] || [];
        if (!items.length) return null;
        return (
          <section key={cat} data-testid={`options-learn-cat-${cat.toLowerCase()}`}>
            <h3 className="text-xs uppercase tracking-widest text-violet-300/70 mb-3">
              {cat}
            </h3>
            <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-4">
              {items.map((doc) => (
                <article
                  key={doc.key}
                  className="rounded-lg border border-slate-800 bg-slate-900/60 p-4 hover:border-violet-500/40 transition-colors"
                  data-testid={`options-learn-term-${doc.key}`}
                >
                  <h4 className="text-base font-semibold text-violet-200 mb-1">
                    {doc.term}
                  </h4>
                  <p className="text-xs text-slate-300 italic mb-2">
                    {doc.short}
                  </p>
                  <p className="text-sm text-slate-400 leading-relaxed">
                    {doc.long}
                  </p>
                </article>
              ))}
            </div>
          </section>
        );
      })}

      {totalShown === 0 && (
        <p className="text-slate-500 text-sm" data-testid="options-learn-empty">
          No terms match "{query}".
        </p>
      )}
    </div>
  );
}
