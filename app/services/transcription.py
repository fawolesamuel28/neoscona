import os
import httpx
import logging
import tempfile
from app.core.logger import get_logger

logger = get_logger(__name__)

async def download_media(media_url: str, api_key: str = None) -> bytes:
    """Download media from 360dialog or Evolution API."""
    async with httpx.AsyncClient() as client:
        headers = {"D360-API-KEY": api_key} if api_key else {}
        response = await client.get(media_url, headers=headers)
        response.raise_for_status()
        return response.content

async def transcribe_voice_note(audio_url: str) -> str:
    """
    Download and transcribe voice note using OpenAI Whisper.
    """
    api_key = os.getenv("WHATSAPP_API_KEY") # Use for download
    openai_key = os.getenv("OPENAI_API_KEY")
    
    if not openai_key:
        logger.warning("OPENAI_API_KEY not set, transcription skipped.")
        return "[VOICE_NOTE_UNAVAILABLE]"

    try:
        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key=openai_key)
        
        audio_data = await download_media(audio_url, api_key)
        
        # Save to temp file for Whisper
        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
            tmp.write(audio_data)
            tmp_path = tmp.name
        
        try:
            with open(tmp_path, "rb") as audio_file:
                transcript = await client.audio.transcriptions.create(
                    model="whisper-1",
                    file=audio_file
                )
            return transcript.text
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
                
    except Exception as e:
        logger.error(f"Voice transcription failed: {e}")
        return "[VOICE_NOTE_ERROR]"
