import requests
from client.config import OBSIDIAN_API_KEY, OBSIDIAN_INSECURE_URL

class ObsidianClient:
    def __init__(self):
        self.base_url = OBSIDIAN_INSECURE_URL
        self.headers = {
            "Authorization": f"Bearer {OBSIDIAN_API_KEY}",
            "Content-Type": "application/json"
        }

    def get_note(self, path: str):
        """Reads a note from the vault."""
        response = requests.get(f"{self.base_url}/active/", headers=self.headers) if path == "active" else \
                   requests.get(f"{self.base_url}/vault/{path}", headers=self.headers)
        if response.status_code == 200:
            return response.text
        return None

    def search_notes(self, query: str):
        """Searches for notes in the vault."""
        # Simple search implementation using the search endpoint if available
        # Otherwise, list files and filter (simplified for now)
        params = {"query": query}
        response = requests.post(f"{self.base_url}/search/", headers=self.headers, json=params)
        if response.status_code == 200:
            return response.json()
        return []

    def update_note(self, path: str, content: str, mode: str = "overwrite"):
        """Creates or updates a note."""
        if mode == "append":
            return self.append_to_note(path, content)
        
        response = requests.put(f"{self.base_url}/vault/{path}", headers=self.headers, data=content)
        return response.status_code in [200, 204]

    def append_to_note(self, path: str, content: str):
        """Appends content to an existing note."""
        response = requests.post(f"{self.base_url}/vault/{path}", headers=self.headers, data=content)
        return response.status_code in [200, 204]

    def list_notes(self):
        """Lists all files in the vault."""
        response = requests.get(f"{self.base_url}/vault/", headers=self.headers)
        if response.status_code == 200:
            return response.json()
        return []
