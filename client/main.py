import sys
from client.config import STT_PROVIDER
from client.modules.stt.cloud import CloudSTT
from client.modules.stt.local import LocalSTT
from client.modules.tts import TTSProvider
from client.modules.llm_agent import LLMAgent

def main():
    print("Initializing Jarvis Voice Client...")
    
    # Initialize components
    if STT_PROVIDER == "cloud":
        stt = CloudSTT()
    else:
        stt = LocalSTT()
        
    tts = TTSProvider()
    agent = LLMAgent()
    
    print("\n--- Jarvis is ready ---")
    print("Press Ctrl+C to exit.")
    
    try:
        while True:
            input("\nPress Enter to speak to Jarvis...")
            
            # 1. Listen
            user_text = stt.listen()
            if not user_text:
                print("Jarvis: I didn't catch that. Could you repeat?")
                continue
            
            print(f"You: {user_text}")
            
            # 2. Think
            print("Jarvis is thinking...")
            response_text = agent.process_query(user_text)
            
            # 3. Speak
            print(f"Jarvis: {response_text}")
            tts.speak(response_text)
            
    except KeyboardInterrupt:
        print("\nShutting down Jarvis. Goodbye!")
        sys.exit(0)

if __name__ == "__main__":
    main()
