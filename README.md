⚠️ This README needs an update


# VoiceAI — Production Voice Chatbot

A full-stack voice chatbot using **AssemblyAI** for transcription, **LangChain + Gemini** for conversational AI, and **browser TTS** for speech output. Pure HTTP — no WebSockets, no streaming.

---

## Architecture

```
Browser
  └─ MediaRecorder → audio blob
       └─ POST /api/transcribe-and-chat
            └─ Backend (FastAPI)
                 ├─ AssemblyAI  → batch transcription → transcript text
                 ├─ LangChain v1 create_agent()
                 │    ├─ init_chat_model("google_genai:gemini-2.0-flash")
                 │    └─ InMemorySaver checkpointer  (keyed by thread_id)
                 └─ JSON response { session_id, transcript, response }
  └─ SpeechSynthesisUtterance(response).speak()
```

### Session memory — LangChain v1
Each browser session gets a UUID that becomes the LangGraph `thread_id`. The `InMemorySaver` checkpointer (from `langgraph.checkpoint.memory`) stores the full agent state per thread — no manual dict management, no `RunnableWithMessageHistory`, no deprecated APIs.

---

## Project Structure

```
voice-chatbot/
├── backend/
│   ├── main.py              # FastAPI app — all logic in one file
│   ├── requirements.txt
│   └── Dockerfile
├── frontend/
│   ├── src/
│   │   ├── App.tsx          # Main UI
│   │   ├── App.css          # Styles
│   │   ├── main.tsx
│   │   ├── index.css
│   │   ├── hooks/
│   │   │   ├── useVoiceRecorder.ts   # MediaRecorder wrapper
│   │   │   ├── useTTS.ts             # SpeechSynthesis wrapper
│   │   │   └── api.ts                # HTTP client
│   │   └── types/
│   │       └── index.ts
│   ├── index.html
│   ├── nginx.conf           # Proxies /api → backend, SPA fallback
│   ├── vite.config.ts
│   ├── package.json
│   ├── tsconfig.json
│   └── Dockerfile
├── docker-compose.yml
├── .env.example
└── README.md
```

---

## Prerequisites

- [Docker](https://docs.docker.com/get-docker/) + [Docker Compose](https://docs.docker.com/compose/)
- An **AssemblyAI** API key → https://www.assemblyai.com/dashboard
- A **Google AI Studio** API key → https://aistudio.google.com/apikey

---

## Quickstart

### 1. Clone & configure

```bash
git clone <your-repo>
cd voice-chatbot

cp .env.example .env
# Edit .env and fill in both API keys
```

### 2. Run with Docker Compose

```bash
docker compose up --build
```

- Frontend → http://localhost
- Backend API → http://localhost:8000
- API docs → http://localhost:8000/docs

### 3. Use it

1. Open http://localhost in your browser
2. Click the **◈ mic button** and speak
3. Release — your audio is sent to the backend
4. Wait for transcription + AI response
5. The response is displayed and **spoken aloud** via browser TTS

---

## Local Development (without Docker)

### Backend

```bash
cd backend
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt

export ASSEMBLYAI_API_KEY=your_key
export GOOGLE_API_KEY=your_key

uvicorn main:app --reload --port 8000
```

### Frontend

```bash
cd frontend
npm install

# Vite dev server proxies /api → http://backend:8000
# For local dev, edit vite.config.ts proxy target to http://localhost:8000

npm run dev
# → http://localhost:5173
```

---

## API Reference

### `POST /transcribe-and-chat`

**Form data:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `audio` | File | ✅ | Audio file (webm, mp3, wav, ogg, m4a) |
| `session_id` | string | ❌ | Reuse an existing session for memory continuity |

**Response:**

```json
{
  "session_id": "abc123",
  "transcript": "What is the capital of France?",
  "response": "The capital of France is Paris!"
}
```

### `GET /session/{session_id}/history`

Returns the full conversation history for a session.

### `DELETE /session/{session_id}`

Clears memory for a session.

### `GET /health`

Returns `{ "status": "ok" }`.

---

## Environment Variables

| Variable | Description |
|----------|-------------|
| `ASSEMBLYAI_API_KEY` | AssemblyAI API key for batch transcription |
| `GOOGLE_API_KEY` | Google AI Studio key for Gemini via LangChain |

---

## Notes

- **LangChain v1**: Uses `create_agent` + `init_chat_model` + `InMemorySaver` — the current recommended API. No `RunnableWithMessageHistory`, no `ChatPromptTemplate`, no deprecated patterns.
- **Audio format**: The browser records in `audio/webm` (Chrome/Firefox) or `audio/mp4` (Safari). AssemblyAI supports all of these natively.
- **Session persistence**: Sessions are in-memory only (LangGraph `InMemorySaver`). Restarting the backend clears all conversation history. For production, swap `InMemorySaver` for `PostgresSaver` from `langgraph-checkpoint-postgres`.
- **TTS voices**: Browser TTS quality varies by OS/browser. Chrome on macOS/Windows typically gives the best voices.
- **Max audio size**: Nginx is configured for 20 MB uploads, which is ~10+ minutes of speech.
