import { useState, useRef, useCallback } from 'react';
import { Message, RecordingStatus } from '../types';

function generateId(): string {
  return Math.random().toString(36).slice(2, 10);
}

export function useVoiceChat() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [status, setStatus] = useState<RecordingStatus>('idle');
  const [error, setError] = useState<string | null>(null);
  
  // 🔥 NEW: Track actual WebSocket connection state
  const [isConnected, setIsConnected] = useState<boolean>(false);

  const wsRef = useRef<WebSocket | null>(null);
  const audioContextRef = useRef<AudioContext | null>(null);
  const mediaStreamRef = useRef<MediaStream | null>(null);
  const processorRef = useRef<ScriptProcessorNode | null>(null);
  const nextPlayTimeRef = useRef<number>(0);
  const audioSentRef = useRef<boolean>(false);

  // ── WebSocket connection ─────────────────────────────────────────────
  const connect = useCallback((sessionId: string): Promise<WebSocket> => {
    return new Promise((resolve, reject) => {
      if (wsRef.current?.readyState === WebSocket.OPEN) {
        resolve(wsRef.current);
        return;
      }

      const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
      const wsUrl = import.meta.env.DEV
        ? `ws://localhost:8000/ws/${sessionId}`
        : `${protocol}//${window.location.host}/ws/${sessionId}`;

      const ws = new WebSocket(wsUrl);
      wsRef.current = ws;
      ws.binaryType = 'arraybuffer';

      ws.onopen = () => {
        setIsConnected(true); // Set connected flag
        resolve(ws);
      };

      ws.onerror = () => {
        setError('WebSocket connection error.');
        setIsConnected(false);
        reject(new Error('WebSocket connection failed'));
      };

      ws.onclose = () => {
        setIsConnected(false); // Clear connected flag
        setStatus((prev) => (prev === 'speaking' ? prev : 'idle'));
      };

      ws.onmessage = async (event) => {
        if (typeof event.data === 'string') {
          const data = JSON.parse(event.data);

          if (data.type === 'transcript') {
            setMessages((prev) => {
              const last = prev[prev.length - 1];

              if (last && last.role === data.role) {
                const updated = [...prev];
                updated[updated.length - 1] = {
                  ...updated[updated.length - 1],
                  content: updated[updated.length - 1].content + data.text,
                };
                return updated;
              } else if (data.text) {
                return [
                  ...prev,
                  {
                    id: generateId(),
                    role: data.role as 'user' | 'assistant',
                    content: data.text,
                    timestamp: new Date(),
                  },
                ];
              }
              return prev;
            });
          } else if (data.type === 'turn_end') {
            if (nextPlayTimeRef.current <= (audioContextRef.current?.currentTime ?? 0)) {
              setStatus('idle');
            }
          } else if (data.type === 'error') {
            setError(data.message ?? 'Unknown error from server.');
            setStatus('idle');
          }
        } else if (event.data instanceof ArrayBuffer) {
          setStatus('speaking');
          await playAudioData(event.data);
        }
      };
    });
  }, []);

  // ── Audio playback ──────────────────────────────────────────────────
  const playAudioData = async (arrayBuffer: ArrayBuffer) => {
    const ctx = audioContextRef.current;
    if (!ctx) return;

    try {
      const int16Array = new Int16Array(arrayBuffer);
      const audioBuffer = ctx.createBuffer(1, int16Array.length, 16000);
      const channelData = audioBuffer.getChannelData(0);

      for (let i = 0; i < int16Array.length; i++) {
        channelData[i] = int16Array[i] / 32768.0;
      }

      const source = ctx.createBufferSource();
      source.buffer = audioBuffer;
      source.connect(ctx.destination);

      const playTime = Math.max(ctx.currentTime, nextPlayTimeRef.current);
      source.start(playTime);

      nextPlayTimeRef.current = playTime + audioBuffer.duration;

      source.onended = () => {
        if (nextPlayTimeRef.current <= ctx.currentTime + 0.05) {
          setStatus('idle');
          nextPlayTimeRef.current = 0;
        }
      };
    } catch (err) {
      console.error('Failed to play audio chunk', err);
    }
  };

  // ── Start recording ─────────────────────────────────────────────────
  const startRecording = useCallback(async (sessionId: string) => {
    setError(null);

    if (!navigator.mediaDevices?.getUserMedia) {
      setError('Microphone blocked. Use HTTPS or localhost.');
      return;
    }

    try {
      const ws = await connect(sessionId);
      const AudioContextClass = window.AudioContext || (window as any).webkitAudioContext;

      if (!audioContextRef.current) {
        audioContextRef.current = new AudioContextClass({ sampleRate: 16000 });
      }

      const ctx = audioContextRef.current;
      if (ctx.state === 'suspended') await ctx.resume();

      nextPlayTimeRef.current = 0;
      audioSentRef.current = false;

      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      mediaStreamRef.current = stream;

      const source = ctx.createMediaStreamSource(stream);
      // @ts-ignore
      const processor = ctx.createScriptProcessor(4096, 1, 1);
      processorRef.current = processor;

      processor.onaudioprocess = (e) => {
        if (ws.readyState === WebSocket.OPEN) {
          const inputData = e.inputBuffer.getChannelData(0);
          const pcmData = new Int16Array(inputData.length);

          for (let i = 0; i < inputData.length; i++) {
            pcmData[i] = Math.max(-1, Math.min(1, inputData[i])) * 32767;
          }

          ws.send(pcmData.buffer);
          audioSentRef.current = true;
        }
      };

      source.connect(processor);
      processor.connect(ctx.destination);
      setStatus('recording');

    } catch (err) {
      console.error(err);
      setError('Mic permission denied or connection failed.');
      setStatus('idle');
    }
  }, [connect]);

  // ── Stop recording ─────────────────────────────────────────────────
  const stopRecording = useCallback(() => {
    setStatus('processing');

    if (processorRef.current) {
      processorRef.current.disconnect();
      processorRef.current = null;
    }

    if (mediaStreamRef.current) {
      mediaStreamRef.current.getTracks().forEach((t) => t.stop());
      mediaStreamRef.current = null;
    }

    if (wsRef.current?.readyState === WebSocket.OPEN && audioSentRef.current) {
      wsRef.current.send(JSON.stringify({ type: 'end_of_turn' }));
    }

    audioSentRef.current = false;
  }, []);

  // 🔥 NEW: Disconnect session (keeps messages, drops WS)
  const disconnectSession = useCallback(() => {
    if (wsRef.current) {
      wsRef.current.close();
      wsRef.current = null;
    }
    if (audioContextRef.current) {
      audioContextRef.current.close();
      audioContextRef.current = null;
    }
    nextPlayTimeRef.current = 0;
    audioSentRef.current = false;
    setIsConnected(false);
    setStatus('idle');
  }, []);

  // ── Clear session (wipes everything) ───────────────────────────────
  const clearSession = useCallback(() => {
    disconnectSession();
    setMessages([]);
    setError(null);
  }, [disconnectSession]);

  return {
    startRecording,
    stopRecording,
    clearSession,
    disconnectSession, // Exported new function
    isConnected,       // Exported connection state
    status,
    error,
    messages,
  };
}