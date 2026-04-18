import { useState, useCallback, useRef, useEffect } from 'react';
import { useVoiceChat } from './hooks/useVoiceChat';
import { Message, RecordingStatus } from './types';
import './App.css';

function generateId(): string {
  return Math.random().toString(36).slice(2, 10);
}

function WaveformBars({ active }: { active: boolean }) {
  return (
    <div className={`waveform ${active ? 'waveform--active' : ''}`}>
      {Array.from({ length: 12 }).map((_, i) => (
        <span key={i} className="waveform__bar" style={{ '--i': i } as React.CSSProperties} />
      ))}
    </div>
  );
}

function StatusBadge({ status }: { status: RecordingStatus }) {
  const labels: Record<RecordingStatus, string> = {
    idle: 'Ready',
    recording: 'Listening…',
    processing: 'Thinking…',
    speaking: 'Speaking…',
  };
  return (
    <div className={`status-badge status-badge--${status}`}>
      <span className="status-badge__dot" />
      {labels[status]}
    </div>
  );
}

function MessageBubble({ msg }: { msg: Message }) {
  return (
    <div className={`bubble bubble--${msg.role}`}>
      {msg.role === 'user' && msg.transcript && (
        <p className="bubble__transcript">🎙 {msg.transcript}</p>
      )}
      <p className="bubble__content">{msg.content}</p>
      <span className="bubble__time">
        {msg.timestamp.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
      </span>
    </div>
  );
}

export default function App() {
  const [sessionId] = useState<string>(generateId());
  const bottomRef = useRef<HTMLDivElement>(null);

  const {
    startRecording,
    stopRecording,
    clearSession,
    disconnectSession,
    isConnected,
    status,
    error,
    messages
  } = useVoiceChat();

  // Auto-scroll
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  const handleMicPress = useCallback(async () => {
    if (status === 'processing' || status === 'speaking') return;

    if (status === 'idle') {
      await startRecording(sessionId);
    } else if (status === 'recording') {
      stopRecording();
    }
  }, [status, sessionId, startRecording, stopRecording]);

  const handleClear = useCallback(() => {
    clearSession();
  }, [clearSession]);

  const handleDisconnect = useCallback(() => {
    disconnectSession();
  }, [disconnectSession]);

  // 🔥 CHANGED: Dynamic labels based on connection state and recording status
  const micLabel =
    status === 'recording'
      ? 'Listening…'
      : status === 'processing'
      ? 'Thinking…'
      : status === 'speaking'
      ? 'AI Speaking…'
      : isConnected
      ? 'Listening...'
      : 'Connect & Speak';

  return (
    <div className="app">
      <div className="grid-bg" aria-hidden="true" />

      <header className="app__header">
        <div className="app__logo">
          <span className="app__logo-mark">◈</span>
          <h1>VoiceAI</h1>
        </div>
        <div className="app__header-right">
          
          <div className={`connection-status ${isConnected ? 'connected' : 'disconnected'}`}>
            <span className="connection-dot" />
            {isConnected ? 'Online' : 'Offline'}
          </div>

          <StatusBadge status={status} />
          
          {isConnected && (
            <button className="btn btn--ghost" onClick={handleDisconnect}>
              Disconnect
            </button>
          )}

          {messages.length > 0 && (
            <button className="btn btn--ghost" onClick={handleClear}>
              Clear Chat
            </button>
          )}
        </div>
      </header>

      <main className="app__main">
        {messages.length === 0 ? (
          <div className="empty-state">
            <div className="empty-state__orb" />
            <p className="empty-state__headline">Start speaking</p>
            <p className="empty-state__sub">
              Press the mic button, ask anything, then release.
            </p>
          </div>
        ) : (
          <div className="messages">
            {messages.map((m) => (
              <MessageBubble key={m.id} msg={m} />
            ))}
            <div ref={bottomRef} />
          </div>
        )}
      </main>

      {error && (
        <div className="error-banner" role="alert">
          <span>⚠ {error}</span>
        </div>
      )}

      <footer className="app__footer">
        <WaveformBars active={status === 'recording'} />
        
        {/* 🔥 The Mic button dynamically changes its text now */}
        <button
          className={`mic-btn mic-btn--${status}`}
          onClick={handleMicPress}
          disabled={status === 'processing' || status === 'speaking'}
          aria-label={micLabel}
        >
          <span className="mic-btn__ring mic-btn__ring--1" />
          <span className="mic-btn__ring mic-btn__ring--2" />
          <MicIcon status={status} />
          <span className="mic-btn__label">{micLabel}</span>
        </button>
        
        <WaveformBars active={status === 'speaking'} />
      </footer>
    </div>
  );
}

function MicIcon({ status }: { status: RecordingStatus }) {
  if (status === 'processing') {
    return (
      <svg className="mic-icon spin" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
        <path d="M21 12a9 9 0 1 1-6.219-8.56" />
      </svg>
    );
  }
  if (status === 'speaking') {
    return (
      <svg className="mic-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
        <polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5" />
        <path d="M19.07 4.93a10 10 0 0 1 0 14.14" />
        <path d="M15.54 8.46a5 5 0 0 1 0 7.07" />
      </svg>
    );
  }
  return (
    <svg className="mic-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <rect x="9" y="2" width="6" height="12" rx="3" fill={status === 'recording' ? 'currentColor' : 'none'} />
      <path d="M5 10a7 7 0 0 0 14 0" />
      <line x1="12" y1="17" x2="12" y2="22" />
      <line x1="8" y1="22" x2="16" y2="22" />
    </svg>
  );
}