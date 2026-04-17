import { useState, useRef, useCallback } from 'react';
import { Message, RecordingStatus } from '../types';

function generateId(): string {
  return Math.random().toString(36).slice(2, 10);
}

export function useVoiceChat() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [status, setStatus] = useState<RecordingStatus>('idle');
  const [error, setError] = useState<string | null>(null);

  const wsRef = useRef<WebSocket | null>(null);
  const audioContextRef = useRef<AudioContext | null>(null);
  const mediaStreamRef = useRef<MediaStream | null>(null);
  const processorRef = useRef<ScriptProcessorNode | null>(null);
  const nextPlayTimeRef = useRef<number>(0);

  const connect = useCallback((sessionId: string) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) return;

    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    // Use the Vite proxy in dev, or Nginx path in production
    const wsUrl = import.meta.env.DEV 
        ? `ws://${window.location.host}/ws/${sessionId}`
        : `${protocol}//${window.location.host}/ws/${sessionId}`;

    const ws = new WebSocket(wsUrl);
    wsRef.current = ws;
    ws.binaryType = 'arraybuffer';

    ws.onmessage = async (event) => {
      if (typeof event.data === 'string') {
        const data = JSON.parse(event.data);
        
        if (data.type === 'transcript') {
          setMessages((prev) => {
            const last = prev[prev.length - 1];
            if (last && last.role === data.role) {
              const updated = [...prev];
              updated[updated.length - 1].content += data.text;
              return updated;
            } else if (data.text) {
              return [...prev, { id: generateId(), role: data.role as 'user'|'assistant', content: data.text, timestamp: new Date() }];
            }
            return prev;
          });
        } else if (data.type === 'turn_end') {
          setStatus('idle');
        }
      } else if (event.data instanceof ArrayBuffer) {
        setStatus('speaking');
        playAudioData(event.data);
      }
    };

    ws.onerror = () => setError('WebSocket connection error.');
    ws.onclose = () => setStatus('idle');
  }, []);

  const playAudioData = async (arrayBuffer: ArrayBuffer) => {
    if (!audioContextRef.current) {
      audioContextRef.current = new (window.AudioContext || (window as any).webkitAudioContext)();
    }
    const ctx = audioContextRef.current;
    
    try {
      // Bedrock returns raw 16-bit 16kHz PCM. browsers expect WAV headers.
      // We manually construct an AudioBuffer from the raw PCM data.
      const int16Array = new Int16Array(arrayBuffer);
      const audioBuffer = ctx.createBuffer(1, int16Array.length, 16000);
      const channelData = audioBuffer.getChannelData(0);
      for (let i = 0; i < int16Array.length; i++) {
        channelData[i] = int16Array[i] / 32768.0; // Convert 16-bit int to float32
      }

      const source = ctx.createBufferSource();
      source.buffer = audioBuffer;
      source.connect(ctx.destination);
      
      const playTime = Math.max(ctx.currentTime, nextPlayTimeRef.current);
      source.start(playTime);
      nextPlayTimeRef.current = playTime + audioBuffer.duration;
    } catch (err) {
      console.error("Failed to play audio chunk", err);
    }
  };

  const startRecording = useCallback(async (sessionId: string) => {
    setError(null);
    try {
      connect(sessionId);
      
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      mediaStreamRef.current = stream;

      // Bedrock expects 16kHz audio sample rate
      const ctx = new (window.AudioContext || (window as any).webkitAudioContext)({ sampleRate: 16000 });
      audioContextRef.current = ctx;

      const source = ctx.createMediaStreamSource(stream);
      const processor = ctx.createScriptProcessor(4096, 1, 1);
      processorRef.current = processor;

      processor.onaudioprocess = (e) => {
        if (wsRef.current?.readyState === WebSocket.OPEN) {
          const inputData = e.inputBuffer.getChannelData(0);
          const pcmData = new Int16Array(inputData.length);
          for (let i = 0; i < inputData.length; i++) {
            pcmData[i] = Math.max(-1, Math.min(1, inputData[i])) * 32767;
          }
          // Send raw PCM binary to backend
          wsRef.current.send(pcmData.buffer);
        }
      };

      source.connect(processor);
      processor.connect(ctx.destination);
      setStatus('recording');

    } catch (err) {
      setError('Microphone access denied or error starting recording.');
      setStatus('idle');
    }
  }, [connect]);

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
    // Signal to the backend that the user finished speaking
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ type: 'end_of_turn' }));
    }
  }, []);

  const clearSession = useCallback(() => {
    if (wsRef.current) {
      wsRef.current.close();
      wsRef.current = null;
    }
    setMessages([]);
    setStatus('idle');
    setError(null);
  }, []);

  return { startRecording, stopRecording, clearSession, status, error, messages };
}