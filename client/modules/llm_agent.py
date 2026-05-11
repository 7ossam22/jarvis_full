import json
from openai import OpenAI
from client.config import OPENAI_API_KEY
from client.modules.obsidian import ObsidianClient

class LLMAgent:
    def __init__(self):
        self.client = OpenAI(api_key=OPENAI_API_KEY)
        self.obsidian = ObsidianClient()
        self.system_prompt = (
            "You are Jarvis, a highly intelligent and helpful AI assistant. "
            "You have access to a persistent knowledge base (an Obsidian vault). "
            "Use the provided tools to search, read, and update notes in the vault "
            "to maintain long-term memory and context about the user's projects and preferences. "
            "Keep your responses concise and helpful."
        )
        self.history = [{"role": "system", "content": self.system_prompt}]
        self.tools = [
            {
                "type": "function",
                "function": {
                    "name": "search_vault",
                    "description": "Search for notes in the Obsidian vault using a query.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string", "description": "The search query."}
                        },
                        "required": ["query"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "read_note",
                    "description": "Read the content of a specific note from the vault.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string", "description": "The path to the note (e.g., 'Project/Notes.md')."}
                        },
                        "required": ["path"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "update_note",
                    "description": "Create or update a note in the vault.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string", "description": "The path to the note."},
                            "content": {"type": "string", "description": "The content to write."},
                            "mode": {"type": "string", "enum": ["overwrite", "append"], "description": "Whether to overwrite or append."}
                        },
                        "required": ["path", "content"]
                    }
                }
            }
        ]

    def process_query(self, user_input: str):
        self.history.append({"role": "user", "content": user_input})
        
        while True:
            response = self.client.chat.completions.create(
                model="gpt-4o",
                messages=self.history,
                tools=self.tools,
                tool_choice="auto"
            )
            
            message = response.choices[0].message
            self.history.append(message)

            if not message.tool_calls:
                return message.content

            for tool_call in message.tool_calls:
                function_name = tool_call.function.name
                arguments = json.loads(tool_call.function.arguments)
                
                print(f"Jarvis is using tool: {function_name}({arguments})")
                
                if function_name == "search_vault":
                    result = self.obsidian.search_notes(arguments["query"])
                elif function_name == "read_note":
                    result = self.obsidian.get_note(arguments["path"])
                elif function_name == "update_note":
                    result = self.obsidian.update_note(arguments["path"], arguments["content"], arguments.get("mode", "overwrite"))
                else:
                    result = "Unknown tool"

                self.history.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "name": function_name,
                    "content": str(result)
                })
