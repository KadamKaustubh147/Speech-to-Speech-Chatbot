import { ChatResponse } from '../types';

const BASE_URL = import.meta.env.VITE_API_URL ?? '/api';

export async function transcribeAndChat(
  audioBlob: Blob,
  sessionId: string | null
): Promise<ChatResponse> {
  const form = new FormData();

  // Some browsers record as audio/webm, others as audio/mp4 etc.
  const ext = audioBlob.type.includes('mp4') ? 'mp4' : 'webm';
  form.append('audio', audioBlob, `recording.${ext}`);
  if (sessionId) form.append('session_id', sessionId);

  const res = await fetch(`${BASE_URL}/transcribe-and-chat`, {
    method: 'POST',
    body: form,
  });

  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Unknown error' }));
    throw new Error(err.detail ?? `HTTP ${res.status}`);
  }

  return res.json() as Promise<ChatResponse>;
}

export async function clearSession(sessionId: string): Promise<void> {
  await fetch(`${BASE_URL}/session/${sessionId}`, { method: 'DELETE' });
}
