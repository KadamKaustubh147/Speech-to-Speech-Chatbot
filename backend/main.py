import asyncio
import json
import os
import traceback
from typing import Dict

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

# Active sessions: session_id → NovaSonicSession
sessions: Dict[str, object] = {}

def make_model():
    """
    Initializes the Bedrock Nova Sonic model.
    Boto3 will automatically authenticate using the AWS_ACCESS_KEY_ID 
    and AWS_SECRET_ACCESS_KEY environment variables provided by Docker.
    """
    return ChatBedrockNovaSonic(
        model_id=os.getenv("NOVA_MODEL_ID", "amazon.nova-sonic-v1:0"),
        region_name=os.getenv("AWS_REGION", "us-east-1"),
        voice_id=os.getenv("NOVA_VOICE_ID", "matthew"),
    )

@app.get("/health")
def health():
    return {"status": "ok"}


@app.websocket("/ws/{session_id}")
async def websocket_endpoint(ws: WebSocket, session_id: str):
    await ws.accept()

    try:
        # Initializing the model INSIDE the try block so validation 
        # or auth errors are caught and printed to the terminal.
        model = make_model()

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
        print(f"[{session_id}] Client disconnected normally.")
    except Exception as e:
        print(f"[{session_id}] SESSION SETUP ERROR: {e}")
        traceback.print_exc()
    finally:
        sessions.pop(session_id, None)

async def _send_loop(ws: WebSocket, session):
    """Receive audio chunks from browser → forward to Nova Sonic."""
    audio_sent = False
    try:
        while True:
            msg = await ws.receive()
            if msg["type"] == "websocket.disconnect":
                break

            if "bytes" in msg and msg["bytes"]:
                # Raw PCM audio bytes from browser
                await session.send_audio_chunk(msg["bytes"])
                audio_sent = True

            elif "text" in msg and msg["text"]:
                data = json.loads(msg["text"])
                if data.get("type") == "end_of_turn":
                    # Only tell AWS the turn ended IF audio was actually captured
                    if audio_sent:
                        await session.end_audio_input()
                        audio_sent = False # reset for the next turn
                    else:
                        print(f"Skipped ending turn: No audio was sent by the client.")
    except Exception as e:
        print(f"SEND LOOP ERROR: {e}")
        import traceback
        traceback.print_exc()

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
    except Exception as e:
        print(f"RECEIVE LOOP ERROR: {e}")
        traceback.print_exc()