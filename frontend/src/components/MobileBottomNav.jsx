import React from 'react';
import { BarChart3, Globe, TrendingUp, Search, Sparkles } from 'lucide-react';

const MobileBottomNav = ({ onOpenChat }) => {
  const scrollTo = (id) => {
    document.getElementById(id)?.scrollIntoView({ behavior: 'smooth' });
  };

  const items = [
    { icon: TrendingUp, label: 'Markets', action: () => scrollTo('stock-ticker') },
    { icon: BarChart3, label: 'Options', action: () => scrollTo('options-radar') },
    { icon: Globe, label: 'Macro', action: () => scrollTo('macro-dashboard') },
    { icon: Search, label: 'Research', action: () => scrollTo('company-research') },
    { icon: Sparkles, label: 'AI Chat', action: onOpenChat, accent: true },
  ];

  return (
    <nav className="lg:hidden fixed bottom-0 left-0 right-0 z-50 bg-[#0A2A63]/95 backdrop-blur-xl border-t border-slate-700/50 safe-area-bottom" data-testid="mobile-bottom-nav">
      <div className="flex items-center justify-around px-1 py-1.5">
        {items.map((item, i) => {
          const Icon = item.icon;
          return (
            <button
              key={item.label}
              onClick={item.action}
              className={`flex flex-col items-center gap-0.5 px-3 py-1.5 rounded-xl transition-colors ${
                item.accent
                  ? 'bg-[#35D6C8] text-white shadow-lg shadow-blue-500/30'
                  : 'text-slate-400 active:text-white active:bg-slate-800'
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
