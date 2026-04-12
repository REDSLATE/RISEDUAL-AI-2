import React from 'react';
import { Brain, X, Pin, Trash2 } from 'lucide-react';

const MemoryPanel = ({ memories, memoryEnabled, onToggle, onDelete, onClearAll, onClose }) => {
  const pinnedCount = memories.filter(m => m.category === 'pinned').length;

  return (
    <div className="border-b border-slate-400/25 max-h-[280px] overflow-y-auto flex-shrink-0 p-3 space-y-2" data-testid="memory-panel">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Brain className="w-4 h-4 text-[#3DE8D9]" />
          <span className="text-white text-xs font-semibold">Chat Memory</span>
          <span className="text-slate-500 text-[10px]">{memories.length} saved</span>
          {pinnedCount > 0 && (
            <span className="text-[#3DE8D9] text-[10px]">{pinnedCount}/5 pinned</span>
          )}
        </div>
        <div className="flex items-center gap-1">
          <button
            onClick={onToggle}
            className={`text-[10px] px-2 py-0.5 rounded-full border transition-colors ${
              memoryEnabled
                ? 'bg-[#3DE8D9]/10 text-[#3DE8D9] border-[#3DE8D9]/30'
                : 'bg-slate-800 text-slate-500 border-slate-600'
            }`}
            data-testid="memory-enable-toggle"
          >
            {memoryEnabled ? 'ON' : 'OFF'}
          </button>
          {memories.length > 0 && (
            <button onClick={onClearAll} className="text-slate-500 hover:text-orange-400 text-[10px] px-1" title="Clear all"
              data-testid="clear-all-memories">
              Clear all
            </button>
          )}
          <button onClick={onClose} className="text-slate-400 hover:text-white">
            <X className="w-3.5 h-3.5" />
          </button>
        </div>
      </div>
      {!memoryEnabled && (
        <p className="text-slate-500 text-[10px] bg-slate-800/60 rounded-lg p-2">
          Memory is off. The AI won't remember past conversations. Turn it on to enable persistent context.
        </p>
      )}
      {memories.length === 0 ? (
        <p className="text-slate-500 text-[10px] text-center py-3">
          {memoryEnabled ? 'No memories yet. Chat with the AI and it will remember key details.' : 'Memory is disabled.'}
        </p>
      ) : (
        <div className="space-y-1">
          {memories.map(m => (
            <div key={m.memory_id} className="flex items-start gap-2 bg-slate-800/40 rounded-lg px-2.5 py-1.5 group">
              {m.category === 'pinned' && <Pin className="w-3 h-3 text-[#3DE8D9] shrink-0 mt-0.5" />}
              <p className="flex-1 text-slate-300 text-[11px] leading-snug">{m.content}</p>
              <button
                onClick={() => onDelete(m.memory_id)}
                className="opacity-0 group-hover:opacity-100 text-slate-500 hover:text-orange-400 shrink-0 mt-0.5 transition-opacity"
                data-testid={`delete-memory-${m.memory_id}`}
              >
                <Trash2 className="w-3 h-3" />
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
};

export default MemoryPanel;
