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
    className: "border-[rgb(var(--wr-badge-official-border)/.25)] bg-[rgb(var(--wr-badge-official-bg)/.12)] text-[rgb(var(--wr-badge-official-text))]",
  },
  search: {
    label: "Search",
    icon: Search,
    className: "border-[rgb(var(--wr-badge-search-border)/.25)] bg-[rgb(var(--wr-badge-search-bg)/.12)] text-[rgb(var(--wr-badge-search-text))]",
  },
  fallback: {
    label: "Fallback",
    icon: Database,
    className: "border-[rgb(var(--wr-badge-fallback-border)/.25)] bg-[rgb(var(--wr-badge-fallback-bg)/.12)] text-[rgb(var(--wr-badge-fallback-text))]",
  },
  cached: {
    label: "Cached",
    icon: Clock3,
    className: "border-[rgb(var(--wr-badge-cached-border)/.25)] bg-[rgb(var(--wr-badge-cached-bg)/.12)] text-[rgb(var(--wr-badge-cached-text))]",
  },
};

const statusBadgeMap = {
  live: {
    label: "Live",
    icon: CheckCircle2,
    className: "border-[rgb(var(--wr-status-live-border)/.25)] bg-[rgb(var(--wr-status-live-bg)/.12)] text-[rgb(var(--wr-status-live-text))]",
  },
  partial: {
    label: "Partial",
    icon: AlertTriangle,
    className: "border-[rgb(var(--wr-status-partial-border)/.25)] bg-[rgb(var(--wr-status-partial-bg)/.12)] text-[rgb(var(--wr-status-partial-text))]",
  },
  timeout: {
    label: "Timeout",
    icon: Clock3,
    className: "border-[rgb(var(--wr-status-timeout-border)/.25)] bg-[rgb(var(--wr-status-timeout-bg)/.12)] text-[rgb(var(--wr-status-timeout-text))]",
  },
  error: {
    label: "Error",
    icon: XCircle,
    className: "border-[rgb(var(--wr-status-error-border)/.25)] bg-[rgb(var(--wr-status-error-bg)/.12)] text-[rgb(var(--wr-status-error-text))]",
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
    <div className="group rounded-2xl border border-[rgb(var(--wr-border)/.10)] bg-[rgb(var(--wr-surface-1))] p-4 shadow-[0_10px_30px_rgba(0,0,0,0.28)] transition-all duration-200 hover:border-[rgb(var(--wr-border)/.15)] hover:bg-[rgb(var(--wr-surface-2))]">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <h3 className="truncate text-sm font-semibold text-[rgb(var(--wr-text))]">
              {result.title || result.engine?.toUpperCase()}
            </h3>
          </div>
          <p className="mt-1 text-xs text-[rgb(var(--wr-text-muted))]">
            Source: <span className="uppercase tracking-wide">{result.engine}</span>
          </p>
        </div>

        <div className="flex shrink-0 flex-wrap items-center justify-end gap-2">
          <Badge config={trustConfig} />
          <Badge config={statusConfig} />
        </div>
      </div>

      <p className="mt-3 text-sm leading-6 text-[rgb(var(--wr-text)/.85)]">
        {result.summary || "No summary available."}
      </p>

      <div className="mt-4 flex flex-wrap gap-x-4 gap-y-2 text-xs text-[rgb(var(--wr-text-muted))]">
        <span>
          Confidence: <span className="text-[rgb(var(--wr-text)/.85)]">{Math.round((result.confidence || 0) * 100)}%</span>
        </span>
        <span>
          Items: <span className="text-[rgb(var(--wr-text)/.85)]">{result.items?.length || 0}</span>
        </span>
        <span>
          Freshness: <span className="text-[rgb(var(--wr-text)/.85)]">{formatFreshness(result.freshness_seconds)}</span>
        </span>
      </div>

      {result.error && (
        <div className="mt-4 rounded-xl border border-rose-400/20 bg-rose-500/10 px-3 py-2 text-xs leading-5 text-rose-200">
          {result.error}
        </div>
      )}

      {!!result.items?.length && (
        <div className="mt-4 rounded-xl border border-[rgb(var(--wr-border)/.08)] bg-[rgb(var(--wr-border)/.05)] p-3">
          <p className="mb-2 text-[11px] font-semibold uppercase tracking-[0.14em] text-[rgb(var(--wr-text-muted))]">
            Top items
          </p>
          <div className="space-y-2">
            {result.items.slice(0, 3).map((item, idx) => (
              <div
                key={item.url || item.accession || `item-${idx}`}
                className="rounded-lg border border-[rgb(var(--wr-border)/.06)] bg-[rgb(var(--wr-surface-2))] px-3 py-2"
              >
                <p className="text-xs font-medium text-[rgb(var(--wr-text))]">
                  {item.title || item.form || item.date || `Item ${idx + 1}`}
                </p>
                {(item.snippet || item.filingDate || item.value) && (
                  <p className="mt-1 text-xs text-[rgb(var(--wr-text-muted))]">
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

export function WarRoomBriefHeader({ brief, degraded }) {
  return (
    <div className="rounded-3xl border border-cyan-400/15 bg-[linear-gradient(180deg,rgb(var(--wr-surface-3)),rgb(var(--wr-bg)))] p-5 shadow-[0_20px_60px_rgba(0,0,0,0.35)]">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-cyan-300/80">
            Search War Room
          </p>
          <h2 className="mt-2 text-xl font-semibold text-[rgb(var(--wr-text))]">
            {brief.headline}
          </h2>
        </div>

        <div className="flex flex-wrap gap-2">
          <Badge
            config={
              degraded
                ? statusBadgeMap.partial
                : statusBadgeMap.live
            }
          />
        </div>
      </div>

      <p className="mt-4 max-w-3xl text-sm leading-7 text-[rgb(var(--wr-text)/.85)]">
        {brief.summary}
      </p>

      {!!brief.signals?.length && (
        <div className="mt-5 flex flex-wrap gap-2">
          {brief.signals.map((signal, idx) => (
            <span
              key={`signal-${idx}`}
              className="inline-flex items-center rounded-full border border-cyan-400/15 bg-cyan-400/[0.08] px-3 py-1.5 text-xs text-cyan-100"
            >
              {signal}
            </span>
          ))}
        </div>
      )}

      {!!brief.risks?.length && (
        <div className="mt-4 rounded-2xl border border-amber-400/15 bg-amber-500/[0.08] p-3">
          <p className="text-[11px] font-semibold uppercase tracking-[0.16em] text-amber-200/80">
            Caveats
          </p>
          <ul className="mt-2 space-y-1">
            {brief.risks.map((risk, idx) => (
              <li key={`risk-${idx}`} className="text-sm text-amber-100">
                {risk}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

export default function WarRoomResults({ data }) {
  const results = data?.engine_results || [];
  const brief = data?.brief;

  return (
    <section className="space-y-5">
      {brief && (
        <WarRoomBriefHeader brief={brief} degraded={data?.degraded} />
      )}

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        {results.map((result, idx) => (
          <WarRoomSourceCard key={`${result.engine}-${idx}`} result={result} />
        ))}
      </div>
    </section>
  );
}
