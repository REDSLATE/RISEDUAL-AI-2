import React from 'react';
import { BarChart3, Globe, TrendingUp, Search, Sparkles, Briefcase, Swords } from 'lucide-react';

const MobileBottomNav = ({ onOpenChat, activeView, onNavigate, v2Nav = false }) => {
  const items = v2Nav
    ? [
        { icon: TrendingUp, label: 'Markets', key: 'dashboard' },
        { icon: Swords, label: 'War Room', key: 'warroom' },
        { icon: Search, label: 'Research', key: 'research' },
        { icon: Briefcase, label: 'Workspace', key: 'workspace' },
        { icon: Sparkles, label: 'AI Chat', action: onOpenChat, accent: true },
      ]
    : [
        { icon: TrendingUp, label: 'Markets', key: 'dashboard' },
        { icon: Search, label: 'Research', key: 'research' },
        { icon: BarChart3, label: 'Options', key: 'options' },
        { icon: Briefcase, label: 'Workspace', key: 'workspace' },
        { icon: Sparkles, label: 'AI Chat', action: onOpenChat, accent: true },
      ];

  return (
    <nav className="lg:hidden fixed bottom-0 left-0 right-0 z-[99999] bg-[#060E1F] border-t border-slate-400/25" data-testid="mobile-bottom-nav">
      <div className="flex items-center justify-around px-1 pt-1 pb-2">
        {items.map((item) => {
          const Icon = item.icon;
          const isActive = !item.accent && activeView === item.key;
          return (
            <button
              key={item.label}
              onClick={() => {
                if (item.action) {
                  item.action();
                } else {
                  console.log('[MobileNav] navigating to:', item.key); // eslint-disable-line no-console
                  onNavigate(item.key);
                }
              }}
              className={`flex flex-col items-center justify-center min-w-[56px] min-h-[44px] gap-0.5 px-2 py-1 rounded-xl transition-colors ${
                item.accent
                  ? 'bg-[#3DE8D9] text-white shadow-lg shadow-blue-500/30'
                  : isActive
                    ? 'text-[#3DE8D9] bg-[#3DE8D9]/10'
                    : 'text-slate-300 active:text-white active:bg-slate-700/40'
              }`}
              data-testid={`mobile-nav-${item.label.toLowerCase().replace(' ', '-')}`}
            >
              <Icon className="w-5 h-5" />
              <span className="text-[10px] font-medium">{item.label}</span>
            </button>
          );
        })}
      </div>
    </nav>
  );
};

export default MobileBottomNav;
