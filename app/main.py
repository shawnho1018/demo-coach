import os
import json
import logging
import asyncio
import ssl
import certifi
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
import google.auth
import google.auth.transport.requests
import websockets

def load_dotenv(dotenv_path: str = ".env") -> None:
    """Loads environment variables from a .env file without external dependencies."""
    if os.path.exists(dotenv_path):
        try:
            with open(dotenv_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    # Skip comments and empty lines
                    if not line or line.startswith("#"):
                        continue
                    if "=" in line:
                        key, val = line.split("=", 1)
                        key = key.strip()
                        val = val.strip()
                        # Strip surrounding quotes if present
                        if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
                            val = val[1:-1]
                        if key not in os.environ:
                            os.environ[key] = val
        except Exception as e:
            print(f"Error loading .env file: {e}")

# Load environment variables from local .env if present
load_dotenv()

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("gecx-gateway")

app = FastAPI()

# Setup templates directory
templates = Jinja2Templates(directory="app/templates")

# Configure GECX details
# (We default to user specified values but allow override via environment variables)
GCP_PROJECT = os.getenv("GCP_PROJECT", "cfh-de-chatbot-lab")
GCP_LOCATION = os.getenv("GCP_LOCATION", "us")
#GECX_APP_ID = os.getenv("GECX_APP_ID", "edf026cc-7117-44b0-aee9-3880ddf4c8ec")
#GECX_DEPLOYMENT_ID = os.getenv("GECX_DEPLOYMENT_ID", "e30868d4-8787-474c-aa0a-6c0ce89b49bf")
GECX_APP_ID = os.getenv("GECX_APP_ID", "c806756f-9855-413b-b552-4d741b296572")
GECX_DEPLOYMENT_ID = os.getenv("GECX_DEPLOYMENT_ID", "17348383-2b82-41b3-a257-badcaf3a4511")

# Construct resource names
APP_PATH = f"projects/{GCP_PROJECT}/locations/{GCP_LOCATION}/apps/{GECX_APP_ID}"
DEPLOYMENT_PATH = f"{APP_PATH}/deployments/{GECX_DEPLOYMENT_ID}"
GECX_BIDI_URI = f"wss://ces.googleapis.com/ws/google.cloud.ces.v1.SessionService/BidiRunSession/locations/{GCP_LOCATION}"

def get_oauth_token():
    """Generates a fresh Google Cloud OAuth2 token for authentication."""
    try:
        credentials, _ = google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
        auth_request = google.auth.transport.requests.Request()
        credentials.refresh(auth_request)
        return credentials.token
    except Exception as e:
        logger.error(f"Failed to generate OAuth token: {e}")
        raise RuntimeError("Google Cloud Authentication failed. Please ensure ADC is configured.") from e

@app.get("/", response_class=HTMLResponse)
async def get_index(request: Request):
    """Serves the frontend client interface."""
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "project_id": GCP_PROJECT,
            "location": GCP_LOCATION,
            "app_id": GECX_APP_ID,
            "deployment_id": GECX_DEPLOYMENT_ID
        }
    )

@app.get("/healthz")
async def health_check():
    """Basic health check endpoint."""
    return {"status": "healthy"}

@app.websocket("/ws/{session_id}")
async def websocket_endpoint(websocket: WebSocket, session_id: str):
    """Establishes client connection and proxies it to the GECX agent."""
    await websocket.accept()
    logger.info(f"Client connected for session: {session_id}")

    try:
        token = get_oauth_token()
    except Exception as e:
        logger.error(f"Auth error during websocket init: {e}")
        await websocket.close(code=1008, reason="Authentication failed")
        return

    headers = {"Authorization": f"Bearer {token}"}
    session_name = f"{APP_PATH}/sessions/{session_id}"

    try:
        ssl_context = ssl.create_default_context(cafile=certifi.where())
        async with websockets.connect(GECX_BIDI_URI, additional_headers=headers, ssl=ssl_context) as gecx_ws:
            logger.info("Successfully connected to GECX BidiRunSession stream")

            # 1. Send the initialization config message
            init_message = {
                "config": {
                    "session": session_name,
                    "deployment": DEPLOYMENT_PATH,
                    "inputAudioConfig": {
                        "audioEncoding": "LINEAR16",
                        "sampleRateHertz": 16000
                    },
                    "outputAudioConfig": {
                        "audioEncoding": "LINEAR16",
                        "sampleRateHertz": 16000
                    }
                }
            }
            await gecx_ws.send(json.dumps(init_message))
            logger.info("Sent session config initialization to GECX")

            async def upstream_listener():
                """Listens to client messages and relays them to GECX."""
                try:
                    async for message in websocket.iter_json():
                        msg_type = message.get("type")
                        
                        if msg_type == "text":
                            query_text = message.get("text", "")
                            realtime_message = {
                                "realtimeInput": {
                                    "text": query_text
                                }
                            }
                            await gecx_ws.send(json.dumps(realtime_message))
                            logger.info(f"Relayed text query: {query_text}")
                            
                        elif msg_type == "audio":
                            audio_b64 = message.get("data", "")
                            realtime_message = {
                                "realtimeInput": {
                                    "audio": audio_b64
                                }
                            }
                            await gecx_ws.send(json.dumps(realtime_message))
                except WebSocketDisconnect:
                    logger.info("Client disconnected from WebSocket")
                except Exception as e:
                    logger.error(f"Error in upstream listener: {e}")

            async def downstream_listener():
                """Listens to GECX responses and relays them to client."""
                try:
                    async for message_str in gecx_ws:
                        message = json.loads(message_str)
                        
                        # Handle different server message types (supporting both camelCase and snake_case)
                        recognition_result = message.get("recognitionResult") or message.get("recognition_result")
                        session_output = message.get("sessionOutput") or message.get("session_output")
                        interruption_signal = message.get("interruptionSignal") or message.get("interruption_signal")
                        end_session = message.get("endSession") or message.get("end_session")
                        
                        if recognition_result:
                            transcript = recognition_result.get("transcript", "")
                            await websocket.send_json({
                                "type": "recognition_result",
                                "transcript": transcript
                            })
                            logger.info(f"Speech transcript: {transcript}")
                            
                        elif session_output:
                            # Forward text output
                            text = session_output.get("text")
                            if text:
                                await websocket.send_json({
                                    "type": "text",
                                    "text": text
                                })
                                logger.info(f"Agent text response: {text}")
                                
                            # Forward audio output
                            audio = session_output.get("audio")
                            if audio:
                                await websocket.send_json({
                                    "type": "audio",
                                    "audio": audio
                                })
                                
                            # Forward turn completed status
                            turn_completed = session_output.get("turnCompleted") or session_output.get("turn_completed")
                            if turn_completed:
                                await websocket.send_json({
                                    "type": "turn_completed"
                                })
                                logger.info("Turn completed signal received")
                                
                        elif interruption_signal:
                            await websocket.send_json({
                                "type": "interruption"
                            })
                            logger.info("Interruption signal received (User Barge-in)")
                            
                        elif end_session:
                            await websocket.send_json({
                                "type": "end_session"
                            })
                            logger.info("GECX session ended")
                            break
                            
                except Exception as e:
                    logger.error(f"Error in downstream listener: {e}")

            # Run both upstream and downstream concurrently
            await asyncio.gather(upstream_listener(), downstream_listener())

    except websockets.exceptions.ConnectionClosed as e:
        logger.error(f"GECX connection closed: {e}")
        await websocket.send_json({"type": "error", "message": "GECX agent connection lost"})
    except Exception as e:
        logger.error(f"Unexpected error in proxy session: {e}")
        await websocket.send_json({"type": "error", "message": str(e)})
    finally:
        logger.info(f"Websocket session terminated: {session_id}")
        try:
            await websocket.close()
        except Exception:
            pass

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8080)))
