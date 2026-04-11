import React from 'react';
import { Lock } from 'lucide-react';
import { Button } from './ui/button';
import { useAuth } from '../contexts/AuthContext';

const ProBlurWall = ({ children, freeRowCount = 3, onSubscribe, label = "Full Data" }) => {
  const { isPro } = useAuth();

  if (isPro) return children;

  return (
    <div className="relative" data-testid="pro-blur-wall">
      {children}
      <div className="absolute inset-0 flex flex-col items-center justify-end pb-6" style={{ top: `${freeRowCount * 48 + 60}px` }}>
        <div className="absolute inset-0 backdrop-blur-md bg-slate-900/60" />
        <div className="relative z-10 text-center px-4">
          <div className="w-12 h-12 bg-slate-800 rounded-2xl flex items-center justify-center mx-auto mb-3 border border-slate-600/50">
            <Lock className="w-6 h-6 text-slate-400" />
          </div>
          <p className="text-white font-semibold text-sm mb-1">Upgrade to See {label}</p>
          <p className="text-slate-400 text-xs mb-3">First {freeRowCount} rows are free. Get full access with Pro.</p>
          <Button className="bg-[#35D6C8] hover:bg-[#67E3D3] text-white rounded-xl text-sm" onClick={onSubscribe} data-testid="blur-wall-upgrade">
            Upgrade to Pro
          </Button>
        </div>
      </div>
    </div>
  );
};

export default ProBlurWall;
