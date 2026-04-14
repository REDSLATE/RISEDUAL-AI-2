import React from 'react';
import { BarChart3, Globe, TrendingUp, Search, Sparkles } from 'lucide-react';

const MobileBottomNav = ({ onOpenChat, activeView, onNavigate }) => {
  const items = [
    { icon: TrendingUp, label: 'Markets', key: 'dashboard' },
    { icon: BarChart3, label: 'Options', key: 'options' },
    { icon: Globe, label: 'Macro', key: 'research', subTab: 'macro' },
    { icon: Search, label: 'Research', key: 'research', subTab: 'company' },
    { icon: Sparkles, label: 'AI Chat', action: onOpenChat, accent: true },
  ];

  return (
    <nav className="lg:hidden fixed bottom-0 left-0 right-0 z-50 bg-[#060E1F]/95 backdrop-blur-xl border-t border-slate-400/25 safe-area-bottom" data-testid="mobile-bottom-nav">
      <div className="flex items-center justify-around px-1 py-1.5">
        {items.map((item) => {
          const Icon = item.icon;
          const isActive = !item.accent && activeView === item.key;
          return (
            <button
              key={item.label}
              onClick={item.action || (() => onNavigate(item.key, item.subTab))}
              className={`flex flex-col items-center gap-0.5 px-3 py-1.5 rounded-xl transition-colors ${
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
