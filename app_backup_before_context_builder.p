import os
import ollama
import chainlit as cl
from typing import List, Dict

MODEL = "qwen2.5-coder:7b"
LEO_FILES_PATH = os.path.expanduser("~/Desktop/Leo_Files")

def load_leo_file(filename: str) -> str:
    path = os.path.join(LEO_FILES_PATH, filename)
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return f"[Missing: {filename}]"

@cl.on_chat_start
async def start():
    # Load all files once at startup
    content = f'''You are Leo, Caleb's autonomous agent.
=== IDENTITY ===
{load_leo_file("IDENTITY.md")}
=== SOUL ===
{load_leo_file("SOUL.md")}
=== AGENTS ===
{load_leo_file("AGENTS.md")}
=== OPERATING MODEL ===
{load_leo_file("OPERATING_MODEL.md")}
=== TROUBLESHOOTING ===
{load_leo_file("GENERAL-TROUBLESHOOTING-FRAMEWORK-V5-FINAL.md")}
=== USER ===
{load_leo_file("USER.md")}'''

    cl.user_session.set("history", [{"role": "system", "content": content}])
    await cl.Message(content=f"🚀 Leo ({MODEL}) is online. Fast-mode active.").send()

@cl.on_message
async def main(message: cl.Message):
    history = cl.user_session.get("history")
    history.append({"role": "user", "content": message.content})
    msg = cl.Message(content="")
    await msg.send()

    try:
        client = ollama.AsyncClient(timeout=60.0)
        full_response = ""
        async for part in await client.chat(
            model=MODEL,
            messages=history,
            stream=True,
            options={"num_ctx": 8192, "temperature": 0.3}
        ):
            token = part['message']['content']
            full_response += token
            await msg.stream_token(token)

        history.append({"role": "assistant", "content": full_response})
        cl.user_session.set("history", history)
        await msg.update()
    except Exception as e:
        await cl.Message(content=f"Error: {str(e)}").send()
