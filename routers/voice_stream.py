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
    Process the final transcript using Claude Agent with progressive TTS.
    
    Key improvement: TTS starts as soon as Claude generates the first sentence,
    rather than waiting for the complete response. This reduces time-to-first-audio
    from ~2-3s to ~500-800ms.
    
    Flow:
    1. Claude starts generating text
    2. When a sentence is complete, send it to Deepgram TTS
    3. Stream audio chunks back to client immediately
    4. Continue with next sentence
    """
    from core.streaming_tts import create_progressive_streamer, stream_tts
    from core.config import settings
    
    async def safe_send_json(data: dict) -> bool:
        try:
            await websocket.send_json(data)
            return True
        except Exception:
            return False
    
    async def safe_send_bytes(data: bytes) -> bool:
        try:
            await websocket.send_bytes(data)
            return True
        except Exception:
            return False

    await safe_send_json({
        "type": "processing",
        "transcript": transcript,
        "message": "Thinking..."
    })
    
    # Check if TTS is available
    tts_enabled = bool(settings.DEEPGRAM_API_KEY)
    
    try:
        if _agent_runner is None or _session_manager is None:
            # Fallback: just echo transcript
            fallback_text = f"I heard: {transcript}"
            await safe_send_json({
                "type": "chat_response",
                "transcript": transcript,
                "ai_response": fallback_text,
                "actions": [],
                "responded": True
            })
            # Stream TTS for fallback
            if tts_enabled:
                await stream_tts_simple(websocket, fallback_text, safe_send_json, safe_send_bytes)
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
        
        # Create progressive TTS streamer (Deepgram WebSocket)
        tts_streamer = create_progressive_streamer() if tts_enabled else None
        tts_started = False
        
        # Run through agent with streaming + progressive TTS
        full_response = []
        collected_actions = []
        
        async for chunk in _agent_runner.run_stream(session, transcript):
            if chunk.type == "text":
                text_chunk = chunk.content
                full_response.append(text_chunk)
                
                # Send text to client
                await safe_send_json({
                    "type": "ai_chunk",
                    "content": text_chunk
                })
                
                # Feed text to progressive TTS
                if tts_streamer:
                    tts_streamer.add_text(text_chunk)
                    
                    # Stream any available audio immediately
                    if not tts_started:
                        await safe_send_json({"type": "tts_start"})
                        tts_started = True
                    
                    async for audio_chunk in tts_streamer.get_pending_audio():
                        await safe_send_bytes(audio_chunk)
                        
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
        
        # Send complete response
        await safe_send_json({
            "type": "chat_response",
            "transcript": transcript,
            "ai_response": response_text or "Done!",
            "actions": collected_actions,
            "session_id": session.id,
            "responded": True
        })
        
        # Finish TTS and stream remaining audio
        if tts_streamer:
            if not tts_started:
                await safe_send_json({"type": "tts_start"})
            
            async for audio_chunk in tts_streamer.finish():
                await safe_send_bytes(audio_chunk)
            
            await safe_send_json({"type": "tts_end"})
            logger.info(f"Progressive TTS complete for: {response_text[:50]}...")
        
        await safe_send_json({
            "type": "complete",
            "transcript": transcript,
            "responded": True
        })
        
        # Save session in background (fire-and-forget pattern)
        _session_manager.save_session_background(session)
    
    except Exception as e:
        logger.error(f"Agent processing error: {e}")
        # Abort TTS if running
        if 'tts_streamer' in dir() and tts_streamer:
            await tts_streamer.abort()
        await safe_send_json({
            "type": "error",
            "message": str(e)
        })


async def stream_tts_simple(
    websocket: WebSocket,
    text: str,
    safe_send_json,
    safe_send_bytes
):
    """
    Simple one-shot TTS streaming using Deepgram.
    
    For progressive streaming during agent execution, use the
    ProgressiveTTSStreamer in process_transcript_with_agent.
    
    Sends:
    - {"type": "tts_start"} when TTS begins
    - Binary audio chunks (MP3)
    - {"type": "tts_end"} when TTS completes
    """
    from core.config import settings
    
    # Check if TTS is enabled
    if not settings.DEEPGRAM_API_KEY:
        logger.warning("TTS disabled: no DEEPGRAM_API_KEY")
        return
    
    try:
        from core.streaming_tts import stream_tts
        
        await safe_send_json({"type": "tts_start"})
        
        # Stream audio chunks
        chunk_count = 0
        async for audio_chunk in stream_tts(text):
            await safe_send_bytes(audio_chunk)
            chunk_count += 1
        
        logger.info(f"TTS complete: {chunk_count} audio chunks sent")
        await safe_send_json({"type": "tts_end"})
        
    except Exception as e:
        logger.error(f"TTS streaming error: {e}")
        await safe_send_json({
            "type": "tts_error",
            "message": str(e)
        })
