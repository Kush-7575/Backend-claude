"""
Streaming TTS Provider (Clawdbot Pattern)

Provides streaming text-to-speech using OpenAI's tts-1 API.
Audio chunks are yielded as soon as they're available for
immediate playback.

Based on Clawdbot's TTS implementation (src/tts/tts.ts)
"""
import asyncio
import logging
from typing import AsyncIterator, Optional, Literal
from dataclasses import dataclass

import httpx

from core.config import settings

logger = logging.getLogger("brainmap.tts")


# OpenAI TTS voices
TTSVoice = Literal["alloy", "echo", "fable", "onyx", "nova", "shimmer"]

# Audio formats
TTSFormat = Literal["mp3", "opus", "aac", "flac", "wav", "pcm"]


@dataclass
class TTSConfig:
    """Configuration for TTS."""
    api_key: str
    model: str = "tts-1"  # tts-1 (fast) or tts-1-hd (quality)
    voice: TTSVoice = "alloy"
    format: TTSFormat = "mp3"
    speed: float = 1.0  # 0.25 to 4.0


class StreamingTTSProvider:
    """
    Streaming TTS using OpenAI's API.
    
    Streams audio chunks as they become available for
    minimal time-to-first-audio.
    """
    
    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = "tts-1",
        voice: TTSVoice = "alloy",
        format: TTSFormat = "mp3",
        speed: float = 1.0,
    ):
        self.api_key = api_key or settings.OPENAI_API_KEY
        if not self.api_key:
            raise ValueError("OpenAI API key required for TTS")
        
        self.model = model
        self.voice = voice
        self.format = format
        self.speed = speed
        
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(30.0, connect=10.0),
        )
    
    async def stream_speech(
        self,
        text: str,
        voice: Optional[TTSVoice] = None,
        speed: Optional[float] = None,
    ) -> AsyncIterator[bytes]:
        """
        Stream audio chunks for the given text.
        
        Args:
            text: Text to convert to speech
            voice: Override default voice
            speed: Override default speed
            
        Yields:
            Audio chunks (in configured format)
        """
        if not text.strip():
            return
        
        voice = voice or self.voice
        speed = speed if speed is not None else self.speed
        
        url = "https://api.openai.com/v1/audio/speech"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "input": text,
            "voice": voice,
            "response_format": self.format,
            "speed": speed,
        }
        
        try:
            async with self._client.stream(
                "POST",
                url,
                headers=headers,
                json=payload,
            ) as response:
                if response.status_code != 200:
                    error_text = await response.aread()
                    logger.error(f"TTS API error: {response.status_code} - {error_text}")
                    return
                
                # Stream audio chunks
                async for chunk in response.aiter_bytes(chunk_size=4096):
                    if chunk:
                        yield chunk
                        
        except httpx.TimeoutException as e:
            logger.error(f"TTS timeout: {e}")
        except Exception as e:
            logger.error(f"TTS error: {e}")
    
    async def speak(self, text: str, voice: Optional[TTSVoice] = None) -> bytes:
        """
        Convert text to speech and return complete audio.
        
        For streaming playback, use stream_speech() instead.
        """
        chunks = []
        async for chunk in self.stream_speech(text, voice=voice):
            chunks.append(chunk)
        return b"".join(chunks)
    
    async def close(self) -> None:
        """Close the HTTP client."""
        await self._client.aclose()


class ChunkedTTSStreamer:
    """
    Streams TTS for text that's still being generated.
    
    Accumulates text until a natural breakpoint (sentence end),
    then streams TTS for that chunk while accumulating more.
    This minimizes time-to-first-audio while agent is still thinking.
    
    Based on Clawdbot's chunked TTS pattern.
    """
    
    def __init__(
        self,
        tts_provider: StreamingTTSProvider,
        min_chunk_chars: int = 20,
        sentence_endings: str = ".!?;:",
    ):
        self.tts = tts_provider
        self.min_chunk_chars = min_chunk_chars
        self.sentence_endings = sentence_endings
        
        self._buffer = ""
        self._audio_queue: asyncio.Queue[bytes] = asyncio.Queue()
        self._text_complete = False
        self._tts_task: Optional[asyncio.Task] = None
        self._aborted = False
    
    def add_text(self, text: str) -> None:
        """Add text chunk from the agent stream."""
        if self._aborted:
            return
        self._buffer += text
        
        # Check if we have a complete sentence to speak
        self._maybe_start_tts()
    
    def _maybe_start_tts(self) -> None:
        """Start TTS if we have enough text at a natural breakpoint."""
        if len(self._buffer) < self.min_chunk_chars:
            return
        
        # Find last sentence ending
        best_break = -1
        for i, char in enumerate(self._buffer):
            if char in self.sentence_endings:
                best_break = i
        
        if best_break >= self.min_chunk_chars - 1:
            # Extract the chunk to speak
            chunk = self._buffer[:best_break + 1].strip()
            self._buffer = self._buffer[best_break + 1:]
            
            if chunk:
                # Queue TTS for this chunk
                asyncio.create_task(self._stream_chunk_to_queue(chunk))
    
    async def _stream_chunk_to_queue(self, text: str) -> None:
        """Stream TTS audio into the queue."""
        try:
            async for audio in self.tts.stream_speech(text):
                if self._aborted:
                    break
                await self._audio_queue.put(audio)
        except Exception as e:
            logger.error(f"Chunked TTS error: {e}")
    
    def finish_text(self) -> None:
        """Signal that all text has been added."""
        self._text_complete = True
        
        # Speak any remaining buffer
        if self._buffer.strip():
            asyncio.create_task(self._stream_chunk_to_queue(self._buffer.strip()))
            self._buffer = ""
    
    async def stream_audio(self) -> AsyncIterator[bytes]:
        """
        Yield audio chunks as they become available.
        
        Call this while add_text() is being called from another task.
        """
        while True:
            try:
                # Wait for audio with timeout
                audio = await asyncio.wait_for(
                    self._audio_queue.get(),
                    timeout=0.1
                )
                yield audio
            except asyncio.TimeoutError:
                # Check if we're done
                if self._text_complete and self._audio_queue.empty():
                    break
                if self._aborted:
                    break
    
    def abort(self) -> None:
        """Abort the TTS stream (for barge-in)."""
        self._aborted = True
        self._buffer = ""
        # Clear the queue
        while not self._audio_queue.empty():
            try:
                self._audio_queue.get_nowait()
            except asyncio.QueueEmpty:
                break


# Singleton provider
_tts_provider: Optional[StreamingTTSProvider] = None


def get_tts_provider() -> StreamingTTSProvider:
    """Get the singleton TTS provider."""
    global _tts_provider
    if _tts_provider is None:
        _tts_provider = StreamingTTSProvider()
    return _tts_provider
