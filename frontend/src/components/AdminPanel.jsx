import React, { useState, useEffect, useCallback } from 'react';
import { Users, Crown, RefreshCw, Gift, FileCode, Database, Key, Lock, Film, ShieldCheck, Activity, Search, MessageSquare, TrendingUp, Bitcoin, X, Eye, FileText, HeartPulse, AlertTriangle, Radio, GitBranch, Brain } from 'lucide-react';
import { Button } from './ui/button';
import { toast } from './ui/sonner';
import { authFetch } from '../contexts/AuthContext';
import AdminTools from './admin/AdminTools';
import PromoManager from './admin/PromoManager';
import CacheMonitor from './admin/CacheMonitor';
import BrokerOAuthConfig from './admin/BrokerOAuthConfig';
import MediaManager from './admin/MediaManager';
import SecurityAudit from './admin/SecurityAudit';
import WaitlistAdmin from './admin/WaitlistAdmin';
import UsersTab from './admin/UsersTab';
import ProviderHealth from './admin/ProviderHealth';
import KeyVault from './admin/KeyVault';
import HelpSearchInsights from './admin/HelpSearchInsights';
import ChipAdoptionInsights from './admin/ChipAdoptionInsights';
import ConvictionCalibration from './admin/ConvictionCalibration';
import CryptoPaperDashboard from './CryptoPaperDashboard';
import CryptoAdversarialDashboard from './CryptoAdversarialDashboard';
import ShadowAccuracyPanel from './admin/ShadowAccuracyPanel';
import CouncilTierStatusPill from './admin/CouncilTierStatusPill';
import PatentWatchPanel from './admin/PatentWatchPanel';
import ProofChainExplorer from './admin/ProofChainExplorer';
import GuardShadowPanel from './admin/GuardShadowPanel';
import OpsSnapshotPanel from './admin/OpsSnapshotPanel';
import ToxicSpikeAutopsyPanel from './admin/ToxicSpikeAutopsyPanel';
import EngineRegistryPanel from './admin/EngineRegistryPanel';
import WhatIfReplayPanel from './admin/WhatIfReplayPanel';
import NewsShockBurnIn from './admin/NewsShockBurnIn';
import TerminalTopActions from './admin/TerminalTopActions';
import AdminRoutesPanel from './admin/AdminRoutesPanel';
import ShellyDiagnosticTile from './admin/ShellyDiagnosticTile';
import FastVetoTile from './admin/FastVetoTile';
import logger from '../utils/logger';
import { getApiBase } from '../utils/apiBase';

const API = `${getApiBase()}/api`;

// Tabs grouped by domain — order matters: People → Operations → Insights.
// A leading `group` label is rendered as a tiny divider chip in the tab bar,
// so admins can scan 12 tabs without reading every word.
const TAB_GROUPS = [
  {
    label: 'People',
    tabs: [
      { id: 'users',    label: 'Users',    icon: Users },
      { id: 'waitlist', label: 'Waitlist', icon: Users },
    ],
  },
  {
    label: 'Operations',
    tabs: [
      { id: 'ops',       label: 'Health',    icon: HeartPulse },
      { id: 'terminal',  label: 'Terminal',  icon: TrendingUp },
      { id: 'burn-in',   label: 'Burn-In',   icon: Radio },
      { id: 'providers', label: 'Providers', icon: Activity },
      { id: 'vault',     label: 'Vault',     icon: Lock },
      { id: 'broker',    label: 'Broker',    icon: Key },
      { id: 'cache',     label: 'Cache',     icon: Database },
      { id: 'routes',    label: 'Routes',    icon: GitBranch },
      { id: 'media',     label: 'Media',     icon: Film },
      { id: 'security',  label: 'Security',  icon: ShieldCheck },
      { id: 'promos',    label: 'Promos',    icon: Gift },
    ],
  },
  {
    label: 'Insights',
    tabs: [
      { id: 'help-search',   label: 'Help Search', icon: Search },
      { id: 'chip-adoption', label: 'Chip CTR',    icon: MessageSquare },
      { id: 'conviction',    label: 'Conviction',  icon: TrendingUp },
      { id: 'crypto',        label: 'Crypto Bots', icon: Bitcoin },
      { id: 'adversarial',   label: 'Adversarial', icon: TrendingUp },
      { id: 'shadow',        label: 'Shadow',      icon: Eye },
      { id: 'autopsy',       label: 'Toxic Autopsy', icon: AlertTriangle },
      { id: 'engines',       label: 'AI Core', icon: Activity },
      { id: 'shelly',        label: 'Shelly',  icon: Brain },
      { id: 'whatif',        label: 'What-If Replay', icon: Activity },
      { id: 'patents',       label: 'Patent Watch', icon: FileText },
      { id: 'proof-chain',   label: 'Proof Chain', icon: ShieldCheck },
      { id: 'guard-shadow',  label: 'Guard Shadow', icon: Eye },
      { id: 'tools',         label: 'Tools',       icon: FileCode },
    ],
  },
];

// Flat id → tab lookup for the subtitle renderer.
const TAB_INDEX = TAB_GROUPS.flatMap((g) => g.tabs).reduce((m, t) => {
  m[t.id] = t;
  return m;
}, {});

// Tab → subtitle shown in the header when that tab is active. Replaces the
// old always-visible "N users total" which was stale on every non-Users tab.
const TAB_SUBTITLES = {
  users:          (ctx) => `${ctx.users.length} users total`,
  waitlist:       () => 'Invite queue + analytics',
  providers:      () => 'Market-data + email failover health',
  vault:          () => 'API keys & secrets',
  broker:         () => 'Alpaca / Kraken OAuth wiring',
  cache:          () => 'MongoDB cache tiers & TTLs',
  routes:         () => 'FastAPI route registry · duplicate detection · gate audit',
  media:          () => 'Uploaded assets & CDN state',
  security:       () => 'Auth attempts · password breaches · rate limits',
  promos:         () => 'Coupons & credit grants',
  ops:            () => 'Env flags · Mongo · scheduler · Tier 3 state · auto notes',
  terminal:       () => 'Sovereign-driven prioritized action queue · EXIT / ENTER / MANAGE / WATCH',
  'burn-in':      () => 'NEWS_SHOCK feeders · snapshots · Smart-Money blocks — Monday AM burn-in',
  'help-search':  () => 'Unanswered help-search queries',
  'chip-adoption':() => 'L1/L2 chat-chip click-through rates',
  conviction:     () => 'ML calibration · win-rate by conviction score',
  crypto:         () => 'Isolated 24/7 crypto paper bots · PnL · adaptations',
  adversarial:    () => 'Bull / Bear / Commander layer · win-rate spread · phase gates',
  shadow:         () => 'Research shadow — disagreement-conditional accuracy · cost budget',
  autopsy:        () => 'Toxic spike autopsy — WHY high-confidence predictions failed',
  engines:        () => 'AI Core engine registry — live + candidate side-by-side · bucket-lift comparison',
  shelly:         () => 'Shelly · Patent M Learning Core — situational memory · observation only (rollout step 1/5)',
  'fast-veto':    () => 'Tier 1 Fast Veto — sub-ms classical guardrail · veto-only · shadow mode',
  whatif:         () => 'What-If Replay — project each engine schema against the firewall outcome ledger',
  patents:        () => 'USPTO Patent Watch — daily fetch · per-query results',
  'proof-chain':  () => 'Patent J — immutable hash-linked decision audit trail',
  'guard-shadow': () => 'Decision Pipeline Guard — would-block vs executed (shadow rollout)',
  tools:          () => 'Codebase export & utilities',
};

// Map of tab id → renderer. Replaces the old nested ternary for clarity.
const TAB_COMPONENTS = {
  users:          (ctx) => <UsersTab users={ctx.users} filter={ctx.filter} setFilter={ctx.setFilter} actionLoading={ctx.actionLoading} doAction={ctx.doAction} />,
  ops:            () => <OpsSnapshotPanel />,
  terminal:       () => <TerminalTopActions />,
  'burn-in':      () => <NewsShockBurnIn />,
  providers:      () => <ProviderHealth />,
  vault:          () => <KeyVault />,
  promos:         () => <PromoManager />,
  broker:         () => <BrokerOAuthConfig />,
  cache:          () => <CacheMonitor />,
  routes:         () => <AdminRoutesPanel />,
  media:          () => <MediaManager />,
  security:       () => <SecurityAudit />,
  waitlist:       () => <WaitlistAdmin />,
  'help-search':  () => <HelpSearchInsights />,
  'chip-adoption':() => <ChipAdoptionInsights />,
  conviction:     () => <ConvictionCalibration />,
  crypto:         () => <CryptoPaperDashboard />,
  adversarial:    () => <CryptoAdversarialDashboard />,
  shadow:         () => <ShadowAccuracyPanel />,
  autopsy:        () => <ToxicSpikeAutopsyPanel />,
  engines:        () => <EngineRegistryPanel />,
  shelly:         () => <ShellyDiagnosticTile />,
  'fast-veto':    () => <FastVetoTile />,
  whatif:         () => <WhatIfReplayPanel />,
  patents:        () => <PatentWatchPanel />,
  'proof-chain':  () => <ProofChainExplorer />,
  'guard-shadow': () => <GuardShadowPanel />,
  tools:          () => <AdminTools />,
};

const AdminPanel = ({ onClose }) => {
  const [tab, setTab] = useState(() => {
    // One-shot deep-link: if a component (e.g., the toxic-spike
    // notification "View Autopsy" button) left a tab hint in
    // sessionStorage, honour it and clear so subsequent opens of
    // the admin panel start on Users as before.
    try {
      const hint = window.sessionStorage.getItem('risedual_admin_initial_tab');
      if (hint) {
        window.sessionStorage.removeItem('risedual_admin_initial_tab');
        return hint;
      }
    } catch (_) { /* noop */ }
    return 'users';
  });
  const [users, setUsers] = useState([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState('');
  const [actionLoading, setActionLoading] = useState(null);

  const fetchUsers = useCallback(async () => {
    setLoading(true);
    try {
      const res = await authFetch(`${API}/auth/admin/users`);
      if (res.ok) {
        const data = await res.json();
        setUsers(data.users);
      }
    } catch (e) {
      logger.error('Error fetching users:', e);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetchUsers(); }, [fetchUsers]);

  const doAction = async (userId, action) => {
    setActionLoading(`${userId}-${action}`);
    try {
      const res = await authFetch(`${API}/auth/admin/users/${userId}/${action}`, { method: 'POST' });
      if (res.ok) fetchUsers();
      else {
        const d = await res.json();
        toast.error(d.detail || 'Action failed');
      }
    } catch (e) {
      toast.error('Action failed');
    } finally {
      setActionLoading(null);
    }
  };

  const Renderer = TAB_COMPONENTS[tab] || TAB_COMPONENTS.users;
  const currentTab = TAB_INDEX[tab];
  const subtitle = (TAB_SUBTITLES[tab] || (() => ''))({ users });

  return (
    <div className="fixed inset-0 bg-black/70 backdrop-blur-sm z-50 flex items-start justify-center overflow-y-auto p-4" data-testid="admin-panel">
      <div className="bg-slate-900 rounded-2xl max-w-4xl w-full my-4 border border-slate-400/25">
        {/* Header — title reflects active tab, refresh only on Users */}
        <div className="p-4 sm:p-6 border-b border-slate-400/30 flex items-center justify-between">
          <div className="flex items-center gap-3 min-w-0">
            <div className="w-8 h-8 sm:w-10 sm:h-10 bg-red-600 rounded-xl flex items-center justify-center shrink-0">
              <Crown className="w-5 h-5 sm:w-6 sm:h-6 text-white" />
            </div>
            <div className="min-w-0">
              <h2 className="text-white text-lg sm:text-xl font-bold">
                Admin · {currentTab?.label || 'Panel'}
              </h2>
              {subtitle && <p className="text-slate-300 text-xs sm:text-sm truncate">{subtitle}</p>}
            </div>
          </div>
          <div className="flex items-center gap-2 shrink-0">
            <CouncilTierStatusPill
              apiBase={getApiBase()}
              onOpenShadowTab={() => setTab('shadow')}
            />
            {tab === 'users' && (
              <Button
                variant="outline"
                size="sm"
                onClick={fetchUsers}
                disabled={loading}
                className="bg-slate-800 border-slate-400/30 text-white"
                title="Refresh user list"
                data-testid="admin-refresh-users-btn"
              >
                <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
              </Button>
            )}
            <button
              onClick={onClose}
              className="text-slate-400 hover:text-white p-1.5 rounded-md hover:bg-slate-800 transition-colors"
              title="Close admin panel"
              data-testid="admin-close-btn"
            >
              <X className="w-5 h-5" />
            </button>
          </div>
        </div>

        {/* Tabs — grouped with tiny dividers so admins can scan them */}
        <div className="flex items-center border-b border-slate-400/25 px-2 sm:px-4 overflow-x-auto scrollbar-none">
          {TAB_GROUPS.map((group, gi) => (
            <React.Fragment key={group.label}>
              {gi > 0 && (
                <div
                  className="mx-2 h-4 w-px bg-slate-700 shrink-0"
                  aria-hidden="true"
                />
              )}
              <span className="text-[9px] font-bold uppercase tracking-wider text-slate-500 px-1.5 shrink-0 hidden sm:inline">
                {group.label}
              </span>
              {group.tabs.map((t) => (
                <button
                  key={t.id}
                  onClick={() => setTab(t.id)}
                  data-testid={`admin-tab-${t.id}`}
                  className={`flex items-center gap-1.5 px-3 py-3 text-xs sm:text-sm font-medium border-b-2 transition-all whitespace-nowrap shrink-0 ${
                    tab === t.id ? 'text-[#3DE8D9] border-[#3DE8D9]' : 'text-slate-400 border-transparent hover:text-slate-300'
                  }`}
                >
                  <t.icon className="w-3.5 h-3.5 sm:w-4 sm:h-4" />
                  {t.label}
                </button>
              ))}
            </React.Fragment>
          ))}
        </div>

        <Renderer
          users={users}
          filter={filter}
          setFilter={setFilter}
          actionLoading={actionLoading}
          doAction={doAction}
        />
      </div>
    </div>
  );
};

export default AdminPanel;
