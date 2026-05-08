import React, { useState, useEffect, useCallback } from 'react';
import { Plus, Minus, Play, Save, Trash2, RefreshCw, ChevronDown, Filter } from 'lucide-react';
import { Button } from '../ui/button';
import { Input } from '../ui/input';
import { Badge } from '../ui/badge';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '../ui/select';
import { toast } from '../ui/sonner';
import { authFetch } from '../../contexts/AuthContext';
import { getApiBase } from '../../utils/apiBase';

const API = `${getApiBase()}/api/scanner`;

const ConditionRow = ({ condition, indicators, operators, onChange, onRemove }) => {
  const indInfo = indicators.find(i => i.id === condition.indicator);
  const indType = indInfo?.type || 'number';
  const ops = operators[indType] || [];

  return (
    <div className="flex items-center gap-2 bg-slate-900/60 rounded-lg p-2 border border-slate-700/40" data-testid="rule-condition-row">
      <Select value={condition.indicator} onValueChange={v => onChange({ ...condition, indicator: v })}>
        <SelectTrigger className="bg-slate-800 border-slate-600 text-white text-[11px] h-8 w-40"><SelectValue placeholder="Indicator" /></SelectTrigger>
        <SelectContent className="max-h-[250px]">
          {indicators.map(i => (
            <SelectItem key={i.id} value={i.id} className="text-xs">{i.name}</SelectItem>
          ))}
        </SelectContent>
      </Select>

      <Select value={condition.operator} onValueChange={v => onChange({ ...condition, operator: v })}>
        <SelectTrigger className="bg-slate-800 border-slate-600 text-white text-[11px] h-8 w-28"><SelectValue placeholder="Op" /></SelectTrigger>
        <SelectContent>
          {ops.map(o => (
            <SelectItem key={o.id} value={o.id} className="text-xs">{o.label}</SelectItem>
          ))}
        </SelectContent>
      </Select>

      {indType === 'category' ? (
        <Select value={String(condition.value)} onValueChange={v => onChange({ ...condition, value: v })}>
          <SelectTrigger className="bg-slate-800 border-slate-600 text-white text-[11px] h-8 w-32"><SelectValue placeholder="Value" /></SelectTrigger>
          <SelectContent>
            {(indInfo?.values || []).map(v => (
              <SelectItem key={v} value={v} className="text-xs">{v}</SelectItem>
            ))}
          </SelectContent>
        </Select>
      ) : (
        <Input type="number" value={condition.value} onChange={e => onChange({ ...condition, value: e.target.value })}
          placeholder="Value" className="bg-slate-800 border-slate-600 text-white text-[11px] h-8 w-24" data-testid="rule-condition-value" />
      )}

      <button onClick={onRemove} className="text-slate-500 hover:text-red-400 shrink-0"><Minus className="w-3.5 h-3.5" /></button>
    </div>
  );
};

// Nested sub-group component (non-recursive, max depth 1)
const SubGroup = ({ group, indicators, operators, onChange }) => {
  const updateCondition = (idx, cond) => {
    const newConds = [...(group.conditions || [])];
    newConds[idx] = cond;
    onChange({ ...group, conditions: newConds });
  };

  const removeCondition = (idx) => {
    const newConds = (group.conditions || []).filter((_, i) => i !== idx);
    onChange({ ...group, conditions: newConds });
  };

  const addCondition = () => {
    onChange({
      ...group,
      conditions: [...(group.conditions || []), { indicator: 'rsi', operator: 'lt', value: '30' }],
    });
  };

  return (
    <div className="rounded-xl p-3 space-y-2 bg-slate-800/50 border border-slate-500/20 ml-4" data-testid="rule-group-1">
      <div className="flex items-center gap-2 mb-1">
        <div className="flex items-center gap-1 bg-slate-900/60 rounded-lg p-0.5">
          <button onClick={() => onChange({ ...group, logic: 'AND' })}
            className={`px-2 py-0.5 rounded text-[10px] font-bold ${group.logic === 'AND' ? 'bg-[#3DE8D9] text-white' : 'text-slate-400'}`}
            data-testid="rule-logic-and">AND</button>
          <button onClick={() => onChange({ ...group, logic: 'OR' })}
            className={`px-2 py-0.5 rounded text-[10px] font-bold ${group.logic === 'OR' ? 'bg-violet-500 text-white' : 'text-slate-400'}`}
            data-testid="rule-logic-or">OR</button>
        </div>
        <span className="text-slate-500 text-[9px]">{group.logic === 'AND' ? 'All conditions must match' : 'Any condition can match'}</span>
      </div>

      {(group.conditions || []).map((cond, i) => (
        <ConditionRow key={`subcond-${i}`} condition={cond} indicators={indicators} operators={operators}
          onChange={c => updateCondition(i, c)} onRemove={() => removeCondition(i)} />
      ))}

      <div className="flex items-center gap-2 pt-1">
        <button onClick={addCondition} className="flex items-center gap-1 text-[#3DE8D9] text-[10px] hover:text-white">
          <Plus className="w-3 h-3" /> Add Condition
        </button>
      </div>
    </div>
  );
};

// Main rule group component (depth 0 only, uses SubGroup for nested)
const RuleGroup = ({ group, indicators, operators, onChange }) => {
  const updateCondition = (idx, cond) => {
    const newConds = [...(group.conditions || [])];
    newConds[idx] = cond;
    onChange({ ...group, conditions: newConds });
  };

  const removeCondition = (idx) => {
    const newConds = (group.conditions || []).filter((_, i) => i !== idx);
    onChange({ ...group, conditions: newConds });
  };

  const addCondition = () => {
    onChange({
      ...group,
      conditions: [...(group.conditions || []), { indicator: 'rsi', operator: 'lt', value: '30' }],
    });
  };

  const addSubGroup = () => {
    onChange({
      ...group,
      groups: [...(group.groups || []), { logic: 'OR', conditions: [{ indicator: 'rsi', operator: 'gt', value: '70' }], groups: [] }],
    });
  };

  const updateSubGroup = (idx, sub) => {
    const newGroups = [...(group.groups || [])];
    newGroups[idx] = sub;
    onChange({ ...group, groups: newGroups });
  };

  const removeSubGroup = (idx) => {
    onChange({ ...group, groups: (group.groups || []).filter((_, i) => i !== idx) });
  };

  return (
    <div className="rounded-xl p-3 space-y-2 bg-slate-800/30 border border-slate-600/20" data-testid="rule-group-0">
      <div className="flex items-center gap-2 mb-1">
        <div className="flex items-center gap-1 bg-slate-900/60 rounded-lg p-0.5">
          <button onClick={() => onChange({ ...group, logic: 'AND' })}
            className={`px-2 py-0.5 rounded text-[10px] font-bold ${group.logic === 'AND' ? 'bg-[#3DE8D9] text-white' : 'text-slate-400'}`}
            data-testid="rule-logic-and">AND</button>
          <button onClick={() => onChange({ ...group, logic: 'OR' })}
            className={`px-2 py-0.5 rounded text-[10px] font-bold ${group.logic === 'OR' ? 'bg-violet-500 text-white' : 'text-slate-400'}`}
            data-testid="rule-logic-or">OR</button>
        </div>
        <span className="text-slate-500 text-[9px]">{group.logic === 'AND' ? 'All conditions must match' : 'Any condition can match'}</span>
      </div>

      {(group.conditions || []).map((cond, i) => (
        <ConditionRow key={`cond-0-${i}`} condition={cond} indicators={indicators} operators={operators}
          onChange={c => updateCondition(i, c)} onRemove={() => removeCondition(i)} />
      ))}

      {(group.groups || []).map((sub, i) => (
        <div key={`sub-0-${i}`} className="relative">
          <button onClick={() => removeSubGroup(i)} className="absolute -top-1 -right-1 z-10 text-slate-500 hover:text-red-400 bg-slate-900 rounded-full p-0.5">
            <Minus className="w-3 h-3" />
          </button>
          <SubGroup group={sub} indicators={indicators} operators={operators} onChange={g => updateSubGroup(i, g)} />
        </div>
      ))}

      <div className="flex items-center gap-2 pt-1">
        <button onClick={addCondition} className="flex items-center gap-1 text-[#3DE8D9] text-[10px] hover:text-white">
          <Plus className="w-3 h-3" /> Add Condition
        </button>
        <button onClick={addSubGroup} className="flex items-center gap-1 text-violet-400 text-[10px] hover:text-white">
          <Filter className="w-3 h-3" /> Add Group
        </button>
      </div>
    </div>
  );
};

const RuleBuilder = ({ onScanResults }) => {
  const [indicators, setIndicators] = useState([]);
  const [operators, setOperators] = useState({});
  const [rule, setRule] = useState({ logic: 'AND', conditions: [{ indicator: 'rsi', operator: 'lt', value: '30' }], groups: [] });
  const [ruleName, setRuleName] = useState('');
  const [savedRules, setSavedRules] = useState([]);
  const [scanning, setScanning] = useState(false);
  const [showSaved, setShowSaved] = useState(false);

  const loadMeta = useCallback(async () => {
    try {
      const res = await authFetch(`${API}/indicators`);
      if (res.ok) {
        const data = await res.json();
        setIndicators(data.indicators || []);
        setOperators(data.operators || {});
      }
    } catch (e) { console.warn('Failed to load scanner metadata:', e); }
  }, []);

  const loadSavedRules = useCallback(async () => {
    try {
      const res = await authFetch(`${API}/custom/rules`);
      if (res.ok) setSavedRules(await res.json());
    } catch (e) { console.warn('Failed to load saved rules:', e); }
  }, []);

  useEffect(() => { loadMeta(); loadSavedRules(); }, [loadMeta, loadSavedRules]);

  const runScan = async () => {
    if (!rule.conditions?.length) return toast.error('Add at least one condition');
    setScanning(true);
    try {
      const res = await authFetch(`${API}/custom/run`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ rule }),
      });
      if (res.ok) {
        const data = await res.json();
        toast.success(`Scanned ${data.scanned} symbols — ${data.match_count} matches`);
        if (onScanResults) onScanResults(data);
      } else {
        const err = await res.json().catch(() => ({}));
        toast.error(err.detail || 'Scan failed');
      }
    } catch { toast.error('Scan error'); }
    finally { setScanning(false); }
  };

  const saveRule = async () => {
    if (!ruleName.trim()) return toast.error('Give your rule a name');
    try {
      const res = await authFetch(`${API}/custom/save`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: ruleName, rule }),
      });
      if (res.ok) { toast.success('Rule saved'); loadSavedRules(); setRuleName(''); }
      else toast.error('Save failed');
    } catch { toast.error('Save error'); }
  };

  const deleteRule = async (ruleId) => {
    try {
      const res = await authFetch(`${API}/custom/rules/${ruleId}`, { method: 'DELETE' });
      if (res.ok) { toast.success('Rule deleted'); loadSavedRules(); }
    } catch (e) { console.warn('Delete rule error:', e); }
  };

  const loadRule = (saved) => {
    setRule(saved.rule || { logic: 'AND', conditions: [], groups: [] });
    setRuleName(saved.name || '');
    setShowSaved(false);
    toast.success(`Loaded: ${saved.name}`);
  };

  return (
    <div className="space-y-3" data-testid="rule-builder">
      {/* Saved Rules Toggle */}
      <div className="flex items-center justify-between">
        <span className="text-white text-xs font-semibold">Custom Rule Builder</span>
        <button onClick={() => setShowSaved(!showSaved)} className="flex items-center gap-1 text-violet-400 text-[10px] hover:text-white">
          <ChevronDown className={`w-3 h-3 transition-transform ${showSaved ? 'rotate-180' : ''}`} />
          Saved Rules ({savedRules.length})
        </button>
      </div>

      {showSaved && savedRules.length > 0 && (
        <div className="space-y-1.5 bg-slate-800/40 rounded-lg p-2">
          {savedRules.map((sr, i) => (
            <div key={sr.rule_id || `sr-${i}`} className="flex items-center justify-between py-1.5 px-2 rounded hover:bg-slate-700/40">
              <button onClick={() => loadRule(sr)} className="text-white text-xs hover:text-[#3DE8D9]">{sr.name}</button>
              <button onClick={() => deleteRule(sr.rule_id)} className="text-slate-500 hover:text-red-400"><Trash2 className="w-3 h-3" /></button>
            </div>
          ))}
        </div>
      )}

      {/* Rule Editor */}
      <RuleGroup group={rule} indicators={indicators} operators={operators} onChange={setRule} />

      {/* Actions */}
      <div className="flex items-center gap-2">
        <Button onClick={runScan} disabled={scanning} className="flex-1 bg-[#3DE8D9] text-white hover:bg-[#3DE8D9]/80 h-9 text-xs font-bold" data-testid="rule-builder-scan">
          {scanning ? <RefreshCw className="w-3.5 h-3.5 mr-1.5 animate-spin" /> : <Play className="w-3.5 h-3.5 mr-1.5" />}
          {scanning ? 'Scanning...' : 'Run Scan'}
        </Button>
        <div className="flex items-center gap-1">
          <Input value={ruleName} onChange={e => setRuleName(e.target.value)} placeholder="Rule name..."
            className="bg-slate-800 border-slate-600 text-white text-[11px] h-9 w-32" data-testid="rule-builder-name" />
          <Button onClick={saveRule} variant="outline" className="bg-slate-800 border-slate-600 text-slate-300 h-9 text-xs" data-testid="rule-builder-save">
            <Save className="w-3.5 h-3.5" />
          </Button>
        </div>
      </div>
    </div>
  );
};

export default RuleBuilder;
