import React, { useState, useEffect } from 'react';
import { ArrowUp } from 'lucide-react';

const ScrollToTop = () => {
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    const onScroll = () => setVisible(window.scrollY > 600);
    window.addEventListener('scroll', onScroll, { passive: true });
    return () => window.removeEventListener('scroll', onScroll);
  }, []);

  if (!visible) return null;

  return (
    <button
      onClick={() => window.scrollTo({ top: 0, behavior: 'smooth' })}
      className="fixed bottom-20 lg:bottom-6 left-4 z-40 w-10 h-10 bg-slate-800/90 backdrop-blur-sm border border-slate-700/50 rounded-xl flex items-center justify-center text-slate-300 hover:text-white hover:bg-slate-700 scroll-fab shadow-lg"
      data-testid="scroll-to-top-btn"
      aria-label="Scroll to top"
    >
      <ArrowUp className="w-4 h-4" />
    </button>
  );
};

export default ScrollToTop;
