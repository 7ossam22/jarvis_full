import os
import tempfile
from openai import OpenAI
from client.config import OPENAI_API_KEY, TTS_VOICE
import pygame

class TTSProvider:
    def __init__(self):
        self.client = OpenAI(api_key=OPENAI_API_KEY)
        pygame.mixer.init()

    def speak(self, text: str):
        if not text:
            return

        try:
            response = self.client.audio.speech.create(
                model="tts-1",
                voice=TTS_VOICE,
                input=text
            )

            with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
                response.stream_to_file(f.name)
                temp_path = f.name

            pygame.mixer.music.load(temp_path)
            pygame.mixer.music.play()
            while pygame.mixer.music.get_busy():
                pygame.time.Clock().tick(10)
            
            pygame.mixer.music.unload()
            os.remove(temp_path)
        except Exception as e:
            print(f"TTS Error: {e}")
