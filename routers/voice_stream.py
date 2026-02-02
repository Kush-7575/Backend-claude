"""
Voice Streaming Router for BrainMap (Claude Backend)
Real-time WebSocket-based voice transcription using Deepgram SDK.

Flow:
1. Client opens WebSocket connection with codec parameter
2. Client streams audio chunks (binary)
3. Backend forwards audio directly to Deepgram (Opus/PCM natively supported)
4. Backend streams back partial transcripts (JSON) as speech happens
5. On completion, final transcript is processed with Claude Agent

Adapted from old backend's voice_stream.py for Claude Agent SDK.
"""
import os
import json
import asyncio
import io
from typing import Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import BaseModel
import logging

logger = logging.getLogger("brainmap.voice")

router = APIRouter(prefix='/v1/voice', tags=['voice-stream'])

# Development user ID
DEV_USER_ID = "test-user"


def get_deepgram_api_key() -> str | None:
    """Get Deepgram API key at runtime (after .env is loaded by pydantic-settings)."""
    # Use settings (properly loads from .env via pydantic-settings)
    try:
        from core.config import settings
        if settings.DEEPGRAM_API_KEY:
            return settings.DEEPGRAM_API_KEY
    except Exception:
        pass
    # Fallback to direct env var
    return os.environ.get('DEEPGRAM_API_KEY')


class TranscriptMessage(BaseModel):
    """Message sent to client during streaming"""
    type: str  # 'partial', 'final', 'processing', 'complete', 'error'
    transcript: str = ""
    is_final: bool = False
    confidence: float = 0.0


async def decode_webm_to_pcm(webm_data: bytes, sample_rate: int = 16000) -> bytes:
    """Decode WebM audio to PCM using PyAV"""
    try:
        import av
        
        container = av.open(io.BytesIO(webm_data), format='webm')
        audio_stream = next((s for s in container.streams if s.type == 'audio'), None)
        if not audio_stream:
            return b''
        
        resampler = av.audio.resampler.AudioResampler(
            format='s16',
            layout='mono',
            rate=sample_rate
        )
        
        pcm_chunks = []
        for frame in container.decode(audio=0):
            resampled_frames = resampler.resample(frame)
            for resampled_frame in resampled_frames:
                frame_array = resampled_frame.to_ndarray()
                if frame_array.ndim > 1:
                    frame_array = frame_array.T.flatten()
                pcm_chunks.append(frame_array.tobytes())
        
        container.close()
        return b''.join(pcm_chunks)
        
    except Exception as e:
        logger.error(f"WebM decode error: {e}")
        return b''


# Agent runner will be injected at startup
_agent_runner = None
_session_manager = None


def set_dependencies(agent_runner=None, session_manager=None):
    """Set dependencies for voice router."""
    global _agent_runner, _session_manager
    _agent_runner = agent_runner
    _session_manager = session_manager


@router.websocket("/stream")
async def voice_stream(websocket: WebSocket):
    """
    WebSocket endpoint for real-time voice transcription.
    """
    try:
        logger.info(f"Incoming voice connection: path={websocket.url.path}")
        await websocket.accept()
        
        query_params = dict(websocket.query_params)
        context = query_params.get("context", "auto")
        session_id = query_params.get("session_id")
        uid = query_params.get("uid", DEV_USER_ID)
        codec = query_params.get("codec", "opus")
        sample_rate_str = query_params.get("sample_rate", "16000")
        sample_rate = int(sample_rate_str)
        language = query_params.get("language", "en")
        
        logger.info(f"Voice connection: uid={uid}, codec={codec}, sample_rate={sample_rate}")
        
        deepgram_key = get_deepgram_api_key()
        if not deepgram_key:
            logger.error("No Deepgram API key configured")
            await websocket.send_json({
                "type": "error",
                "message": "Voice transcription not configured. Add DEEPGRAM_API_KEY."
            })
            await websocket.close()
            return
        
        # WebM from browser needs special handling
        if codec == 'webm':
            await handle_webm_stream(websocket, context, session_id, uid, sample_rate, language)
        else:
            # PCM/Opus can stream directly
            await handle_realtime_stream(websocket, context, session_id, uid, codec, sample_rate, language)
            
    except Exception as e:
        logger.error(f"Voice endpoint error: {e}")
        try:
            await websocket.close(code=1011)
        except:
            pass


async def handle_realtime_stream(
    websocket: WebSocket,
    context: str,
    session_id: Optional[str],
    uid: str,
    codec: str,
    sample_rate: int,
    language: str
):
    """
    Real-time streaming for PCM/Opus audio using Deepgram SDK v5.
    Opus is sent DIRECTLY to Deepgram (no decoding needed).
    """
    from deepgram import AsyncDeepgramClient
    from deepgram.core.events import EventType
    
    # Map codec to Deepgram encoding
    codec_to_encoding = {
        'opus': 'opus',
        'pcm16': 'linear16',
        'pcm8': 'linear16',
        'linear16': 'linear16',
    }
    deepgram_encoding = codec_to_encoding.get(codec.lower(), 'linear16')
    
    logger.info(f"Starting Deepgram stream: codec={codec} → encoding={deepgram_encoding}")
    
    websocket_closed = False
    full_transcript = ""
    last_partial = ""
    stop_received = False
    
    async def safe_send_json(data: dict) -> bool:
        nonlocal websocket_closed
        if websocket_closed:
            return False
        try:
            await websocket.send_json(data)
            return True
        except:
            websocket_closed = True
            return False
    
    try:
        client = AsyncDeepgramClient(api_key=get_deepgram_api_key())
        
        # Create async live connection using v5 SDK async context manager
        async with client.listen.v1.connect(
            model="nova-2",
            language=language,
            punctuate="true",
            smart_format="true",
            encoding=deepgram_encoding,
            sample_rate=str(sample_rate),
            channels="1",
            interim_results="true",
            endpointing="0",
        ) as connection:
            
            # Event handler for transcripts
            async def on_message(message):
                nonlocal full_transcript, last_partial
                if websocket_closed:
                    return
                try:
                    if hasattr(message, 'channel') and message.channel:
                        alts = message.channel.alternatives
                        if alts and len(alts) > 0:
                            alt = alts[0]
                            transcript = getattr(alt, 'transcript', '')
                            if transcript:
                                is_final = getattr(message, 'is_final', False)
                                confidence = getattr(alt, 'confidence', 0.0)
                                
                                await safe_send_json({
                                    "type": "final" if is_final else "partial",
                                    "transcript": transcript,
                                    "is_final": is_final,
                                    "confidence": confidence
                                })
                                
                                if is_final:
                                    full_transcript += transcript + " "
                                    logger.info(f"Final: {transcript}")
                                else:
                                    last_partial = transcript
                except Exception as e:
                    logger.error(f"on_message error: {e}")
            
            async def on_error(error):
                logger.error(f"Deepgram error: {error}")
                await safe_send_json({"type": "error", "message": str(error)})
            
            # Register handlers
            connection.on(EventType.MESSAGE, on_message)
            connection.on(EventType.ERROR, on_error)
            
            logger.info("Deepgram connection established")
            await safe_send_json({"type": "connected", "message": "Listening..."})
            
            # Start the background listener
            listen_task = asyncio.create_task(connection.start_listening())
            
            # Stream audio from client to Deepgram
            chunk_count = 0
            
            try:
                while not websocket_closed and not stop_received:
                    try:
                        data = await asyncio.wait_for(websocket.receive(), timeout=30.0)
                    except asyncio.TimeoutError:
                        logger.warning("Voice connection timeout")
                        break
                    
                    if data.get("type") == "websocket.disconnect":
                        websocket_closed = True
                        break
                    
                    if "bytes" in data:
                        audio_data = data["bytes"]
                        if audio_data:
                            await connection._send(audio_data)
                            chunk_count += 1
                            if chunk_count % 50 == 0:
                                logger.info(f"Sent {chunk_count} chunks")
                    
                    elif "text" in data:
                        try:
                            msg = json.loads(data["text"])
                            if msg.get("type") == "stop":
                                logger.info("Stop signal received")
                                stop_received = True
                        except:
                            pass
                            
            except WebSocketDisconnect:
                websocket_closed = True
            
            # Cancel listen task
            listen_task.cancel()
            try:
                await listen_task
            except asyncio.CancelledError:
                pass
        
        # Process final transcript (after context manager closes connection)
        if not websocket_closed:
            full_transcript = full_transcript.strip()
            
            # Use last partial as fallback
            if not full_transcript and last_partial:
                full_transcript = last_partial.strip()
            
            if full_transcript:
                await process_transcript_with_agent(
                    websocket, full_transcript, context, session_id, uid
                )
            else:
                await safe_send_json({
                    "type": "complete",
                    "transcript": "",
                    "message": "No speech detected",
                    "responded": False
                })
    
    except Exception as e:
        logger.error(f"Stream error: {e}")
        await safe_send_json({"type": "error", "message": str(e)})
    
    finally:
        if not websocket_closed:
            try:
                await websocket.close()
            except:
                pass


async def handle_webm_stream(
    websocket: WebSocket,
    context: str,
    session_id: Optional[str],
    uid: str,
    sample_rate: int,
    language: str
):
    """Handle WebM audio from browser - accumulate then decode."""
    from deepgram import DeepgramClient, LiveTranscriptionEvents, LiveOptions
    
    logger.info("Starting WebM handler")
    
    webm_buffer = bytearray()
    full_transcript = ""
    last_pcm_length = 0
    
    try:
        client = DeepgramClient(api_key=get_deepgram_api_key())
        
        options = LiveOptions(
            model="nova-2",
            language=language,
            punctuate=True,
            smart_format=True,
            encoding="linear16",
            sample_rate=sample_rate,
            channels=1,
            interim_results=True,
        )
        
        connection = client.listen.live.v("1")
        
        async def on_message(self, result, **kwargs):
            nonlocal full_transcript
            try:
                transcript = result.channel.alternatives[0].transcript
                if transcript:
                    is_final = result.is_final
                    await websocket.send_json({
                        "type": "final" if is_final else "partial",
                        "transcript": transcript,
                        "is_final": is_final
                    })
                    if is_final:
                        full_transcript += transcript + " "
            except Exception as e:
                logger.error(f"on_message error: {e}")
        
        connection.on(LiveTranscriptionEvents.Transcript, on_message)
        
        if not await connection.start(options):
            await websocket.send_json({"type": "error", "message": "Failed to connect"})
            return
        
        await websocket.send_json({"type": "connected", "message": "Listening..."})
        
        # Receive WebM chunks
        chunk_count = 0
        decode_interval = 10
        
        try:
            while True:
                data = await websocket.receive()
                
                if "bytes" in data:
                    webm_buffer.extend(data["bytes"])
                    chunk_count += 1
                    
                    if chunk_count % decode_interval == 0:
                        try:
                            pcm_data = await decode_webm_to_pcm(bytes(webm_buffer), sample_rate)
                            if pcm_data and len(pcm_data) > last_pcm_length:
                                await connection.send(pcm_data[last_pcm_length:])
                                last_pcm_length = len(pcm_data)
                        except:
                            pass
                
                elif "text" in data:
                    try:
                        msg = json.loads(data["text"])
                        if msg.get("type") == "stop":
                            break
                    except:
                        pass
                        
        except WebSocketDisconnect:
            pass
        
        # Final decode
        if webm_buffer:
            try:
                pcm_data = await decode_webm_to_pcm(bytes(webm_buffer), sample_rate)
                if pcm_data and len(pcm_data) > last_pcm_length:
                    await connection.send(pcm_data[last_pcm_length:])
            except:
                pass
        
        await connection.finish()
        await asyncio.sleep(0.3)
        
        # Process
        full_transcript = full_transcript.strip()
        if full_transcript:
            await process_transcript_with_agent(
                websocket, full_transcript, context, session_id, uid
            )
        else:
            await websocket.send_json({
                "type": "complete",
                "transcript": "",
                "message": "No speech detected"
            })
    
    except Exception as e:
        logger.error(f"WebM error: {e}")
        await websocket.send_json({"type": "error", "message": str(e)})
    
    finally:
        try:
            await websocket.close()
        except:
            pass


async def process_transcript_with_agent(
    websocket: WebSocket,
    transcript: str,
    context: str,
    session_id: Optional[str],
    uid: str
):
    """
    Process the final transcript using Claude Agent.
    Sends WebSocket messages for status and response.
    """
    async def safe_send_json(data: dict) -> bool:
        try:
            await websocket.send_json(data)
            return True
        except Exception:
            return False

    await safe_send_json({
        "type": "processing",
        "transcript": transcript,
        "message": "Thinking..."
    })
    
    try:
        if _agent_runner is None or _session_manager is None:
            # Fallback: just echo transcript
            await safe_send_json({
                "type": "chat_response",
                "transcript": transcript,
                "ai_response": f"I heard: {transcript}",
                "actions": [],
                "responded": True
            })
            await safe_send_json({
                "type": "complete",
                "transcript": transcript,
                "responded": True
            })
            return
        
        # Get or create session
        if session_id:
            session = await _session_manager.load_session(session_id, uid)
            if not session:
                session = _session_manager.create_session(uid)
        else:
            session = _session_manager.create_session(uid)
        
        # Run through agent with streaming
        full_response = []
        collected_actions = []
        
        async for chunk in _agent_runner.run_stream(session, transcript):
            if chunk.type == "text":
                full_response.append(chunk.content)
                await safe_send_json({
                    "type": "ai_chunk",
                    "content": chunk.content
                })
            elif chunk.type == "tool_start":
                await safe_send_json({
                    "type": "thinking",
                    "message": f"Using {chunk.content}..."
                })
            elif chunk.type == "tool_end":
                if chunk.metadata:
                    await safe_send_json({
                        "type": "action",
                        "action": chunk.metadata
                    })
                    collected_actions.append(chunk.metadata)
        
        response_text = "".join(full_response)
        
        await safe_send_json({
            "type": "chat_response",
            "transcript": transcript,
            "ai_response": response_text or "Done!",
            "actions": collected_actions,
            "session_id": session.id,
            "responded": True
        })
        
        await safe_send_json({
            "type": "complete",
            "transcript": transcript,
            "responded": True
        })
        
        # Save session in background (Clawdbot fire-and-forget pattern)
        # This reduces perceived latency by not waiting for DB write
        _session_manager.save_session_background(session)
    
    except Exception as e:
        logger.error(f"Agent processing error: {e}")
        await safe_send_json({
            "type": "error",
            "message": str(e)
        })
