import os
import asyncio
import subprocess
import logging
from openai import AsyncOpenAI
import speech_recognition as sr
from app.config import Config

logger = logging.getLogger(__name__)

class VoiceProcessor:
    def __init__(self):
        # Determine the best base URL for OpenAI Whisper API.
        # If the API key starts with sk- (standard OpenAI key) and not sk-or- (OpenRouter),
        # we configure OpenAI client.
        self.use_openai_whisper = False
        
        if Config.LLM_API_KEY and Config.LLM_API_KEY.startswith("sk-") and not Config.LLM_API_KEY.startswith("sk-or-"):
            base_url = "https://api.openai.com/v1"
            self.client = AsyncOpenAI(api_key=Config.LLM_API_KEY, base_url=base_url)
            self.use_openai_whisper = True
            logger.info("VoiceProcessor: Configured to use OpenAI Whisper API.")
        else:
            logger.info("VoiceProcessor: OpenRouter or custom key detected. Using high-speed offline-fallback Google Speech Recognition API.")

    def _transcribe_google_sync(self, wav_path: str) -> str:
        """Synchronous part of Google Speech Recognition, to be run in a separate thread."""
        r = sr.Recognizer()
        with sr.AudioFile(wav_path) as source:
            audio_data = r.record(source)
        try:
            return r.recognize_google(audio_data, language="ru-RU")
        except sr.UnknownValueError:
            logger.warning("Google Speech Recognition: Audio was not understood.")
            return ""
        except sr.RequestError as e:
            logger.error(f"Google Speech Recognition service error: {e}")
            raise e

    async def transcribe_voice(self, ogg_path: str) -> str:
        """Convert Telegram OGG/Opus voice message and transcribe using Whisper or Google Speech API."""
        
        # If we can use OpenAI Whisper (using standard OpenAI key)
        if self.use_openai_whisper:
            mp3_path = ogg_path.replace(".ogg", ".mp3")
            try:
                logger.info(f"Converting voice message {ogg_path} to MP3...")
                cmd = ["ffmpeg", "-y", "-i", ogg_path, "-acodec", "libmp3lame", "-aq", "4", mp3_path]
                process = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE
                )
                stdout, stderr = await process.communicate()
                
                if process.returncode != 0:
                    logger.error(f"FFmpeg conversion to MP3 failed: {stderr.decode()}")
                    raise RuntimeError("Failed to convert audio file to MP3.")
                    
                logger.info("Sending audio to OpenAI Whisper API...")
                with open(mp3_path, "rb") as audio_file:
                    transcription = await self.client.audio.transcriptions.create(
                        model="whisper-1",
                        file=audio_file,
                        language="ru"
                    )
                return transcription.text
            except Exception as e:
                logger.error(f"OpenAI Whisper failed: {e}. Falling back to Google Speech Recognition...")
            finally:
                if os.path.exists(mp3_path):
                    try:
                        os.remove(mp3_path)
                    except Exception as ex:
                        logger.error(f"Failed to remove temp MP3 file: {ex}")

        # Fallback (or default for OpenRouter users): Google Speech Recognition (free, unlimited, keyless)
        wav_path = ogg_path.replace(".ogg", ".wav")
        try:
            logger.info(f"Converting voice message {ogg_path} to WAV...")
            # Convert OGG to 16kHz Mono 16-bit PCM WAV (ideal for Google Speech Recognition)
            cmd = ["ffmpeg", "-y", "-i", ogg_path, "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1", wav_path]
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
            stdout, stderr = await process.communicate()
            
            if process.returncode != 0:
                logger.error(f"FFmpeg conversion to WAV failed: {stderr.decode()}")
                raise RuntimeError("Failed to convert audio file to WAV.")
                
            logger.info("Transcribing audio via Google Speech Recognition API (ru-RU)...")
            # Run the synchronous transcription in an executor thread to keep things fully asynchronous
            text = await asyncio.to_thread(self._transcribe_google_sync, wav_path)
            return text
            
        except Exception as e:
            logger.error(f"Error during fallback voice transcription: {e}")
            raise e
        finally:
            if os.path.exists(wav_path):
                try:
                    os.remove(wav_path)
                except Exception as ex:
                    logger.error(f"Failed to remove temp WAV file: {ex}")
