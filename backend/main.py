import asyncio
import base64
import json
import os
import uuid
from typing import Dict

import boto3
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from langchain_aws.chat_models.bedrock_nova_sonic import ChatBedrockNovaSonic

app = FastAPI(title="Nova Sonic Voice Chatbot")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Model config
MODEL_ID = "amazon.nova-2-sonic-v1:0"   # or "amazon.nova-sonic-v1:0"
REGION   = os.getenv("AWS_REGION", "us-east-1")
VOICE_ID = os.getenv("NOVA_VOICE_ID", "matthew")  # matthew | tiffany | amy

# Active sessions: session_id → NovaSonicSession
sessions: Dict[str, object] = {}


BEDROCK_API_KEY = os.getenv("AWS_BEARER_TOKEN_BEDROCK")

def make_model():
    return ChatBedrockNovaSonic(
        model_id=os.getenv("NOVA_MODEL_ID", "amazon.nova-sonic-v1:0"),
        region_name=os.getenv("AWS_REGION", "us-east-1"),
        voice_id=os.getenv("NOVA_VOICE_ID", "matthew"),
        credentials_profile_name=None,
        # Bearer token auth
        aws_bearer_token_bedrock=BEDROCK_API_KEY,
    )


@app.get("/health")
def health():
    return {"status": "ok"}


@app.websocket("/ws/{session_id}")
async def websocket_endpoint(ws: WebSocket, session_id: str):
    await ws.accept()
    model = make_model()

    try:
        async with model.create_session() as session:
            sessions[session_id] = session

            # Kick off send and receive concurrently
            send_task    = asyncio.create_task(_send_loop(ws, session))
            receive_task = asyncio.create_task(_receive_loop(ws, session))

            done, pending = await asyncio.wait(
                [send_task, receive_task],
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()

    except WebSocketDisconnect:
        pass
    finally:
        sessions.pop(session_id, None)


async def _send_loop(ws: WebSocket, session):
    """Receive audio chunks from browser → forward to Nova Sonic."""
    try:
        while True:
            msg = await ws.receive()
            if msg["type"] == "websocket.disconnect":
                break

            if "bytes" in msg and msg["bytes"]:
                # Raw PCM audio bytes from browser
                await session.send_audio_chunk(msg["bytes"])

            elif "text" in msg and msg["text"]:
                data = json.loads(msg["text"])
                if data.get("type") == "end_of_turn":
                    # Signal that the user finished speaking
                    await session.send_audio_chunk(b"", end_of_turn=True)
    except Exception:
        pass


async def _receive_loop(ws: WebSocket, session):
    """Receive events from Nova Sonic → forward audio/text to browser."""
    try:
        async for event in session.receive_events():
            if event["type"] == "audio":
                # Send raw PCM back to browser as binary
                await ws.send_bytes(event["audio"])
            elif event["type"] == "text":
                await ws.send_text(json.dumps({
                    "type": "transcript",
                    "role": event.get("role", "assistant"),
                    "text": event.get("text", ""),
                }))
            elif event["type"] == "content_block_stop":
                await ws.send_text(json.dumps({"type": "turn_end"}))
    except Exception:
        pass