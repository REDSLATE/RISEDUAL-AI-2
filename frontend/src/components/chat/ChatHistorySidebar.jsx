import React from 'react';
import { X } from 'lucide-react';

const ChatHistorySidebar = ({ history, onSelect, onClose }) => (
  <div className="border-b border-slate-400/25 bg-slate-800/50 px-2.5 py-2 max-h-[200px] overflow-y-auto" data-testid="chat-history-sidebar">
    <div className="flex items-center justify-between mb-1.5">
      <span className="text-slate-400 text-[11px] font-medium">Chat History</span>
      <button onClick={onClose} className="text-slate-400 hover:text-white"><X className="w-3 h-3" /></button>
    </div>
    {(!history || history.length === 0) ? (
      <p className="text-slate-400 text-[11px]">No previous chats</p>
    ) : (
      <div className="space-y-0.5">
        {history.map((s) => (
          <button
            key={s.session_id}
            onClick={() => onSelect(s.session_id)}
            className="w-full text-left px-2.5 py-1.5 rounded-lg hover:bg-slate-600/30 transition-colors group"
            data-testid={`session-${s.session_id}`}
          >
            <div className="text-white text-[11px] font-medium truncate">{s.preview || 'Chat Session'}</div>
            <div className="text-slate-400 text-[9px]">{s.message_count || 0} messages</div>
          </button>
        ))}
      </div>
    )}
  </div>
);

export default ChatHistorySidebar;
