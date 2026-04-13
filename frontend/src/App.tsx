import { useState, useCallback, useRef, useEffect } from 'react';
import { useVoiceRecorder } from './hooks/useVoiceRecorder';
import { useTTS } from './hooks/useTTS';
import { transcribeAndChat, clearSession } from './hooks/api';
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
  const [messages, setMessages] = useState<Message[]>([]);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [status, setStatus] = useState<RecordingStatus>('idle');
  const [error, setError] = useState<string | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  const { isRecording, startRecording, stopRecording, error: recError } = useVoiceRecorder();
  const { speak, cancel: cancelTTS, isSpeaking } = useTTS();

  // Sync recorder error
  useEffect(() => {
    if (recError) setError(recError);
  }, [recError]);

  // Auto-scroll
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  // Sync speaking status
  useEffect(() => {
    if (isSpeaking) setStatus('speaking');
  }, [isSpeaking]);

  const handleMicPress = useCallback(async () => {
    if (status === 'processing' || status === 'speaking') return;

    if (!isRecording) {
      setError(null);
      setStatus('recording');
      await startRecording();
    } else {
      setStatus('processing');
      const blob = await stopRecording();

      if (!blob || blob.size < 1000) {
        setError('Recording too short. Please hold and speak clearly.');
        setStatus('idle');
        return;
      }

      try {
        const data = await transcribeAndChat(blob, sessionId);

        if (!sessionId) setSessionId(data.session_id);

        // Add user message
        setMessages((prev) => [
          ...prev,
          {
            id: generateId(),
            role: 'user',
            content: data.transcript,
            transcript: data.transcript,
            timestamp: new Date(),
          },
        ]);

        // Add assistant message
        setMessages((prev) => [
          ...prev,
          {
            id: generateId(),
            role: 'assistant',
            content: data.response,
            timestamp: new Date(),
          },
        ]);

        // Speak
        speak(data.response, () => setStatus('idle'));
        setStatus('speaking');
      } catch (err) {
        const msg = err instanceof Error ? err.message : 'Something went wrong.';
        setError(msg);
        setStatus('idle');
      }
    }
  }, [status, isRecording, sessionId, startRecording, stopRecording, speak]);

  const handleClear = useCallback(async () => {
    cancelTTS();
    if (sessionId) await clearSession(sessionId).catch(() => {});
    setSessionId(null);
    setMessages([]);
    setStatus('idle');
    setError(null);
  }, [sessionId, cancelTTS]);

  const micLabel =
    status === 'recording'
      ? 'Stop'
      : status === 'processing'
      ? 'Wait…'
      : status === 'speaking'
      ? 'Playing'
      : 'Speak';

  return (
    <div className="app">
      {/* Decorative grid */}
      <div className="grid-bg" aria-hidden="true" />

      <header className="app__header">
        <div className="app__logo">
          <span className="app__logo-mark">◈</span>
          <h1>VoiceAI</h1>
        </div>
        <div className="app__header-right">
          <StatusBadge status={status} />
          {messages.length > 0 && (
            <button className="btn btn--ghost" onClick={handleClear}>
              Clear
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
          <button onClick={() => setError(null)} aria-label="Dismiss">✕</button>
        </div>
      )}

      <footer className="app__footer">
        <WaveformBars active={isRecording} />
        <button
          className={`mic-btn mic-btn--${status}`}
          onClick={handleMicPress}
          disabled={status === 'processing'}
          aria-label={micLabel}
        >
          <span className="mic-btn__ring mic-btn__ring--1" />
          <span className="mic-btn__ring mic-btn__ring--2" />
          <MicIcon recording={isRecording} status={status} />
          <span className="mic-btn__label">{micLabel}</span>
        </button>
        <WaveformBars active={isSpeaking} />
      </footer>
    </div>
  );
}

function MicIcon({ recording, status }: { recording: boolean; status: RecordingStatus }) {
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
      <rect x="9" y="2" width="6" height="12" rx="3" fill={recording ? 'currentColor' : 'none'} />
      <path d="M5 10a7 7 0 0 0 14 0" />
      <line x1="12" y1="17" x2="12" y2="22" />
      <line x1="8" y1="22" x2="16" y2="22" />
    </svg>
  );
}
