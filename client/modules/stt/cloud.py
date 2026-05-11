import speech_recognition as sr
from openai import OpenAI
from client.config import OPENAI_API_KEY
from client.modules.stt.interface import STTProvider
import tempfile
import os

class CloudSTT(STTProvider):
    def __init__(self):
        self.client = OpenAI(api_key=OPENAI_API_KEY)
        self.recognizer = sr.Recognizer()
        self.microphone = sr.Microphone()

    def listen(self) -> str:
        with self.microphone as source:
            print("Listening (Cloud)...")
            self.recognizer.adjust_for_ambient_noise(source)
            audio = self.recognizer.listen(source)

        try:
            # Save audio to a temporary file
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as temp_audio:
                temp_audio.write(audio.get_wav_data())
                temp_audio_path = temp_audio.name

            # Use OpenAI Whisper API
            with open(temp_audio_path, "rb") as audio_file:
                transcript = self.client.audio.transcriptions.create(
                    model="whisper-1", 
                    file=audio_file
                )
            
            os.remove(temp_audio_path)
            return transcript.text
        except Exception as e:
            print(f"Cloud STT Error: {e}")
            return ""
