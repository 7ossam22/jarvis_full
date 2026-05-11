import os
import json
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# Project Paths
BASE_DIR = Path(__file__).parent.parent
OBSIDIAN_VAULT_PATH = BASE_DIR / "7oss"
OBSIDIAN_CONFIG_PATH = OBSIDIAN_VAULT_PATH / ".obsidian/plugins/obsidian-local-rest-api/data.json"

def get_obsidian_config():
    """Extracts Obsidian Local REST API config from the plugin's data.json."""
    if not OBSIDIAN_CONFIG_PATH.exists():
        return {
            "api_key": os.getenv("OBSIDIAN_API_KEY"),
            "port": int(os.getenv("OBSIDIAN_PORT", 27124)),
            "insecure_port": int(os.getenv("OBSIDIAN_INSECURE_PORT", 27123))
        }
    
    with open(OBSIDIAN_CONFIG_PATH, "r") as f:
        data = json.load(f)
        return {
            "api_key": data.get("apiKey"),
            "port": data.get("port", 27124),
            "insecure_port": data.get("insecurePort", 27123)
        }

# API Keys
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# Obsidian Configuration
OBSIDIAN_CFG = get_obsidian_config()
OBSIDIAN_API_KEY = OBSIDIAN_CFG["api_key"]
OBSIDIAN_URL = f"https://127.0.0.1:{OBSIDIAN_CFG['port']}"
OBSIDIAN_INSECURE_URL = f"http://127.0.0.1:{OBSIDIAN_CFG['insecure_port']}"

# STT / TTS Settings
STT_PROVIDER = os.getenv("STT_PROVIDER", "cloud")  # "cloud" or "local"
TTS_VOICE = os.getenv("TTS_VOICE", "alloy")
