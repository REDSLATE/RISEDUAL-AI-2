import React from 'react';
import {
  GraduationCap, RefreshCw, AlertTriangle, CheckCircle2, Code2,
  FileText, Sparkles, ListChecks, AlertOctagon, BookOpen,
} from 'lucide-react';
import { authFetch } from '../../contexts/AuthContext';
import { getApiBase } from '../../utils/apiBase';

const API = `${getApiBase()}/api`;

/**
 * PythonCoach — Alpha's operator-facing Python learning surface.
 *
 *   - "Generate lesson plan" turns a goal into a structured plan
 *     (steps / concepts / drills / pitfalls).
 *   - "Review pasted code" runs an AST-based static review and,
 *     optionally, asks the LLM for richer feedback.
 *   - "Load example" pulls a starter snippet from the backend.
 *
 * The coach NEVER executes user code. All feedback is static + LLM.
 */

const SEV_PILL = {
  info:  'bg-cyan-500/10 text-cyan-400 border-cyan-500/30',
  warn:  'bg-amber-500/10 text-amber-400 border-amber-500/30',
  error: 'bg-rose-500/10 text-rose-400 border-rose-500/30',
};

function Pill({ children, kind = 'slate', testid }) {
  const palette = {
    slate:   'bg-slate-800 text-slate-300 border-slate-700/40',
    cyan:    'bg-cyan-500/10 text-cyan-400 border-cyan-500/30',
    emerald: 'bg-emerald-500/10 text-emerald-400 border-emerald-500/30',
    rose:    'bg-rose-500/10 text-rose-400 border-rose-500/30',
  };
  return (
    <span
      data-testid={testid}
      className={`inline-block px-2 py-0.5 rounded-full text-[10px] uppercase tracking-wider font-mono border ${palette[kind]}`}
    >
      {children}
    </span>
  );
}

function FindingsList({ findings }) {
  if (!findings || findings.length === 0) {
    return (
      <div className="text-xs text-slate-500 italic py-3" data-testid="coach-findings-empty">
        No findings — code looks clean to the static reviewer.
      </div>
    );
  }
  return (
    <ul className="space-y-2" data-testid="coach-findings-list">
      {findings.map((f, idx) => (
        <li
          key={idx}
          className={`border rounded-lg px-3 py-2 text-sm flex items-start gap-2 ${SEV_PILL[f.severity] || SEV_PILL.info}`}
          data-testid={`coach-finding-${f.rule_id}`}
        >
          <AlertOctagon size={14} className="flex-shrink-0 mt-0.5" />
          <div className="flex-1">
            <div className="text-[11px] uppercase tracking-widest font-mono opacity-80">
              {f.rule_id}{f.line ? ` · line ${f.line}` : ''}
            </div>
            <div>{f.message}</div>
          </div>
        </li>
      ))}
    </ul>
  );
}

function LessonPlanView({ plan }) {
  if (!plan) return null;
  return (
    <div className="space-y-4" data-testid="coach-plan-view">
      <div className="bg-slate-900/40 border border-slate-800 rounded-lg p-4">
        <div className="flex items-start justify-between gap-3 mb-2">
          <div className="text-sm text-slate-300">{plan.summary}</div>
          <Pill kind="cyan" testid="coach-plan-est">
            ~{plan.estimated_minutes}m
          </Pill>
        </div>
        <div className="text-[10px] uppercase tracking-widest text-slate-500 font-mono">
          {plan.generated_by}
        </div>
      </div>

      {plan.concepts?.length > 0 && (
        <div className="bg-slate-900/40 border border-slate-800 rounded-lg p-4">
          <div className="flex items-center gap-2 text-xs uppercase tracking-widest text-slate-500 mb-2">
            <BookOpen size={14} /> Concepts
          </div>
          <div className="flex flex-wrap gap-1.5">
            {plan.concepts.map((c, i) => (
              <Pill key={i} kind="slate">{c}</Pill>
            ))}
          </div>
        </div>
      )}

      {plan.steps?.length > 0 && (
        <div className="bg-slate-900/40 border border-slate-800 rounded-lg p-4">
          <div className="flex items-center gap-2 text-xs uppercase tracking-widest text-slate-500 mb-3">
            <ListChecks size={14} /> Steps
          </div>
          <ol className="space-y-3">
            {plan.steps.map((s) => (
              <li
                key={s.step}
                className="border-l-2 border-cyan-500/40 pl-3"
                data-testid={`coach-step-${s.step}`}
              >
                <div className="text-sm font-semibold text-slate-200">
                  {s.step}. {s.title}
                </div>
                <div className="text-xs text-slate-400 mt-1">{s.why}</div>
                <div className="text-xs text-cyan-400 mt-1.5 font-mono">
                  → {s.practice}
                </div>
              </li>
            ))}
          </ol>
        </div>
      )}

      {plan.drills?.length > 0 && (
        <div className="bg-slate-900/40 border border-slate-800 rounded-lg p-4">
          <div className="flex items-center gap-2 text-xs uppercase tracking-widest text-slate-500 mb-2">
            <Sparkles size={14} /> Drills
          </div>
          <ul className="space-y-1 text-sm text-slate-300 list-disc pl-5">
            {plan.drills.map((d, i) => <li key={i}>{d}</li>)}
          </ul>
        </div>
      )}

      {plan.pitfalls?.length > 0 && (
        <div className="bg-amber-500/5 border border-amber-500/20 rounded-lg p-4">
          <div className="flex items-center gap-2 text-xs uppercase tracking-widest text-amber-400 mb-2">
            <AlertTriangle size={14} /> Common pitfalls
          </div>
          <ul className="space-y-1 text-sm text-amber-200/90 list-disc pl-5">
            {plan.pitfalls.map((p, i) => <li key={i}>{p}</li>)}
          </ul>
        </div>
      )}
    </div>
  );
}

function ReviewView({ review }) {
  if (!review) return null;
  return (
    <div className="space-y-4" data-testid="coach-review-view">
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <Stat
          label="Parses"
          value={review.parses ? 'yes' : 'no'}
          accent={review.parses ? 'emerald' : 'rose'}
          testid="coach-review-parses"
        />
        <Stat label="Lines" value={review.line_count} testid="coach-review-lines" />
        <Stat label="Functions" value={review.function_count} testid="coach-review-funcs" />
        <Stat
          label="Docstrings"
          value={review.has_docstrings ? 'yes' : 'no'}
          accent={review.has_docstrings ? 'emerald' : 'amber'}
          testid="coach-review-docs"
        />
      </div>

      <div className="bg-slate-900/40 border border-slate-800 rounded-lg p-4">
        <div className="flex items-center gap-2 text-xs uppercase tracking-widest text-slate-500 mb-3">
          <Code2 size={14} /> Static review
        </div>
        <div className="text-sm text-slate-300 mb-3">{review.summary}</div>
        <FindingsList findings={review.findings || []} />
      </div>

      {review.deep_feedback && (
        <div className="bg-cyan-500/5 border border-cyan-500/20 rounded-lg p-4">
          <div className="flex items-center gap-2 text-xs uppercase tracking-widest text-cyan-400 mb-2">
            <Sparkles size={14} /> Deep feedback (LLM)
          </div>
          <div
            className="text-sm text-slate-200 whitespace-pre-wrap"
            data-testid="coach-deep-feedback"
          >
            {review.deep_feedback}
          </div>
        </div>
      )}

      {review.next_drills?.length > 0 && (
        <div className="bg-slate-900/40 border border-slate-800 rounded-lg p-4">
          <div className="flex items-center gap-2 text-xs uppercase tracking-widest text-slate-500 mb-2">
            <Sparkles size={14} /> Next drills
          </div>
          <ul className="space-y-1 text-sm text-slate-300 list-disc pl-5">
            {review.next_drills.map((d, i) => <li key={i}>{d}</li>)}
          </ul>
        </div>
      )}
    </div>
  );
}

function Stat({ label, value, accent = 'slate', testid }) {
  const palette = {
    slate:   'text-slate-300',
    cyan:    'text-cyan-400',
    emerald: 'text-emerald-400',
    amber:   'text-amber-400',
    rose:    'text-rose-400',
  };
  return (
    <div
      data-testid={testid}
      className="bg-slate-900/60 border border-slate-800 rounded-lg p-3"
    >
      <div className="text-[10px] uppercase tracking-widest text-slate-500">
        {label}
      </div>
      <div className={`text-lg font-semibold ${palette[accent]}`}>{value}</div>
    </div>
  );
}

const DEFAULT_GOAL =
  'Build a Python function that fetches stock prices, retries on failure, and returns a clean dictionary.';

export default function PythonCoach() {
  const [goal, setGoal] = React.useState(DEFAULT_GOAL);
  const [code, setCode] = React.useState('');
  const [plan, setPlan] = React.useState(null);
  const [review, setReview] = React.useState(null);
  const [busy, setBusy] = React.useState(null);
  const [deep, setDeep] = React.useState(false);
  const [error, setError] = React.useState(null);

  const onPlan = async () => {
    setBusy('plan');
    setError(null);
    setPlan(null);
    try {
      const r = await authFetch(`${API}/admin/python-coach/plan`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ goal }),
      });
      const json = await r.json();
      if (!r.ok) throw new Error(json.detail || `plan failed (${r.status})`);
      setPlan(json);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(null);
    }
  };

  const onReview = async () => {
    if (!code.trim()) {
      setError('Paste some Python code first.');
      return;
    }
    setBusy('review');
    setError(null);
    setReview(null);
    try {
      const r = await authFetch(`${API}/admin/python-coach/review`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ code, goal, deep }),
      });
      const json = await r.json();
      if (!r.ok) throw new Error(json.detail || `review failed (${r.status})`);
      setReview(json);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(null);
    }
  };

  const onLoadExample = async () => {
    setBusy('example');
    setError(null);
    try {
      const r = await authFetch(`${API}/admin/python-coach/example`);
      const json = await r.json();
      if (!r.ok) throw new Error(json.detail || `example failed (${r.status})`);
      setCode(json.code || '');
      if (json.goal) setGoal(json.goal);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(null);
    }
  };

  return (
    <div data-testid="python-coach" className="space-y-5">
      <div>
        <h2 className="text-2xl font-semibold text-slate-100 mb-1 flex items-center gap-2">
          <GraduationCap className="text-cyan-400" size={22} />
          Python Coach
        </h2>
        <p className="text-sm text-slate-400 max-w-2xl">
          Turn a plain-English goal into a focused Python lesson plan,
          and statically review pasted code. The coach never executes
          your code — feedback is AST-based, plus an optional LLM pass
          for richer review.
          <span className="text-amber-400">
            {' '}Operator-only. The coach is firewalled from the code-evolution
            gate and execution paths.
          </span>
        </p>
      </div>

      {error && (
        <div
          className="bg-rose-500/10 border border-rose-500/30 rounded-lg px-4 py-3 text-sm text-rose-300 flex items-start gap-2"
          data-testid="coach-error"
        >
          <AlertTriangle size={16} className="flex-shrink-0 mt-0.5" />
          <div>{error}</div>
        </div>
      )}

      {/* Goal + code split */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <div className="bg-slate-900/40 border border-slate-800 rounded-lg p-4 space-y-3">
          <div className="text-xs uppercase tracking-widest text-slate-500 font-mono flex items-center gap-2">
            <FileText size={14} /> Learning goal
          </div>
          <textarea
            data-testid="coach-goal-input"
            value={goal}
            onChange={(e) => setGoal(e.target.value)}
            rows={3}
            className="w-full bg-slate-950 border border-slate-700 rounded-md p-2 text-sm text-slate-200 font-mono focus:outline-none focus:border-cyan-500"
            placeholder="What do you want to learn? Example: build a retry-aware HTTP fetcher."
          />
          <div className="flex gap-2">
            <button
              type="button"
              data-testid="coach-plan-btn"
              disabled={busy === 'plan' || !goal.trim()}
              onClick={onPlan}
              className={`px-4 py-2 rounded-md text-sm font-semibold transition-colors flex items-center gap-2 ${
                busy === 'plan' || !goal.trim()
                  ? 'bg-slate-800 text-slate-500 cursor-not-allowed'
                  : 'bg-cyan-500 hover:bg-cyan-400 text-slate-950'
              }`}
            >
              <Sparkles size={14} className={busy === 'plan' ? 'animate-pulse' : ''} />
              {busy === 'plan' ? 'Planning…' : 'Generate lesson plan'}
            </button>
          </div>
        </div>

        <div className="bg-slate-900/40 border border-slate-800 rounded-lg p-4 space-y-3">
          <div className="flex items-center justify-between">
            <div className="text-xs uppercase tracking-widest text-slate-500 font-mono flex items-center gap-2">
              <Code2 size={14} /> Python code
            </div>
            <label className="text-[11px] text-slate-400 flex items-center gap-1.5 select-none">
              <input
                type="checkbox"
                data-testid="coach-deep-toggle"
                checked={deep}
                onChange={(e) => setDeep(e.target.checked)}
                className="accent-cyan-500"
              />
              Deep LLM feedback
            </label>
          </div>
          <textarea
            data-testid="coach-code-input"
            value={code}
            onChange={(e) => setCode(e.target.value)}
            rows={10}
            spellCheck={false}
            className="w-full bg-slate-950 border border-slate-700 rounded-md p-2 text-xs text-slate-200 font-mono focus:outline-none focus:border-cyan-500"
            placeholder="# Paste Python code to review…"
          />
          <div className="flex gap-2 flex-wrap">
            <button
              type="button"
              data-testid="coach-review-btn"
              disabled={busy === 'review' || !code.trim()}
              onClick={onReview}
              className={`px-4 py-2 rounded-md text-sm font-semibold transition-colors flex items-center gap-2 ${
                busy === 'review' || !code.trim()
                  ? 'bg-slate-800 text-slate-500 cursor-not-allowed'
                  : 'bg-emerald-500 hover:bg-emerald-400 text-slate-950'
              }`}
            >
              <CheckCircle2 size={14} className={busy === 'review' ? 'animate-pulse' : ''} />
              {busy === 'review' ? 'Reviewing…' : 'Review pasted code'}
            </button>
            <button
              type="button"
              data-testid="coach-example-btn"
              disabled={busy === 'example'}
              onClick={onLoadExample}
              className={`px-4 py-2 rounded-md text-sm font-semibold transition-colors flex items-center gap-2 ${
                busy === 'example'
                  ? 'bg-slate-800 text-slate-500 cursor-not-allowed'
                  : 'bg-slate-700 hover:bg-slate-600 text-slate-200'
              }`}
            >
              <RefreshCw size={14} className={busy === 'example' ? 'animate-spin' : ''} />
              Load example
            </button>
          </div>
        </div>
      </div>

      {plan && (
        <section data-testid="coach-plan-section">
          <div className="text-xs uppercase tracking-widest text-cyan-400 font-mono mb-2">
            Lesson plan
          </div>
          <LessonPlanView plan={plan} />
        </section>
      )}

      {review && (
        <section data-testid="coach-review-section">
          <div className="text-xs uppercase tracking-widest text-emerald-400 font-mono mb-2">
            Code review
          </div>
          <ReviewView review={review} />
        </section>
      )}

      {!plan && !review && (
        <div
          className="bg-slate-900/40 border border-slate-800 rounded-lg p-8 text-center"
          data-testid="coach-empty"
        >
          <GraduationCap size={28} className="mx-auto text-slate-600 mb-2" />
          <div className="text-sm text-slate-400">
            Ready when you are. Try{' '}
            <span className="text-cyan-400 font-mono">Generate lesson plan</span>{' '}
            first, then paste code to review.
          </div>
        </div>
      )}
    </div>
  );
}
