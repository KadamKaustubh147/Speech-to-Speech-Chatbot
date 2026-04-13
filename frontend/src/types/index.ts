export interface Message {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  transcript?: string;
  timestamp: Date;
}

export interface ChatResponse {
  session_id: string;
  transcript: string;
  response: string;
}

export type RecordingStatus = 'idle' | 'recording' | 'processing' | 'speaking';
