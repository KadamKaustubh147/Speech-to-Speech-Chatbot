import os
import asyncio
import base64
import json
import uuid
import traceback
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from aws_sdk_bedrock_runtime.client import BedrockRuntimeClient, InvokeModelWithBidirectionalStreamOperationInput
from aws_sdk_bedrock_runtime.models import InvokeModelWithBidirectionalStreamInputChunk, BidirectionalInputPayloadPart
from aws_sdk_bedrock_runtime.config import Config
from smithy_aws_core.identity.environment import EnvironmentCredentialsResolver

app = FastAPI(title="Nova Sonic Voice Chatbot (With Real Memory)")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/health")
def health():
    return {"status": "ok"}

# ── THE MEMORY ARRAY ──────────────────────────────────────────
session_memory_store = {}

class WSNovaSonic:
    def __init__(self, ws: WebSocket, session_id: str):
        self.ws = ws
        self.session_id = session_id
        
        if self.session_id not in session_memory_store:
            session_memory_store[self.session_id] = []
        self.memory = session_memory_store[self.session_id]
        
        self.model_id = os.getenv("NOVA_MODEL_ID", "amazon.nova-sonic-v1:0").strip()
        self.region = os.getenv("AWS_REGION", "us-east-1").strip()
        
        config = Config(
            endpoint_uri=f"https://bedrock-runtime.{self.region}.amazonaws.com",
            region=self.region,
            aws_credentials_identity_resolver=EnvironmentCredentialsResolver(),
        )
        self.client = BedrockRuntimeClient(config=config)
        
        self.stream = None
        self.prompt_name = None
        self.audio_c_name = None
        self.in_audio_turn = False

    async def _send_event(self, event_json):
        if self.stream:
            event = InvokeModelWithBidirectionalStreamInputChunk(
                value=BidirectionalInputPayloadPart(bytes_=event_json.encode('utf-8'))
            )
            await self.stream.input_stream.send(event)

    async def start_audio_turn(self):
        """Creates a fresh stream per turn and injects the ENTIRE memory array as context."""
        self.stream = await self.client.invoke_model_with_bidirectional_stream(
            InvokeModelWithBidirectionalStreamOperationInput(model_id=self.model_id)
        )
        self.prompt_name = str(uuid.uuid4())
        
        await self._send_event('{"event": {"sessionStart": {"inferenceConfiguration": {"maxTokens": 1024, "topP": 0.9, "temperature": 0.7}}}}')
        
        await self._send_event(f'''
        {{
          "event": {{
            "promptStart": {{
              "promptName": "{self.prompt_name}",
              "textOutputConfiguration": {{ "mediaType": "text/plain" }},
              "audioOutputConfiguration": {{
                "mediaType": "audio/lpcm",
                "sampleRateHertz": 16000,
                "sampleSizeBits": 16,
                "channelCount": 1,
                "voiceId": "matthew",
                "encoding": "base64",
                "audioType": "SPEECH"
              }}
            }}
          }}
        }}
        ''')
        
        sys_c_name = str(uuid.uuid4())
        await self._send_event(f'''
        {{ "event": {{ "contentStart": {{ "promptName": "{self.prompt_name}", "contentName": "{sys_c_name}", "type": "TEXT", "interactive": false, "role": "SYSTEM", "textInputConfiguration": {{ "mediaType": "text/plain" }} }} }} }}
        ''')
        
        system_prompt = json.dumps("You are a friendly voice assistant. Keep responses brief and conversational.")
        await self._send_event(f'''
        {{ "event": {{ "textInput": {{ "promptName": "{self.prompt_name}", "contentName": "{sys_c_name}", "content": {system_prompt} }} }} }}
        ''')
        
        await self._send_event(f'''
        {{ "event": {{ "contentEnd": {{ "promptName": "{self.prompt_name}", "contentName": "{sys_c_name}" }} }} }}
        ''')

        # Inject conversation history cleanly
        for msg in self.memory:
            hist_c_name = str(uuid.uuid4())
            role = msg["role"].upper()
            safe_text = json.dumps(msg["text"])
            
            await self._send_event(f'''
            {{ "event": {{ "contentStart": {{ "promptName": "{self.prompt_name}", "contentName": "{hist_c_name}", "type": "TEXT", "interactive": false, "role": "{role}", "textInputConfiguration": {{ "mediaType": "text/plain" }} }} }} }}
            ''')
            
            await self._send_event(f'''
            {{ "event": {{ "textInput": {{ "promptName": "{self.prompt_name}", "contentName": "{hist_c_name}", "content": {safe_text} }} }} }}
            ''')
            
            await self._send_event(f'''
            {{ "event": {{ "contentEnd": {{ "promptName": "{self.prompt_name}", "contentName": "{hist_c_name}" }} }} }}
            ''')

        self.audio_c_name = str(uuid.uuid4())
        await self._send_event(f'''
        {{
            "event": {{
                "contentStart": {{
                    "promptName": "{self.prompt_name}",
                    "contentName": "{self.audio_c_name}",
                    "type": "AUDIO",
                    "interactive": true,
                    "role": "USER",
                    "audioInputConfiguration": {{
                        "mediaType": "audio/lpcm",
                        "sampleRateHertz": 16000,
                        "sampleSizeBits": 16,
                        "channelCount": 1,
                        "audioType": "SPEECH",
                        "encoding": "base64"
                    }}
                }}
            }}
        }}
        ''')

        self.in_audio_turn = True
        asyncio.create_task(self.process_responses(self.stream))

    async def send_audio_chunk(self, audio_bytes):
        if self.in_audio_turn and self.stream:
            blob = base64.b64encode(audio_bytes).decode('utf-8')
            await self._send_event(f'''
            {{ "event": {{ "audioInput": {{ "promptName": "{self.prompt_name}", "contentName": "{self.audio_c_name}", "content": "{blob}" }} }} }}
            ''')

    async def end_audio_turn(self):
        """Cleanly seal the AWS stream so it generates a response and unlocks the frontend."""
        if self.in_audio_turn and self.stream:
            try:
                await self._send_event(f'''
                {{ "event": {{ "contentEnd": {{ "promptName": "{self.prompt_name}", "contentName": "{self.audio_c_name}" }} }} }}
                ''')
                await self._send_event(f'''
                {{ "event": {{ "promptEnd": {{ "promptName": "{self.prompt_name}" }} }} }}
                ''')
                
                # Crucial: Close the stream to tell AWS we are done sending forever
                await self._send_event('{ "event": { "sessionEnd": {} } }')
                await self.stream.input_stream.close()
            except Exception as e:
                print(f"Error ending turn: {e}")
                
            self.in_audio_turn = False

    async def process_responses(self, active_stream):
        """Listen to AWS output until it naturally closes, ensuring all text and audio is received."""
        user_text = ""
        assistant_text = ""
        current_role = "assistant"
        
        try:
            while True:
                output = await active_stream.await_output()
                if not output: break
                
                result = await output[1].receive()
                if not result or not hasattr(result, 'value') or not result.value: break
                
                if result.value.bytes_:
                    response_data = result.value.bytes_.decode('utf-8')
                    json_data = json.loads(response_data)
                    
                    if 'event' in json_data:
                        event = json_data['event']
                        
                        if 'contentStart' in event:
                            current_role = event['contentStart'].get('role', 'ASSISTANT').lower()
                            
                        elif 'textOutput' in event:
                            text = event['textOutput']['content']
                            if current_role == "user":
                                user_text += text
                            else:
                                assistant_text += text
                                
                            await self.ws.send_text(json.dumps({
                                "type": "transcript",
                                "role": current_role,
                                "text": text
                            }))
                            
                        elif 'audioOutput' in event:
                            audio_bytes = base64.b64decode(event['audioOutput']['content'])
                            await self.ws.send_bytes(audio_bytes)
                            
        except Exception as e:
            # Stream gracefully closes when AWS finishes its response
            pass
        finally:
            # Tell the frontend the turn is fully complete so the mic button resets
            try:
                await self.ws.send_text(json.dumps({"type": "turn_end"}))
            except Exception:
                pass
                
            # Save the gathered transcripts for the next turn
            if user_text.strip():
                self.memory.append({"role": "user", "text": user_text.strip()})
            if assistant_text.strip():
                self.memory.append({"role": "assistant", "text": assistant_text.strip()})


@app.websocket("/ws/{session_id}")
async def websocket_endpoint(ws: WebSocket, session_id: str):
    await ws.accept()
    print(f"[{session_id}] Connected")
    
    bot = WSNovaSonic(ws, session_id)
    
    # 🔥 NEW: The Concurrency Lock to prevent duplicate AWS sessions
    turn_lock = asyncio.Lock()
    
    try:
        while True:
            msg = await ws.receive()
            if msg["type"] == "websocket.disconnect":
                break
                
            if msg.get("bytes"):
                # Traffic Light: Safely pause incoming chunks until the AWS connection is open
                async with turn_lock:
                    if not bot.in_audio_turn:
                        await bot.start_audio_turn()
                
                # Once the lock releases and the turn is active, send the chunk
                await bot.send_audio_chunk(msg["bytes"])
                
            elif msg.get("text"):
                try:
                    data = json.loads(msg["text"])
                    if data.get("type") == "end_of_turn":
                        # Lock the end-turn process to prevent crossed wires
                        async with turn_lock:
                            if bot.in_audio_turn:
                                await bot.end_audio_turn()
                except Exception:
                    pass

    except WebSocketDisconnect:
        print(f"[{session_id}] Disconnected")
    except Exception as e:
        print(f"[{session_id}] WS ERROR: {e}")
        traceback.print_exc()