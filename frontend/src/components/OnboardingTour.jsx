import React, { useState, useEffect, useCallback, useRef } from 'react';
import { ChevronRight, ChevronLeft, X, Rocket, Bot, Brain, BarChart3, TrendingUp, Shield, Search, Layers, Calculator, Zap } from 'lucide-react';
import { Button } from './ui/button';

const TOUR_STEPS = [
  {
    target: null,
    title: "Welcome to RISEDUAL AI",
    content: "Your AI-powered trading command center. Let's take a quick tour of the platform — it'll only take a minute.",
    icon: Rocket,
    position: 'center',
  },
  {
    target: '[data-testid="watchlist-section"]',
    fallback: '#stock-ticker',
    title: "Live Market Data",
    content: "Real-time stock and crypto tickers scroll across the top. Below, your personal Watchlist tracks your favorite symbols with AI-powered insights.",
    icon: TrendingUp,
  },
  {
    target: '#ai-war-room',
    title: "AI War Room",
    content: "Your adversarial AI command center. Multiple AI models analyze the same question and reach consensus — like having a team of analysts on demand.",
    icon: Brain,
  },
  {
    target: '#ai-hypothesis',
    title: "AI Investment Hypothesis",
    content: "Enter any ticker and get an AI-generated bull/bear case with confidence scores. Consensus mode runs 3 models for higher accuracy.",
    icon: Brain,
  },
  {
    target: '#sector-heatmap',
    title: "Sector Heatmap",
    content: "Visual overview of every market sector at a glance. Green = gaining, Red = losing. Great for spotting sector rotation.",
    icon: BarChart3,
  },
  {
    target: '#whale-radar',
    title: "Whale Radar",
    content: "Real-time monitoring of large transactions. Tracks institutional moves and whale activity so you can follow the smart money.",
    icon: Shield,
  },
  {
    target: '[data-testid="bots-dashboard"]',
    fallback: '#trading-bots',
    title: "Trading Bots",
    content: "Create automated trading bots — Grid Bot for ranges, Signal Bot for AI-triggered trades, Webhook Bot for TradingView alerts. All start OFF — toggle ON when ready.",
    icon: Bot,
  },
  {
    target: '[data-testid="nav-bots-shortcut"]',
    title: "Quick Access: Bots",
    content: "This amber button gives you instant access to the Trading Bots panel. Create, toggle, and manage all your bots from here.",
    icon: Bot,
    position: 'below',
  },
  {
    target: '[data-testid="nav-help-btn"]',
    title: "Help Center",
    content: "Click the ? icon anytime for the full user manual. 8 sections covering every feature with step-by-step explanations.",
    icon: Search,
    position: 'below',
  },
  {
    target: null,
    title: "You're All Set!",
    content: "Explore Smart Orders, Risk Calculator, and Market Scanner from the user menu (top-right avatar). Happy trading!",
    icon: Rocket,
    position: 'center',
    final: true,
  },
];

const STORAGE_KEY = 'risedual_tour_completed';

const OnboardingTour = ({ active, onComplete }) => {
  const [step, setStep] = useState(0);
  const [highlight, setHighlight] = useState(null);
  const overlayRef = useRef(null);

  const currentStep = TOUR_STEPS[step];
  const isFirst = step === 0;
  const isLast = step === TOUR_STEPS.length - 1;

  const scrollToTarget = useCallback(() => {
    if (!currentStep) return;
    const target = currentStep.target;
    if (!target || currentStep.position === 'center') {
      setHighlight(null);
      return;
    }

    let el = document.querySelector(target);
    if (!el && currentStep.fallback) {
      el = document.querySelector(currentStep.fallback);
    }

    if (el) {
      el.scrollIntoView({ behavior: 'smooth', block: 'center' });
      setTimeout(() => {
        const rect = el.getBoundingClientRect();
        setHighlight({
          top: rect.top + window.scrollY,
          left: rect.left,
          width: rect.width,
          height: rect.height,
          screenTop: rect.top,
        });
      }, 400);
    } else {
      setHighlight(null);
    }
  }, [currentStep]);

  useEffect(() => {
    if (active) scrollToTarget();
  }, [step, active, scrollToTarget]);

  const next = () => {
    if (isLast) {
      localStorage.setItem(STORAGE_KEY, 'true');
      onComplete();
    } else {
      setStep(s => s + 1);
    }
  };

  const prev = () => { if (!isFirst) setStep(s => s - 1); };

  const skip = () => {
    localStorage.setItem(STORAGE_KEY, 'true');
    onComplete();
  };

  if (!active || !currentStep) return null;

  const Icon = currentStep.icon || Rocket;
  const isCentered = currentStep.position === 'center' || !highlight;

  // Calculate tooltip position
  let tooltipStyle = {};
  if (isCentered) {
    tooltipStyle = { top: '50%', left: '50%', transform: 'translate(-50%, -50%)', position: 'fixed' };
  } else if (currentStep.position === 'below') {
    tooltipStyle = {
      position: 'fixed',
      top: `${Math.min(highlight.screenTop + highlight.height + 12, window.innerHeight - 280)}px`,
      left: `${Math.max(Math.min(highlight.left + highlight.width / 2 - 175, window.innerWidth - 370), 10)}px`,
    };
  } else {
    const belowSpace = window.innerHeight - highlight.screenTop - highlight.height;
    const aboveSpace = highlight.screenTop;
    if (belowSpace > 280) {
      tooltipStyle = {
        position: 'fixed',
        top: `${highlight.screenTop + highlight.height + 12}px`,
        left: `${Math.max(Math.min(highlight.left + highlight.width / 2 - 175, window.innerWidth - 370), 10)}px`,
      };
    } else if (aboveSpace > 280) {
      tooltipStyle = {
        position: 'fixed',
        top: `${highlight.screenTop - 260}px`,
        left: `${Math.max(Math.min(highlight.left + highlight.width / 2 - 175, window.innerWidth - 370), 10)}px`,
      };
    } else {
      tooltipStyle = { top: '50%', left: '50%', transform: 'translate(-50%, -50%)', position: 'fixed' };
    }
  }

  return (
    <div ref={overlayRef} className="fixed inset-0 z-[200]" data-testid="onboarding-tour">
      {/* Dark overlay with cutout */}
      <div className="fixed inset-0 bg-black/70 transition-opacity duration-300" onClick={skip} />

      {/* Highlight ring */}
      {highlight && !isCentered && (
        <div
          className="fixed border-2 border-[#3DE8D9] rounded-xl pointer-events-none z-[201] transition-all duration-500"
          style={{
            top: `${highlight.screenTop - 6}px`,
            left: `${highlight.left - 6}px`,
            width: `${highlight.width + 12}px`,
            height: `${highlight.height + 12}px`,
            boxShadow: '0 0 0 9999px rgba(0,0,0,0.6), 0 0 20px rgba(61,232,217,0.3)',
          }}
        />
      )}

      {/* Tooltip card */}
      <div className="z-[202] w-[350px] bg-[#0B1426] border border-[#3DE8D9]/40 rounded-2xl shadow-2xl shadow-[#3DE8D9]/10 overflow-hidden" style={tooltipStyle} data-testid="tour-tooltip">
        {/* Progress bar */}
        <div className="h-1 bg-slate-800">
          <div className="h-full bg-[#3DE8D9] transition-all duration-500" style={{ width: `${((step + 1) / TOUR_STEPS.length) * 100}%` }} />
        </div>

        <div className="p-5">
          {/* Icon + Title */}
          <div className="flex items-center gap-2.5 mb-3">
            <div className="w-9 h-9 rounded-xl bg-[#3DE8D9]/10 flex items-center justify-center">
              <Icon className="w-5 h-5 text-[#3DE8D9]" />
            </div>
            <div>
              <h3 className="text-white font-bold text-sm">{currentStep.title}</h3>
              <span className="text-slate-500 text-[10px]">Step {step + 1} of {TOUR_STEPS.length}</span>
            </div>
          </div>

          {/* Content */}
          <p className="text-slate-300 text-xs leading-relaxed mb-4">{currentStep.content}</p>

          {/* Actions */}
          <div className="flex items-center justify-between">
            <button onClick={skip} className="text-slate-500 text-[10px] hover:text-slate-300 transition-colors" data-testid="tour-skip">
              Skip Tour
            </button>
            <div className="flex items-center gap-2">
              {!isFirst && (
                <Button size="sm" variant="outline" onClick={prev}
                  className="bg-slate-800 border-slate-600 text-slate-300 h-8 text-xs" data-testid="tour-prev">
                  <ChevronLeft className="w-3.5 h-3.5 mr-0.5" /> Back
                </Button>
              )}
              <Button size="sm" onClick={next}
                className={`h-8 text-xs font-bold ${currentStep.final ? 'bg-lime-500 hover:bg-lime-400 text-white' : 'bg-[#3DE8D9] hover:bg-[#3DE8D9]/80 text-white'}`}
                data-testid="tour-next">
                {currentStep.final ? "Let's Go!" : 'Next'} {!currentStep.final && <ChevronRight className="w-3.5 h-3.5 ml-0.5" />}
              </Button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};

export default OnboardingTour;
export { STORAGE_KEY };
