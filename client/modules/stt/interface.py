from abc import ABC, abstractmethod

class STTProvider(ABC):
    @abstractmethod
    def listen(self) -> str:
        """Listens to the microphone and returns the transcribed text."""
        pass
