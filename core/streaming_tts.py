"""
Streaming TTS Provider using Deepgram WebSocket API

Provides ultra-low-latency text-to-speech using Deepgram's streaming TTS.
Supports progressive text input - you can send text chunks as Claude
generates them, and receive audio immediately.

Key advantages over OpenAI TTS:
- True progressive streaming (send text word-by-word)
- ~100-200ms time-to-first-audio
- Same provider as STT (unified billing)
"""
import asyncio
import logging
from typing import AsyncIterator, Optional, Literal
from dataclasses import dataclass

from deepgram import AsyncDeepgramClient
from deepgram.extensions.types.sockets import (
    SpeakV1TextMessage,
    SpeakV1ControlMessage,
)

from core.config import settings

logger = logging.getLogger("brainmap.tts")


# Deepgram Aura TTS voices - natural conversational voices
DeepgramVoice = Literal[
    "aura-asteria-en",    # Female, warm & expressive (recommended)
    "aura-luna-en",       # Female, calm & soothing
    "aura-stella-en",     # Female, clear & articulate
    "aura-athena-en",     # Female, authoritative
    "aura-hera-en",       # Female, friendly
    "aura-orion-en",      # Male, deep & resonant
    "aura-arcas-en",      # Male, warm & approachable
    "aura-perseus-en",    # Male, clear & professional
    "aura-angus-en",      # Male, Scottish accent
    "aura-orpheus-en",    # Male, expressive
    "aura-helios-en",     # Male, bright & energetic
    "aura-zeus-en",       # Male, authoritative
]

# Audio encoding formats
TTSEncoding = Literal["linear16", "mulaw", "alaw", "mp3", "opus", "flac", "aac"]


@dataclass
class DeepgramTTSConfig:
    """Configuration for Deepgram TTS."""
    api_key: str
    model: DeepgramVoice = "aura-asteria-en"  # Best conversational voice
    encoding: TTSEncoding = "linear16"  # Raw PCM for true real-time streaming
    sample_rate: int = 24000  # 24kHz for good quality


class DeepgramStreamingTTS:
    """
    Streaming TTS using Deepgram's WebSocket API.
    
    Supports progressive text input - send text as Claude generates it,
    receive audio immediately. Much lower latency than batch TTS.
    
    Usage:
        async with DeepgramStreamingTTS() as tts:
            # Send text progressively
            await tts.send_text("Hello, ")
            await tts.send_text("how are you today?")
            await tts.flush()  # Signal end of text
            
            # Receive audio chunks
            async for audio_chunk in tts.audio_stream():
                yield audio_chunk
    """
    
    def __init__(
        self,
        api_key: Optional[str] = None,
        model: DeepgramVoice = "aura-asteria-en",
        encoding: TTSEncoding = "linear16",  # Raw PCM for zero-latency streaming
        sample_rate: int = 24000,
    ):
        self.api_key = api_key or settings.DEEPGRAM_API_KEY
        if not self.api_key:
            raise ValueError("Deepgram API key required for TTS")
        
        self.model = model
        self.encoding = encoding
        self.sample_rate = sample_rate
        
        self._client: Optional[AsyncDeepgramClient] = None
        self._socket = None
        self._audio_queue: asyncio.Queue[bytes] = asyncio.Queue()
        self._receive_task: Optional[asyncio.Task] = None
        self._connected = False
        self._finished = False
    
    async def connect(self) -> None:
        """Establish WebSocket connection to Deepgram TTS."""
        if self._connected:
            return
        
        self._client = AsyncDeepgramClient(api_key=self.api_key)
        
        # Connect to TTS WebSocket
        self._socket = await self._client.speak.v1.connect(
            model=self.model,
            encoding=self.encoding,
            sample_rate=str(self.sample_rate),
        ).__aenter__()
        
        self._connected = True
        self._finished = False
        
        # Start receiving audio in background
        self._receive_task = asyncio.create_task(self._receive_audio())
        
        logger.debug(f"Deepgram TTS connected: model={self.model}, encoding={self.encoding}")
    
    async def _receive_audio(self) -> None:
        """Background task to receive audio chunks from Deepgram."""
        try:
            async for message in self._socket:
                if isinstance(message, bytes):
                    # Binary audio chunk - add to queue
                    await self._audio_queue.put(message)
                else:
                    # JSON message (metadata, control response)
                    logger.debug(f"TTS control message: {message}")
        except Exception as e:
            logger.error(f"TTS receive error: {e}")
        finally:
            # Signal end of audio
            await self._audio_queue.put(None)
    
    async def send_text(self, text: str) -> None:
        """
        Send text to be converted to speech.
        
        Can be called multiple times for progressive streaming.
        Audio will start being generated immediately.
        """
        if not self._connected:
            await self.connect()
        
        if not text.strip():
            return
        
        message = SpeakV1TextMessage(type="Speak", text=text)
        await self._socket.send_text(message)
        logger.debug(f"TTS sent: {text[:50]}...")
    
    async def flush(self) -> None:
        """
        Signal that all text has been sent.
        
        Deepgram will generate remaining audio and close the stream.
        """
        if not self._connected:
            return
        
        message = SpeakV1ControlMessage(type="Flush")
        await self._socket.send_control(message)
        logger.debug("TTS flushed")
    
    async def close(self) -> None:
        """Close the TTS connection."""
        if not self._connected:
            return
        
        try:
            message = SpeakV1ControlMessage(type="Close")
            await self._socket.send_control(message)
        except Exception:
            pass
        
        self._connected = False
        self._finished = True
        
        if self._receive_task:
            self._receive_task.cancel()
            try:
                await self._receive_task
            except asyncio.CancelledError:
                pass
        
        logger.debug("TTS connection closed")
    
    async def audio_stream(self, timeout: float = 10.0) -> AsyncIterator[bytes]:
        """
        Yield audio chunks as they become available.
        
        Call this while sending text to stream audio in real-time.
        Includes timeout protection to prevent indefinite blocking.
        
        Args:
            timeout: Max seconds to wait for each chunk (default 10s)
        """
        while True:
            try:
                chunk = await asyncio.wait_for(
                    self._audio_queue.get(),
                    timeout=timeout
                )
            except asyncio.TimeoutError:
                logger.warning(f"TTS audio stream timeout after {timeout}s")
                break
            if chunk is None:
                # End of stream
                break
            yield chunk
    
    async def __aenter__(self):
        await self.connect()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()


class ProgressiveTTSStreamer:
    """
    Streams TTS for text that's being generated by Claude.
    
    Detects sentence boundaries and sends complete sentences to TTS
    for immediate audio generation. This minimizes time-to-first-audio
    while the agent is still generating text.
    
    Usage:
        streamer = ProgressiveTTSStreamer()
        
        # As Claude generates text:
        for chunk in claude_stream:
            streamer.add_text(chunk)
            async for audio in streamer.get_audio():
                yield audio
        
        # When Claude is done:
        streamer.finish()
        async for audio in streamer.get_audio():
            yield audio
    """
    
    def __init__(
        self,
        api_key: Optional[str] = None,
        model: DeepgramVoice = "aura-asteria-en",
        min_sentence_chars: int = 15,  # Minimum chars before looking for break
        sentence_endings: str = ".!?;",
    ):
        self.api_key = api_key or settings.DEEPGRAM_API_KEY
        self.model = model
        self.min_sentence_chars = min_sentence_chars
        self.sentence_endings = sentence_endings
        
        self._buffer = ""
        self._tts: Optional[DeepgramStreamingTTS] = None
        self._started = False
        self._finished = False
        self._aborted = False
    
    async def _ensure_connected(self) -> None:
        """Ensure TTS connection is established."""
        if self._tts is None:
            self._tts = DeepgramStreamingTTS(
                api_key=self.api_key,
                model=self.model,
            )
            await self._tts.connect()
            self._started = True
    
    def add_text(self, text: str) -> None:
        """
        Add text chunk from Claude stream.
        
        Text is buffered until a sentence boundary is detected.
        """
        if self._aborted or self._finished:
            return
        self._buffer += text
    
    async def get_pending_audio(self) -> AsyncIterator[bytes]:
        """
        Check for complete sentences and yield any available audio.
        
        Call this after each add_text() to stream audio progressively.
        """
        if self._aborted:
            return
        
        # Check for sentence boundary
        await self._maybe_send_sentence()
        
        # Yield any available audio (non-blocking)
        if self._tts:
            while not self._tts._audio_queue.empty():
                chunk = await self._tts._audio_queue.get()
                if chunk is None:
                    break
                yield chunk
    
    async def _maybe_send_sentence(self) -> None:
        """Send complete sentences to TTS."""
        if len(self._buffer) < self.min_sentence_chars:
            return
        
        # Find last sentence ending
        best_break = -1
        for i, char in enumerate(self._buffer):
            if char in self.sentence_endings:
                # Check for space after (to avoid "Dr." or "3.14")
                if i + 1 < len(self._buffer) and self._buffer[i + 1] in " \n":
                    best_break = i + 1
                elif i + 1 == len(self._buffer):
                    best_break = i + 1
        
        if best_break >= self.min_sentence_chars:
            sentence = self._buffer[:best_break].strip()
            self._buffer = self._buffer[best_break:]
            
            if sentence:
                await self._ensure_connected()
                await self._tts.send_text(sentence + " ")
                logger.debug(f"Progressive TTS: sent '{sentence[:30]}...'")
    
    async def finish(self) -> AsyncIterator[bytes]:
        """
        Signal that all text has been added.
        
        Sends any remaining buffer and yields all remaining audio.
        """
        if self._aborted:
            return
        
        self._finished = True
        
        # Send remaining buffer
        if self._buffer.strip():
            await self._ensure_connected()
            await self._tts.send_text(self._buffer.strip())
            self._buffer = ""
        
        # Flush and close
        if self._tts:
            await self._tts.flush()
            
            # Yield remaining audio
            async for chunk in self._tts.audio_stream():
                yield chunk
            
            await self._tts.close()
    
    async def abort(self) -> None:
        """Abort TTS stream (for barge-in)."""
        self._aborted = True
        self._buffer = ""
        
        if self._tts:
            await self._tts.close()
            self._tts = None


# =============================================================================
# Singleton / Factory
# =============================================================================

_tts_instance: Optional[DeepgramStreamingTTS] = None


async def get_tts_connection() -> DeepgramStreamingTTS:
    """Get a new TTS connection (for one-shot use)."""
    tts = DeepgramStreamingTTS()
    await tts.connect()
    return tts


def create_progressive_streamer() -> ProgressiveTTSStreamer:
    """Create a new progressive TTS streamer for agent output."""
    return ProgressiveTTSStreamer()


# =============================================================================
# Simple API for voice_stream.py
# =============================================================================

async def stream_tts(text: str) -> AsyncIterator[bytes]:
    """
    Simple one-shot TTS streaming.
    
    For progressive streaming during agent execution,
    use ProgressiveTTSStreamer instead.
    """
    async with DeepgramStreamingTTS() as tts:
        await tts.send_text(text)
        await tts.flush()
        
        async for chunk in tts.audio_stream():
            yield chunk
