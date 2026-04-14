import React from "react";
import {
  ShieldCheck,
  Search,
  Database,
  Clock3,
  AlertTriangle,
  XCircle,
  CheckCircle2,
} from "lucide-react";

const trustBadgeMap = {
  official: {
    label: "Official",
    icon: ShieldCheck,
    className: "border-emerald-400/25 bg-emerald-500/12 text-emerald-200",
  },
  search: {
    label: "Search",
    icon: Search,
    className: "border-sky-400/25 bg-sky-500/12 text-sky-200",
  },
  fallback: {
    label: "Fallback",
    icon: Database,
    className: "border-amber-400/25 bg-amber-500/12 text-amber-200",
  },
  cached: {
    label: "Cached",
    icon: Clock3,
    className: "border-violet-400/25 bg-violet-500/12 text-violet-200",
  },
};

const statusBadgeMap = {
  live: {
    label: "Live",
    icon: CheckCircle2,
    className: "border-teal-400/25 bg-teal-500/12 text-teal-200",
  },
  partial: {
    label: "Partial",
    icon: AlertTriangle,
    className: "border-yellow-400/25 bg-yellow-500/12 text-yellow-100",
  },
  timeout: {
    label: "Timeout",
    icon: Clock3,
    className: "border-orange-400/25 bg-orange-500/12 text-orange-200",
  },
  error: {
    label: "Error",
    icon: XCircle,
    className: "border-rose-400/25 bg-rose-500/12 text-rose-200",
  },
};

function getTrustKind(result) {
  if (result.cached) return "cached";
  if (result.engine === "sec" || result.engine === "fred") return "official";
  if (result.engine === "ddg" || result.engine === "ddg_news") return "search";
  return "fallback";
}

function getStatusKind(result) {
  if (result.status === "ok" || result.status === "cached") return "live";
  if (result.status === "timeout") return "timeout";
  if (result.status === "error") return "error";
  return "partial";
}

function Badge({ config }) {
  const Icon = config.icon;
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[11px] font-medium tracking-wide ${config.className}`}
    >
      <Icon className="h-3.5 w-3.5" />
      {config.label}
    </span>
  );
}

function formatFreshness(seconds) {
  if (seconds == null) return "Unknown freshness";
  if (seconds < 60) return `${seconds}s old`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m old`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h old`;
  return `${Math.floor(seconds / 86400)}d old`;
}

export function WarRoomSourceCard({ result }) {
  const trustKind = getTrustKind(result);
  const statusKind = getStatusKind(result);

  const trustConfig = trustBadgeMap[trustKind];
  const statusConfig = statusBadgeMap[statusKind];

  return (
    <div className="group rounded-2xl border border-white/10 bg-[#0B1426] p-4 shadow-[0_10px_30px_rgba(0,0,0,0.28)] transition-all duration-200 hover:border-white/15 hover:bg-[#101a31]">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <h3 className="truncate text-sm font-semibold text-white">
              {result.title || result.engine?.toUpperCase()}
            </h3>
          </div>
          <p className="mt-1 text-xs text-slate-400">
            Source: <span className="uppercase tracking-wide">{result.engine}</span>
          </p>
        </div>

        <div className="flex shrink-0 flex-wrap items-center justify-end gap-2">
          <Badge config={trustConfig} />
          <Badge config={statusConfig} />
        </div>
      </div>

      <p className="mt-3 text-sm leading-6 text-slate-200">
        {result.summary || "No summary available."}
      </p>

      <div className="mt-4 flex flex-wrap gap-x-4 gap-y-2 text-xs text-slate-400">
        <span>
          Confidence: <span className="text-slate-200">{Math.round((result.confidence || 0) * 100)}%</span>
        </span>
        <span>
          Items: <span className="text-slate-200">{result.items?.length || 0}</span>
        </span>
        <span>
          Freshness: <span className="text-slate-200">{formatFreshness(result.freshness_seconds)}</span>
        </span>
      </div>

      {result.error && (
        <div className="mt-4 rounded-xl border border-rose-400/20 bg-rose-500/10 px-3 py-2 text-xs leading-5 text-rose-200">
          {result.error}
        </div>
      )}

      {!!result.items?.length && (
        <div className="mt-4 rounded-xl border border-white/[0.08] bg-white/5 p-3">
          <p className="mb-2 text-[11px] font-semibold uppercase tracking-[0.14em] text-slate-400">
            Top items
          </p>
          <div className="space-y-2">
            {result.items.slice(0, 3).map((item, idx) => (
              <div
                key={item.url || item.accession || `item-${idx}`}
                className="rounded-lg border border-white/[0.06] bg-[#0E1830] px-3 py-2"
              >
                <p className="text-xs font-medium text-slate-100">
                  {item.title || item.form || item.date || `Item ${idx + 1}`}
                </p>
                {(item.snippet || item.filingDate || item.value) && (
                  <p className="mt-1 text-xs text-slate-400">
                    {item.snippet || item.filingDate || item.value}
                  </p>
                )}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

export function WarRoomBriefHeader({ brief, degraded, engineCount, okCount }) {
  return (
    <div className="rounded-3xl border border-cyan-400/15 bg-[linear-gradient(180deg,rgba(25,37,62,0.96),rgba(10,18,34,0.96))] p-5 shadow-[0_20px_60px_rgba(0,0,0,0.35)]">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-cyan-300/80">
            Search War Room
          </p>
          <h2 className="mt-2 text-xl font-semibold text-white">
            {brief.headline}
          </h2>
        </div>

        <div className="flex flex-wrap gap-2">
          <Badge
            config={degraded
              ? { label: "Degraded", icon: AlertTriangle, className: "border-yellow-400/25 bg-yellow-500/12 text-yellow-200" }
              : { label: "All Clear", icon: CheckCircle2, className: "border-emerald-400/25 bg-emerald-500/12 text-emerald-200" }
            }
          />
          <span className="inline-flex items-center gap-1.5 rounded-full border border-white/15 bg-white/5 px-2.5 py-1 text-[11px] font-medium text-slate-300">
            {okCount}/{engineCount} engines
          </span>
        </div>
      </div>

      <p className="mt-3 text-sm leading-6 text-slate-300">
        {brief.summary}
      </p>

      {brief.signals?.length > 0 && (
        <div className="mt-4 flex flex-wrap gap-2">
          {brief.signals.map((s, i) => (
            <span
              key={`signal-${i}`}
              className="rounded-full border border-white/10 bg-white/5 px-3 py-1 text-[11px] text-slate-300"
            >
              {s}
            </span>
          ))}
        </div>
      )}

      {brief.risks?.length > 0 && (
        <div className="mt-3 space-y-1.5">
          {brief.risks.map((r, i) => (
            <div key={`risk-${i}`} className="flex items-center gap-2 text-xs text-amber-300/90">
              <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
              {r}
            </div>
          ))}
        </div>
      )}

      {brief.sources_used?.length > 0 && (
        <div className="mt-3 text-[11px] text-slate-500">
          Sources: {brief.sources_used.join(" / ")}
        </div>
      )}
    </div>
  );
}
