import React from 'react';

const patternData = [
  {
    id: 'head-shoulders',
    name: 'Head & Shoulders',
    type: 'Bearish Reversal',
    description: 'Three peaks where the middle peak (head) is highest. Signals trend reversal from bullish to bearish when neckline breaks.',
    color: '#ef4444',
    points: '20,70 40,40 55,60 75,20 95,60 110,40 130,70',
    neckline: { x1: 20, y1: 70, x2: 130, y2: 70 },
  },
  {
    id: 'double-top',
    name: 'Double Top',
    type: 'Bearish Reversal',
    description: 'Two peaks at roughly the same price level. Bearish signal when price breaks below the support between the peaks.',
    color: '#ef4444',
    points: '20,70 45,25 75,60 105,25 130,70',
    neckline: { x1: 20, y1: 60, x2: 130, y2: 60 },
  },
  {
    id: 'double-bottom',
    name: 'Double Bottom',
    type: 'Bullish Reversal',
    description: 'Two troughs at roughly the same price level. Bullish signal when price breaks above resistance between the troughs.',
    color: '#22c55e',
    points: '20,25 45,75 75,35 105,75 130,25',
    neckline: { x1: 20, y1: 35, x2: 130, y2: 35 },
  },
  {
    id: 'ascending-triangle',
    name: 'Ascending Triangle',
    type: 'Bullish Continuation',
    description: 'Flat resistance with rising support. Indicates buying pressure building. Bullish breakout expected.',
    color: '#22c55e',
    points: '20,75 40,30 55,65 75,30 90,55 110,30 130,15',
    neckline: { x1: 20, y1: 30, x2: 130, y2: 30 },
  },
  {
    id: 'descending-triangle',
    name: 'Descending Triangle',
    type: 'Bearish Continuation',
    description: 'Flat support with falling resistance. Selling pressure is building. Bearish breakdown expected.',
    color: '#ef4444',
    points: '20,20 40,70 55,30 75,70 90,40 110,70 130,80',
    neckline: { x1: 20, y1: 70, x2: 130, y2: 70 },
  },
  {
    id: 'cup-handle',
    name: 'Cup & Handle',
    type: 'Bullish Continuation',
    description: 'U-shaped cup followed by a small handle pullback. Strong bullish pattern with breakout above cup rim.',
    color: '#22c55e',
    points: '15,25 25,40 35,55 50,70 65,75 80,70 95,55 105,40 115,25 122,32 130,20',
    neckline: { x1: 15, y1: 25, x2: 130, y2: 25 },
  },
  {
    id: 'bull-flag',
    name: 'Bull Flag',
    type: 'Bullish Continuation',
    description: 'Sharp rise (flagpole) followed by a slight downward consolidation (flag). Expect continuation upward.',
    color: '#22c55e',
    points: '15,80 35,25 50,35 65,30 80,40 95,35 110,25 130,10',
    neckline: null,
  },
  {
    id: 'bear-flag',
    name: 'Bear Flag',
    type: 'Bearish Continuation',
    description: 'Sharp decline (flagpole) followed by slight upward consolidation (flag). Expect continuation downward.',
    color: '#ef4444',
    points: '15,15 35,70 50,60 65,65 80,55 95,60 110,70 130,85',
    neckline: null,
  },
];

const PatternSVG = ({ pattern }) => (
  <svg viewBox="0 0 150 90" className="w-full h-full">
    <rect x="0" y="0" width="150" height="90" fill="#1E293B" rx="4" />
    {/* Grid lines */}
    {[20, 40, 60, 80].map((y) => (
      <line key={y} x1="10" y1={y} x2="140" y2={y} stroke="#334155" strokeWidth="0.5" />
    ))}
    {/* Neckline */}
    {pattern.neckline && (
      <line
        x1={pattern.neckline.x1}
        y1={pattern.neckline.y1}
        x2={pattern.neckline.x2}
        y2={pattern.neckline.y2}
        stroke="#fbbf24"
        strokeWidth="1"
        strokeDasharray="4,3"
        opacity="0.6"
      />
    )}
    {/* Main pattern line */}
    <polyline
      points={pattern.points}
      fill="none"
      stroke={pattern.color}
      strokeWidth="2.5"
      strokeLinejoin="round"
      strokeLinecap="round"
    />
    {/* Glow effect */}
    <polyline
      points={pattern.points}
      fill="none"
      stroke={pattern.color}
      strokeWidth="4"
      strokeLinejoin="round"
      strokeLinecap="round"
      opacity="0.2"
    />
  </svg>
);

const PatternCard = ({ pattern, onClick }) => (
  <button
    onClick={() => onClick(pattern)}
    className="bg-[#1E293B] border border-slate-400/30 rounded-xl p-2 hover:border-[#3DE8D9] transition-all duration-200 hover:shadow-lg hover:shadow-blue-500/10 text-left group"
    data-testid={`pattern-card-${pattern.id}`}
  >
    <div className="aspect-[5/3] mb-2 rounded overflow-hidden">
      <PatternSVG pattern={pattern} />
    </div>
    <p className="text-xs font-semibold text-white truncate group-hover:text-blue-400 transition-colors">{pattern.name}</p>
    <p className={`text-[10px] font-medium ${pattern.color === '#22c55e' ? 'text-lime-400' : 'text-orange-400'}`}>
      {pattern.type}
    </p>
  </button>
);

const ChartPatternLibrary = ({ onSelectPattern }) => (
  <div className="bg-[#060E1F] rounded-xl p-3 border border-slate-400/30" data-testid="pattern-library">
    <div className="flex items-center gap-2 mb-3">
      <div className="w-2 h-2 rounded-full bg-blue-500 animate-pulse" />
      <p className="text-xs font-bold text-blue-400 uppercase tracking-wider">Chart Pattern Library</p>
    </div>
    <p className="text-[11px] text-slate-400 mb-3">Tap any pattern to get AI analysis and trading strategies.</p>
    <div className="grid grid-cols-2 gap-2">
      {patternData.map((pattern) => (
        <PatternCard key={pattern.id} pattern={pattern} onClick={onSelectPattern} />
      ))}
    </div>
  </div>
);

export { patternData };
export default ChartPatternLibrary;
