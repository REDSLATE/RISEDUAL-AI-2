import React, { useState } from 'react';
import { Radar, Activity, EyeOff, BookOpen } from 'lucide-react';
import OptionsRadar from '../OptionsRadar';
import OptionsFlowScreener from '../OptionsFlowScreener';
import DarkPoolData from '../DarkPoolData';
import OptionsLearn from '../OptionsLearn';
import useV2Nav from '../../hooks/useV2Nav';
import IconTabBar from './IconTabBar';

const TABS = [
  { key: 'radar',    label: 'Options Radar', icon: Radar,    desc: 'Unusual options activity & AI-scored opportunities' },
  { key: 'flow',     label: 'Options Flow',  icon: Activity, desc: 'Real-time options flow screener & premium tracker' },
  { key: 'darkpool', label: 'Dark Pool',     icon: EyeOff,   desc: 'Institutional dark-pool prints & volume clusters' },
  { key: 'learn',    label: 'Learn',         icon: BookOpen, desc: 'Plain-language options glossary — terms, Greeks & strategies' },
];

export default function OptionsHub({ onSubscribe, initialTab }) {
  const { enabled: v2Nav } = useV2Nav();
  const [tab, setTab] = useState(initialTab || 'radar');

  return (
    <div data-testid="options-hub">
      <IconTabBar
        tabs={TABS} value={tab} onChange={setTab}
        enabled={v2Nav}
        accent="text-violet-300"
        accentHex="#c4b5fd"
        testIdPrefix="options-tab"
        legendTitle="Options Legend"
      />

      <div className="animate-enter">
        {tab === 'radar' && <OptionsRadar />}
        {tab === 'flow' && <OptionsFlowScreener />}
        {tab === 'darkpool' && <DarkPoolData onSubscribe={onSubscribe} />}
        {tab === 'learn' && <OptionsLearn />}
      </div>
    </div>
  );
}
