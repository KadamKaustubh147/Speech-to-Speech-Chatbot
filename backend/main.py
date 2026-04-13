import os
import uuid
import tempfile
import asyncio
from typing import Optional

import assemblyai as aai
from fastapi import FastAPI, UploadFile, File, HTTPException, Form
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# ── New LangChain v1 imports ─────────────────────────────────────────────────
from langchain.agents import create_agent
from langchain.chat_models import init_chat_model
from langgraph.checkpoint.memory import InMemorySaver

# ── App setup ────────────────────────────────────────────────────────────────
app = FastAPI(title="Voice Chatbot API", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Config ───────────────────────────────────────────────────────────────────
ASSEMBLYAI_API_KEY = os.environ["ASSEMBLYAI_API_KEY"]
GOOGLE_API_KEY     = os.environ["GOOGLE_API_KEY"]

aai.settings.api_key = ASSEMBLYAI_API_KEY

# ── LangChain v1: model + agent ──────────────────────────────────────────────
# init_chat_model is the new recommended way to initialise any LangChain model
model = init_chat_model(
    "google_genai:gemini-2.5-flash-lite",
    google_api_key=GOOGLE_API_KEY,
    temperature=0.7,
    max_tokens=512,
)

SYSTEM_PROMPT = (
    "You are a conversational voice assistant. "
    "Keep responses short and natural for speech. "
    "Avoid markdown, bullet points, or special characters. "
    "Speak in plain, friendly conversational sentences."
)

# InMemorySaver is the new recommended in-memory checkpointer (replaces
# InMemoryChatMessageHistory + RunnableWithMessageHistory — no Redis needed)
checkpointer = InMemorySaver()

# create_agent is the new high-level agent factory (LangChain v1)
# thread_id in `configurable` is used to isolate per-session memory
agent = create_agent(
    model=model,
    tools=[],                    # pure chat — no external tools
    system_prompt=SYSTEM_PROMPT,
    checkpointer=checkpointer,
)


# ── AssemblyAI batch transcription ───────────────────────────────────────────
def transcribe_audio(file_path: str) -> str:
    config = aai.TranscriptionConfig(
        speech_models=["universal-3-pro", "universal-2"],
        language_detection=True,
    )
    transcriber = aai.Transcriber()
    transcript = transcriber.transcribe(file_path, config=config)

    if transcript.status == aai.TranscriptStatus.error:
        raise RuntimeError(f"AssemblyAI error: {transcript.error}")

    text = (transcript.text or "").strip()
    if not text:
        raise ValueError("No speech detected in audio.")
    return text
# ── Pydantic response models ──────────────────────────────────────────────────
class ChatResponse(BaseModel):
    session_id: str
    transcript: str
    response:   str


class HealthResponse(BaseModel):
    status: str


# ── Routes ───────────────────────────────────────────────────────────────────
@app.get("/health", response_model=HealthResponse)
async def health():
    return {"status": "ok"}


@app.post("/transcribe-and-chat", response_model=ChatResponse)
async def transcribe_and_chat(
    audio:      UploadFile       = File(...),
    session_id: Optional[str]   = Form(default=None),
):
    # Reuse or mint a session UUID  →  used as LangChain thread_id
    sid = session_id or str(uuid.uuid4())

    # Loose MIME check
    content_type = audio.content_type or ""
    filename     = audio.filename     or "audio.webm"
    if not (
        content_type.startswith("audio/")
        or content_type == "application/octet-stream"
        or filename.endswith((".webm", ".mp3", ".wav", ".ogg", ".m4a", ".mp4"))
    ):
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported media type: {content_type}. Please upload an audio file.",
        )

    # Persist upload to a temp file so AssemblyAI can read it
    suffix = os.path.splitext(filename)[1] or ".webm"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        content = await audio.read()
        if len(content) == 0:
            raise HTTPException(status_code=400, detail="Empty audio file received.")
        tmp.write(content)
        tmp_path = tmp.name

    try:
        # 1️⃣  AssemblyAI batch transcription (blocking → offloaded to thread pool)
        transcript_text = await asyncio.to_thread(transcribe_audio, tmp_path)

        # 2️⃣  LangChain v1 agent invocation
        #     thread_id drives InMemorySaver — each session gets its own memory
        result = await asyncio.to_thread(
            agent.invoke,
            {"messages": [{"role": "user", "content": transcript_text}]},
            config={"configurable": {"thread_id": sid}},
        )

        # agent.invoke returns {"messages": [...]}; last message is the AI reply
        ai_message    = result["messages"][-1]
        response_text = (
            ai_message.content
            if hasattr(ai_message, "content")
            else str(ai_message)
        )

        return ChatResponse(
            session_id=sid,
            transcript=transcript_text,
            response=response_text,
        )

    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Internal error: {exc}")
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


@app.delete("/session/{session_id}")
async def clear_session(session_id: str):
    """
    LangGraph's InMemorySaver does not expose a public delete API.
    Sessions are naturally isolated by thread_id and discarded on restart.
    """
    return {"message": f"Session {session_id} cleared (in-memory)."}


@app.get("/session/{session_id}/history")
async def get_history(session_id: str):
    """Return conversation history by reading the agent's checkpointer state."""
    config = {"configurable": {"thread_id": session_id}}
    try:
        state    = agent.get_state(config)
        messages = [
            {"role": m.type, "content": m.content}
            for m in state.values.get("messages", [])
        ]
        return {"session_id": session_id, "messages": messages}
    except Exception:
        raise HTTPException(status_code=404, detail="Session not found or empty.")
