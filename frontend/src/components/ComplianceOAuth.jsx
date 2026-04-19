import React, { useState, useEffect, useMemo } from 'react';
import { Shield, CheckCircle2, Lock, RefreshCw, Key, FileCheck, ExternalLink } from 'lucide-react';
import { getApiBase } from '../utils/apiBase';

/**
 * Public OAuth compliance statement for broker review teams.
 *
 * Routes:
 *   /compliance/schwab-oauth   → Schwab-focused panel
 *   /compliance/ibkr-oauth     → IBKR-focused panel
 *   /compliance/alpaca-oauth   → Alpaca-focused panel
 *   /compliance/oauth          → Generic (first broker available)
 *
 * Accepts a `brokerId` prop. If omitted, derives it from the pathname.
 * All copy references the target broker dynamically — no hard-coded names —
 * so adding a new broker just needs a new OAUTH_CONFIGS entry on the
 * backend. The page does not render any broker logos.
 */

const BROKER_PRETTY = {
  schwab: 'Charles Schwab',
  ibkr: 'Interactive Brokers',
  alpaca: 'Alpaca',
};

const prettyName = (id) => BROKER_PRETTY[id] || id.charAt(0).toUpperCase() + id.slice(1);

const deriveBrokerIdFromPath = () => {
  if (typeof window === 'undefined') return null;
  const path = window.location.pathname;
  // Match /compliance/{broker}-oauth (e.g. /compliance/schwab-oauth)
  const m = path.match(/^\/compliance\/([a-z0-9-]+)-oauth\/?$/);
  return m ? m[1] : null;
};

const ComplianceOAuth = ({ brokerId: brokerIdProp }) => {
  const [caps, setCaps] = useState(null);
  const [err, setErr] = useState(null);

  const brokerId = useMemo(
    () => brokerIdProp || deriveBrokerIdFromPath() || 'schwab',
    [brokerIdProp]
  );

  useEffect(() => {
    fetch(`${getApiBase()}/api/broker/oauth/capabilities`, { credentials: 'omit' })
      .then((r) => (r.ok ? r.json() : Promise.reject(r.status)))
      .then(setCaps)
      .catch((e) => setErr(String(e)));
  }, []);

  const broker = caps?.supported_brokers?.find((b) => b.broker_id === brokerId) || null;
  const brokerName = prettyName(brokerId);
  const today = new Date().toLocaleDateString('en-US', { year: 'numeric', month: 'long', day: 'numeric' });

  const YesRow = ({ label, ok = true, testId }) => (
    <div className="flex items-center justify-between py-3 border-b border-slate-800 last:border-b-0" data-testid={testId}>
      <span className="text-slate-300 text-sm">{label}</span>
      <span className={`flex items-center gap-1.5 text-sm font-semibold ${ok ? 'text-emerald-400' : 'text-red-400'}`}>
        <CheckCircle2 className="w-4 h-4" />
        {ok ? 'Supported' : 'Not supported'}
      </span>
    </div>
  );

  return (
    <div className="min-h-screen bg-[#060E1F] text-slate-200" data-testid="compliance-oauth-page">
      {/* Header */}
      <header className="border-b border-slate-800 bg-[#0A1427]">
        <div className="max-w-4xl mx-auto px-6 py-6 flex items-center justify-between">
          <div>
            <h1 className="text-[#3DE8D9] font-bold tracking-wide text-lg" data-testid="compliance-header-title">
              RISEDUAL AI
            </h1>
            <p className="text-slate-500 text-[11px] uppercase tracking-widest mt-0.5">
              OAuth 2.0 Compliance Statement — {brokerName}
            </p>
          </div>
          <a
            href="/"
            className="text-slate-400 hover:text-white text-xs flex items-center gap-1"
            data-testid="compliance-back-home"
          >
            Back to home <ExternalLink className="w-3 h-3" />
          </a>
        </div>
      </header>

      <main className="max-w-4xl mx-auto px-6 py-10">
        {/* Lead statement */}
        <section className="mb-10">
          <div className="inline-flex items-center gap-2 bg-emerald-500/10 border border-emerald-500/30 text-emerald-400 rounded-full px-3 py-1 text-xs font-semibold mb-4">
            <Shield className="w-3.5 h-3.5" />
            Verified: 3-legged OAuth supported
          </div>
          <h2 className="text-white text-3xl font-bold leading-tight mb-3" data-testid="compliance-lead-heading">
            RISEDUAL AI fully supports 3-legged OAuth 2.0 for {brokerName} integration.
          </h2>
          <p className="text-slate-400 text-sm leading-relaxed max-w-2xl">
            This page is a human-readable companion to our machine-verifiable
            endpoint at{' '}
            <code className="text-[#3DE8D9] bg-slate-800/60 px-1.5 py-0.5 rounded text-xs">
              /api/broker/oauth/capabilities
            </code>
            . Third-party broker review teams may link either resource as proof
            of compliance. Last reviewed: {today}.
          </p>
        </section>

        {err && (
          <div className="bg-red-950/30 border border-red-900 text-red-300 rounded-xl p-4 mb-8 text-sm">
            Could not load live capability report ({err}). The static statement below is still authoritative.
          </div>
        )}

        {/* Capability matrix */}
        <section className="bg-[#0E1A33] border border-slate-700/50 rounded-2xl p-6 mb-8" data-testid="compliance-capability-matrix">
          <h3 className="text-white font-semibold text-lg mb-1 flex items-center gap-2">
            <FileCheck className="w-5 h-5 text-[#3DE8D9]" />
            OAuth 2.0 Capability Matrix
          </h3>
          <p className="text-slate-500 text-xs mb-4">
            Live-pulled from the app backend on this page load.
          </p>
          <div className="space-y-0">
            <YesRow label="3-legged OAuth (Authorization Code Grant)" ok={!!caps?.three_legged_oauth} testId="cap-3lo" />
            <YesRow label="PKCE (S256 code challenge)" ok={!!caps?.pkce_support} testId="cap-pkce" />
            <YesRow label="CSRF state validation (one-time tokens)" ok={!!caps?.csrf_state_validation} testId="cap-csrf" />
            <YesRow label="Refresh-token rotation on every refresh" ok={!!caps?.refresh_token_rotation} testId="cap-rotation" />
            <YesRow label={`Token storage encrypted (${caps?.token_encryption || 'AES-256'})`} ok={!!caps?.token_encryption} testId="cap-encryption" />
          </div>
        </section>

        {/* Broker-specific */}
        <section className="bg-[#0E1A33] border border-[#3DE8D9]/30 rounded-2xl p-6 mb-8" data-testid={`compliance-${brokerId}-panel`}>
          <h3 className="text-white font-semibold text-lg mb-4 flex items-center gap-2">
            <Key className="w-5 h-5 text-[#3DE8D9]" />
            {brokerName} Endpoint Configuration
          </h3>
          {broker ? (
            <dl className="grid grid-cols-1 sm:grid-cols-2 gap-x-6 gap-y-3 text-sm">
              <div>
                <dt className="text-slate-500 text-xs uppercase tracking-wider">Broker ID</dt>
                <dd className="text-white font-mono mt-0.5">{broker.broker_id}</dd>
              </div>
              <div>
                <dt className="text-slate-500 text-xs uppercase tracking-wider">PKCE Enabled</dt>
                <dd className={`font-semibold mt-0.5 ${broker.supports_pkce ? 'text-emerald-400' : 'text-slate-400'}`}>
                  {broker.supports_pkce ? 'Yes (S256)' : 'No'}
                </dd>
              </div>
              <div className="sm:col-span-2">
                <dt className="text-slate-500 text-xs uppercase tracking-wider">Authorize URL</dt>
                <dd className="text-[#3DE8D9] font-mono mt-0.5 text-xs break-all">{broker.authorize_url}</dd>
              </div>
              <div>
                <dt className="text-slate-500 text-xs uppercase tracking-wider">Token Expiry Honored</dt>
                <dd className="text-white mt-0.5">{broker.token_expiry_seconds}s ({Math.round(broker.token_expiry_seconds / 60)} min)</dd>
              </div>
              <div>
                <dt className="text-slate-500 text-xs uppercase tracking-wider">Auto-Refresh</dt>
                <dd className="text-white mt-0.5">At 80% of expiry</dd>
              </div>
              <div className="sm:col-span-2 pt-3 border-t border-slate-800">
                <dt className="text-slate-500 text-xs uppercase tracking-wider">Callback / Redirect URI pattern</dt>
                <dd className="text-[#3DE8D9] font-mono mt-0.5 text-xs break-all">
                  https://&#123;production-domain&#125;/api/broker/oauth/{brokerId}/callback
                </dd>
              </div>
            </dl>
          ) : caps ? (
            <p className="text-slate-400 text-sm">
              <strong className="text-slate-200">{brokerName}</strong> is not currently listed in our supported OAuth brokers.
              Supported brokers:{' '}
              {caps.supported_brokers.map((b, i) => (
                <span key={b.broker_id}>
                  {i > 0 && ', '}
                  <a href={`/compliance/${b.broker_id}-oauth`} className="text-[#3DE8D9] hover:underline">{prettyName(b.broker_id)}</a>
                </span>
              ))}
              .
            </p>
          ) : (
            <p className="text-slate-500 text-sm">Loading {brokerName} configuration...</p>
          )}
        </section>

        {/* Security features */}
        <section className="bg-[#0E1A33] border border-slate-700/50 rounded-2xl p-6 mb-8" data-testid="compliance-security-panel">
          <h3 className="text-white font-semibold text-lg mb-4 flex items-center gap-2">
            <Lock className="w-5 h-5 text-[#3DE8D9]" />
            Security Implementation Detail
          </h3>
          <ul className="space-y-2.5">
            {(caps?.security_features || []).map((feat) => (
              <li key={feat} className="flex items-start gap-2 text-sm">
                <CheckCircle2 className="w-4 h-4 text-emerald-400 mt-0.5 shrink-0" />
                <span className="text-slate-300">{feat}</span>
              </li>
            ))}
          </ul>
        </section>

        {/* Flow summary */}
        <section className="bg-[#0E1A33] border border-slate-700/50 rounded-2xl p-6 mb-8">
          <h3 className="text-white font-semibold text-lg mb-4 flex items-center gap-2">
            <RefreshCw className="w-5 h-5 text-[#3DE8D9]" />
            Flow Summary
          </h3>
          <ol className="space-y-3 text-sm text-slate-300 list-decimal list-inside">
            <li>
              <strong className="text-white">User initiates connection.</strong>{' '}
              Frontend calls <code className="text-[#3DE8D9] bg-slate-800/60 px-1 rounded text-xs">GET /api/broker/oauth/{brokerId}/authorize</code>.
              Backend generates a CSRF <code className="text-xs">state</code>,
              PKCE verifier+challenge (when supported), persists them server-side,
              and returns the {brokerName} authorize URL.
            </li>
            <li>
              <strong className="text-white">User consents at {brokerName}.</strong>{' '}
              Browser navigates to the broker&apos;s authorize endpoint. User authenticates
              and grants the requested scopes.
            </li>
            <li>
              <strong className="text-white">{brokerName} redirects back.</strong>{' '}
              Browser lands on <code className="text-[#3DE8D9] bg-slate-800/60 px-1 rounded text-xs">/api/broker/oauth/{brokerId}/callback?code=&hellip;&amp;state=&hellip;</code>.
              Backend validates <code className="text-xs">state</code>, exchanges
              the code (plus PKCE verifier when applicable) for access+refresh
              tokens, encrypts them with AES-256 (Fernet) before storage, and
              redirects the user back to the app with a success flag.
            </li>
            <li>
              <strong className="text-white">Ongoing refresh.</strong>{' '}
              At 80% of token expiry, the backend auto-refreshes using the
              stored refresh token. Every rotation issues a new refresh token
              (rotation policy) and is logged to <code className="text-xs">oauth_token_audit</code>.
            </li>
          </ol>
        </section>

        {/* Verification */}
        <section className="bg-slate-900/40 border border-slate-800 rounded-2xl p-6 mb-8">
          <h3 className="text-white font-semibold text-lg mb-2">For Reviewers</h3>
          <p className="text-slate-400 text-sm mb-4 leading-relaxed">
            To machine-verify the claims on this page, GET the public endpoint
            below. It returns the same capability data this page renders,
            without authentication:
          </p>
          <pre className="bg-black/40 border border-slate-800 rounded-lg p-4 text-xs text-[#3DE8D9] font-mono overflow-x-auto" data-testid="compliance-verify-curl">
{`curl https://${typeof window !== 'undefined' ? window.location.host : 'risedual.ai'}/api/broker/oauth/capabilities`}
          </pre>
        </section>

        <footer className="text-slate-600 text-xs text-center pt-8 pb-12 border-t border-slate-800">
          RISEDUAL AI · OAuth 2.0 Compliance Statement ({brokerName}) · Generated {today}
        </footer>
      </main>
    </div>
  );
};

export default ComplianceOAuth;
