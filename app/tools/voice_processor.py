import os
import asyncio
import subprocess
import logging
from openai import AsyncOpenAI
from app.config import Config

logger = logging.getLogger(__name__)

class VoiceProcessor:
    def __init__(self):
        # Determine the best base URL for OpenAI Whisper API.
        # If the user has a custom LLM URL (like OpenRouter or deepseek), 
        # they might still want to use OpenAI for Whisper or their provider supports it.
        # If the API key starts with sk- (standard OpenAI key), we default to standard OpenAI Whisper.
        base_url = Config.LLM_BASE_URL
        # Only override if it's a standard OpenAI key and not an OpenRouter key
        if "openai.com" not in base_url and Config.LLM_API_KEY.startswith("sk-") and not Config.LLM_API_KEY.startswith("sk-or-"):
            base_url = "https://api.openai.com/v1"
            
        self.client = AsyncOpenAI(api_key=Config.LLM_API_KEY, base_url=base_url)

    async def transcribe_voice(self, ogg_path: str) -> str:
        """Convert Telegram OGG/Opus voice message to MP3 and transcribe using OpenAI Whisper."""
        mp3_path = ogg_path.replace(".ogg", ".mp3")
        
        try:
            logger.info(f"Converting voice message {ogg_path} to MP3...")
            # Convert OGG to MP3 using ffmpeg
            cmd = ["ffmpeg", "-y", "-i", ogg_path, "-acodec", "libmp3lame", "-aq", "4", mp3_path]
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
            stdout, stderr = await process.communicate()
            
            if process.returncode != 0:
                logger.error(f"FFmpeg conversion failed: {stderr.decode()}")
                raise RuntimeError("Failed to convert audio file.")
                
            logger.info("Sending audio to OpenAI Whisper API...")
            if not os.path.exists(mp3_path):
                raise FileNotFoundError(f"Converted MP3 file not found at {mp3_path}")
                
            with open(mp3_path, "rb") as audio_file:
                transcription = await self.client.audio.transcriptions.create(
                    model="whisper-1",
                    file=audio_file,
                    language="ru"  # Specify Russian language for maximum precision
                )
                
            text = transcription.text
            logger.info(f"Successfully transcribed voice message: '{text}'")
            return text
            
        except Exception as e:
            logger.error(f"Error during voice transcription: {e}")
            raise e
        finally:
            # Clean up temporary files
            if os.path.exists(mp3_path):
                try:
                    os.remove(mp3_path)
                except Exception as ex:
                    logger.error(f"Failed to remove temp MP3 file: {ex}")
