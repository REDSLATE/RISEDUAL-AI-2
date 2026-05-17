/**
 * Slash-command parser & dispatcher for the MC-aware chat.
 *
 * Recognized verbs:
 *   /mc help
 *   /mc status
 *   /mc mirror [hours]
 *   /mc intents [limit]
 *   /mc opine <ticker>
 *
 * Everything else falls through to the standard LLM chat path.
 *
 * Doctrine reminder: these are read-only commands. The chat can ask
 * and display, but it never executes — execution stays on MC.
 */
import { authFetch } from '../contexts/AuthContext';
import { getApiBase } from '../utils/apiBase';

const API = `${getApiBase()}/api`;

/** Heuristic: is this user message a `/mc …` slash command? */
export function isMCCommand(text) {
  if (!text) return false;
  return /^\s*\/mc(\s|$)/i.test(text);
}

/**
 * Parse a slash command line. Returns `{ command, args }` or null
 * when the line doesn't start with `/mc`.
 */
export function parseMCCommand(text) {
  if (!isMCCommand(text)) return null;
  const trimmed = text.trim();
  // Strip leading "/mc" then split remainder on whitespace.
  const rest = trimmed.replace(/^\/mc\s*/i, '').trim();
  if (!rest) return { command: 'help', args: [] };
  const tokens = rest.split(/\s+/);
  const [verb, ...args] = tokens;
  return { command: verb.toLowerCase(), args };
}

/**
 * Dispatch a parsed slash command to the backend. Returns the raw
 * card payload (with a `kind` discriminator) for the chat to render.
 */
export async function dispatchMCCommand(parsed) {
  const res = await authFetch(`${API}/chat/mc/dispatch`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(parsed),
  });
  if (!res.ok) {
    return {
      kind: 'mc_error',
      error: `dispatch_failed_${res.status}`,
    };
  }
  return res.json();
}
