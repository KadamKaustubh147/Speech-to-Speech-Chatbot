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

app = FastAPI(title="Nova Sonic Voice Chatbot (Bidirectional Stream)")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/health")
def health():
    return {"status": "ok"}

class WSNovaSonic:
    def __init__(self, ws: WebSocket, session_id: str):
        self.ws = ws
        self.session_id = session_id
        self.model_id = os.getenv("NOVA_MODEL_ID", "amazon.nova-sonic-v1:0").strip()
        self.region = os.getenv("AWS_REGION", "us-east-1").strip()
        self.client = None
        self.stream = None
        self.is_active = False
        
        self.prompt_name = str(uuid.uuid4())
        self.content_name = str(uuid.uuid4())
        self.audio_content_name = None  # Generated dynamically per turn
        
        self.in_audio_turn = False

    def _initialize_client(self):
        """Initialize the Bedrock client using the Smithy core."""
        config = Config(
            endpoint_uri=f"https://bedrock-runtime.{self.region}.amazonaws.com",
            region=self.region,
            aws_credentials_identity_resolver=EnvironmentCredentialsResolver(),
        )
        self.client = BedrockRuntimeClient(config=config)

    async def send_event(self, event_json):
        """Send a raw JSON event to the Bedrock stream."""
        event = InvokeModelWithBidirectionalStreamInputChunk(
            value=BidirectionalInputPayloadPart(bytes_=event_json.encode('utf-8'))
        )
        await self.stream.input_stream.send(event)

    async def start_session(self):
        """Initialize connection and send system prompts."""
        if not self.client:
            self._initialize_client()
            
        self.stream = await self.client.invoke_model_with_bidirectional_stream(
            InvokeModelWithBidirectionalStreamOperationInput(model_id=self.model_id)
        )
        self.is_active = True
        
        # 1. Session Start
        await self.send_event('''
        {
          "event": {
            "sessionStart": {
              "inferenceConfiguration": { "maxTokens": 1024, "topP": 0.9, "temperature": 0.7 }
            }
          }
        }
        ''')
        
        # 2. Prompt Start (Note: Output changed to 16000Hz to match your frontend)
        await self.send_event(f'''
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
        
        # 3. System Prompt setup
        await self.send_event(f'''
        {{
            "event": {{
                "contentStart": {{
                    "promptName": "{self.prompt_name}",
                    "contentName": "{self.content_name}",
                    "type": "TEXT",
                    "interactive": false,
                    "role": "SYSTEM",
                    "textInputConfiguration": {{ "mediaType": "text/plain" }}
                }}
            }}
        }}
        ''')
        
        system_prompt = "You are a friendly, concise voice assistant. Keep responses brief."
        await self.send_event(f'''
        {{
            "event": {{
                "textInput": {{
                    "promptName": "{self.prompt_name}",
                    "contentName": "{self.content_name}",
                    "content": "{system_prompt}"
                }}
            }}
        }}
        ''')
        
        await self.send_event(f'''
        {{
            "event": {{
                "contentEnd": {{
                    "promptName": "{self.prompt_name}",
                    "contentName": "{self.content_name}"
                }}
            }}
        }}
        ''')

    async def start_audio_input(self):
        """Called when the user clicks the mic."""
        self.audio_content_name = str(uuid.uuid4())
        await self.send_event(f'''
        {{
            "event": {{
                "contentStart": {{
                    "promptName": "{self.prompt_name}",
                    "contentName": "{self.audio_content_name}",
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

    async def send_audio_chunk(self, audio_bytes):
        """Pass mic data from the frontend to Bedrock."""
        if not self.is_active: return
        blob = base64.b64encode(audio_bytes).decode('utf-8')
        await self.send_event(f'''
        {{
            "event": {{
                "audioInput": {{
                    "promptName": "{self.prompt_name}",
                    "contentName": "{self.audio_content_name}",
                    "content": "{blob}"
                }}
            }}
        }}
        ''')

    async def end_audio_input(self):
        """Called when the user releases the mic."""
        await self.send_event(f'''
        {{
            "event": {{
                "contentEnd": {{
                    "promptName": "{self.prompt_name}",
                    "contentName": "{self.audio_content_name}"
                }}
            }}
        }}
        ''')

    async def process_bedrock_responses(self):
        """Listen to Bedrock and route data back to the frontend."""
        try:
            while self.is_active:
                output = await self.stream.await_output()
                result = await output[1].receive()
                
                if result.value and result.value.bytes_:
                    response_data = result.value.bytes_.decode('utf-8')
                    json_data = json.loads(response_data)
                    
                    if 'event' in json_data:
                        event = json_data['event']
                        
                        # 1. Route text to UI bubbles
                        if 'textOutput' in event:
                            text = event['textOutput']['content']
                            await self.ws.send_text(json.dumps({
                                "type": "transcript",
                                "role": "assistant",
                                "text": text
                            }))
                            
                        # 2. Route raw audio to the frontend speakers
                        elif 'audioOutput' in event:
                            audio_bytes = base64.b64decode(event['audioOutput']['content'])
                            await self.ws.send_bytes(audio_bytes)
                            
                        # 3. Handle End of Turn
                        elif 'contentEnd' in event:
                            # When Bedrock finishes replying, tell the UI to stop spinning
                            await self.ws.send_text(json.dumps({"type": "turn_end"}))
                            
        except Exception as e:
            print(f"[{self.session_id}] Bedrock processing stopped: {e}")


@app.websocket("/ws/{session_id}")
async def websocket_endpoint(ws: WebSocket, session_id: str):
    await ws.accept()
    print(f"[{session_id}] Connected")
    
    bot = WSNovaSonic(ws, session_id)
    
    try:
        await bot.start_session()
        
        # Start listening to Bedrock in the background
        response_task = asyncio.create_task(bot.process_bedrock_responses())

        # Listen to the Frontend
        while True:
            msg = await ws.receive()
            if msg["type"] == "websocket.disconnect":
                break
                
            # Audio chunks arriving from frontend mic
            if msg.get("bytes"):
                if not bot.in_audio_turn:
                    await bot.start_audio_input()
                    bot.in_audio_turn = True
                await bot.send_audio_chunk(msg["bytes"])
                
            # Control messages from frontend
            elif msg.get("text"):
                try:
                    data = json.loads(msg["text"])
                    if data.get("type") == "end_of_turn" and bot.in_audio_turn:
                        await bot.end_audio_input()
                        bot.in_audio_turn = False
                except Exception:
                    pass

    except WebSocketDisconnect:
        print(f"[{session_id}] Disconnected")
    except Exception as e:
        print(f"[{session_id}] WS ERROR: {e}")
        traceback.print_exc()
    finally:
        bot.is_active = False