import speech_recognition as sr
from client.modules.stt.interface import STTProvider

class LocalSTT(STTProvider):
    def __init__(self):
        self.recognizer = sr.Recognizer()
        self.microphone = sr.Microphone()

    def listen(self) -> str:
        with self.microphone as source:
            print("Listening (Local fallback)...")
            self.recognizer.adjust_for_ambient_noise(source)
            audio = self.recognizer.listen(source)

        try:
            # Note: recognize_google is a free web service, but served as a "local" fallback here.
            # A true local solution would be recognize_sphinx or local whisper.
            text = self.recognizer.recognize_google(audio)
            return text
        except sr.UnknownValueError:
            print("Local STT could not understand audio")
            return ""
        except sr.RequestError as e:
            print(f"Local STT error; {e}")
            return ""
