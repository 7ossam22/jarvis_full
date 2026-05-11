from client.modules.obsidian import ObsidianClient
import sys

def test_obsidian():
    client = ObsidianClient()
    print("Testing Obsidian connection...")
    try:
        notes = client.list_notes()
        print(f"Successfully connected to Obsidian. Found {len(notes)} items.")
        
        # Test reading Welcome.md
        welcome = client.get_note("Welcome.md")
        if welcome:
            print("Successfully read Welcome.md")
        else:
            print("Could not read Welcome.md (might not exist yet)")
            
        return True
    except Exception as e:
        print(f"Connection failed: {e}")
        return False

if __name__ == "__main__":
    if test_obsidian():
        print("Obsidian integration test PASSED.")
    else:
        print("Obsidian integration test FAILED.")
        sys.exit(1)
