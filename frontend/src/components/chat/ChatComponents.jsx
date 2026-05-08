// Legacy compatibility shim — the original monolithic ChatComponents.jsx has
// been split into three focused files: ChatMessages.jsx, ChatInput.jsx,
// VoiceSelector.jsx.  Import the new files directly; this shim exists only so
// in-flight imports keep compiling during the cleanup.
export { default as ChatMessages } from './ChatMessages';
export { default as VoiceSelector } from './VoiceSelector';
export { default as ChatInputArea, ChatInput } from './ChatInput';
