import os
import ollama
from typing import List, Dict

# ====================== CONFIG ======================
MODEL = "qwen3.5:35b-a3b"          # Change to qwen3.5:27b if 35b is still unstable
LEO_FILES_PATH = os.path.expanduser("~/Desktop/Leo files")

# Memory-friendly settings
OPTIONS = {
    "num_ctx": 8192,
    "temperature": 0.35,
    "top_p": 0.9
}

# ===================================================

def load_leo_file(filename: str) -> str:
    path = os.path.join(LEO_FILES_PATH, filename)
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read().strip()
        return content if content else f"[Empty file: {filename}]"
    except FileNotFoundError:
        return f"[Warning: {filename} not found in Leo files folder]"
    except Exception as e:
        return f"[Error reading {filename}: {e}]"

def build_system_prompt() -> str:
    files = {
        "IDENTITY.md": load_leo_file("IDENTITY.md"),
        "SOUL.md": load_leo_file("SOUL.md"),
        "AGENTS.md": load_leo_file("AGENTS.md"),
        "USER.md": load_leo_file("USER.md"),
        "OPERATING_MODEL.md": load_leo_file("OPERATING_MODEL.md"),
        "GENERAL-TROUBLESHOOTING-FRAMEWORK-V5-FINAL.md": load_leo_file("GENERAL-TROUBLESHOOTING-FRAMEWORK-V5-FINAL.md"),
        "MEMORY.md": load_leo_file("MEMORY.md")
    }

    return f"""You are Leo, Caleb's autonomous agent.

You are defined by the files in ~/Desktop/Leo files/. Here is their current content:

=== IDENTITY ===
{files["IDENTITY.md"]}

=== SOUL ===
{files["SOUL.md"]}

=== AGENTS ===
{files["AGENTS.md"]}

=== USER CONTEXT ===
{files["USER.md"]}

=== OPERATING MODEL ===
{files["OPERATING_MODEL.md"]}

=== TROUBLESHOOTING FRAMEWORK (V5) ===
{files["GENERAL-TROUBLESHOOTING-FRAMEWORK-V5-FINAL.md"]}

=== LONG-TERM MEMORY ===
{files["MEMORY.md"]}

You must fully embody all of the above. Be direct, concise, proactive, and action-oriented.
Use the V5 Troubleshooting Framework rigorously when debugging or solving problems.
Prioritize evidence-based reasoning and continuous system improvement.

You can propose edits to these files when it would improve alignment or effectiveness."""

def main():
    print("🚀 Leo Agent (Dynamic Leo Files) started with", MODEL)
    print("Reading files from ~/Desktop/Leo files/ every turn.\n")
    print("Type 'exit' or 'quit' to stop.\n")

    messages: List[Dict] = []

    while True:
        try:
            user_input = input("You: ").strip()
            if user_input.lower() in ["exit", "quit", "q"]:
                print("👋 Goodbye.")
                break
            if not user_input:
                continue

            system_prompt = build_system_prompt()

            messages.append({"role": "user", "content": user_input})

            response = ollama.chat(
                model=MODEL,
                messages=[{"role": "system", "content": system_prompt}] + messages,
                options=OPTIONS
            )

            assistant_content = response['message']['content']
            print(f"\nLeo: {assistant_content}\n")

            messages.append({"role": "assistant", "content": assistant_content})

            # Keep history reasonable
            if len(messages) > 20:
                messages = messages[-20:]

        except Exception as e:
            print(f"❌ Error: {e}")

if __name__ == "__main__":
    main()
