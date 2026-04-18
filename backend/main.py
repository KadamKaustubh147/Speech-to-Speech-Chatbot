import asyncio
import json
import os
import traceback
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


def make_model():
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
    print(f"[{session_id}] Connected")

    try:
        model = make_model()

        async with model.create_session() as session:

            ready_event = asyncio.Event()

            state = {
                "turn_active": False,
                "audio_sent": False,
                "initialized": False,  # 🔥 NEW
            }

            send_task = asyncio.create_task(
                _send_loop(ws, session, session_id, ready_event, state)
            )
            receive_task = asyncio.create_task(
                _receive_loop(ws, session, session_id, ready_event, state)
            )

            await asyncio.wait([send_task, receive_task], return_when=asyncio.FIRST_COMPLETED)

    except Exception as e:
        print(f"[{session_id}] ERROR: {e}")


# ─────────────────────────────────────────────────────────────
# SEND LOOP
# ─────────────────────────────────────────────────────────────
async def _send_loop(ws, session, session_id, ready_event, state):
    try:
        await asyncio.wait_for(ready_event.wait(), timeout=10.0)
        print(f"[{session_id}] Ready for audio")

        while True:
            msg = await ws.receive()

            if msg["type"] == "websocket.disconnect":
                break

            # ── AUDIO ─────────────────────────────────────
            if msg.get("bytes"):
                if not state["turn_active"]:
                    state["turn_active"] = True
                    print(f"[{session_id}] TURN START")

                await session.send_audio_chunk(msg["bytes"])
                state["audio_sent"] = True

            # ── CONTROL ───────────────────────────────────
            elif msg.get("text"):
                try:
                    data = json.loads(msg["text"])
                except:
                    continue

                if data.get("type") == "end_of_turn":

                    if state["turn_active"] and state["audio_sent"]:
                        print(f"[{session_id}] TURN END → sending to Nova")

                        await session.end_audio_input()

                        state["turn_active"] = False
                        state["audio_sent"] = False

                    else:
                        print(
                            f"[{session_id}] Ignored end_of_turn "
                            f"(turn_active={state['turn_active']}, audio_sent={state['audio_sent']})"
                        )

    except WebSocketDisconnect:
        pass
    except Exception as e:
        print(f"[{session_id}] SEND ERROR: {e}")
        traceback.print_exc()


# ─────────────────────────────────────────────────────────────
# RECEIVE LOOP
# ─────────────────────────────────────────────────────────────
async def _receive_loop(ws, session, session_id, ready_event, state):
    try:
        first = True

        async for event in session.receive_events():

            # 🔥 THIS IS THE REAL FIX
            if first:
                ready_event.set()
                first = False

                # 🚨 FORCE CONTENT BLOCK CREATION
                if not state["initialized"]:
                    print(f"[{session_id}] Initializing content block")

                    # Send tiny silent audio to open block
                    await session.send_audio_chunk(b"\x00\x00")
                    state["initialized"] = True

            event_type = event.get("type")

            if event_type == "audio":
                await ws.send_bytes(event["audio"])

            elif event_type == "text":
                await ws.send_text(json.dumps({
                    "type": "transcript",
                    "role": event.get("role", "assistant"),
                    "text": event.get("text", ""),
                }))

            elif event_type == "content_block_stop":
                print(f"[{session_id}] TURN COMPLETE")
                state["turn_active"] = False

                await ws.send_text(json.dumps({
                    "type": "turn_end"
                }))

            elif event_type == "error":
                msg = event.get("message", "Unknown error")
                print(f"[{session_id}] Nova error: {msg}")

    except Exception as e:
        print(f"[{session_id}] RECEIVE ERROR: {e}")
        traceback.print_exc()