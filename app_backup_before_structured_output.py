import os
import ollama
import chainlit as cl

MODEL = "qwen2.5-coder:7b"
LEO_FILES_PATH = os.path.expanduser("~/Desktop/Leo_Files")

MAX_HISTORY_MESSAGES = 6

def load_file(filename: str) -> str:
    path = os.path.join(LEO_FILES_PATH, filename)
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return f"[Missing: {filename}]"

def needs_troubleshooting(text: str) -> bool:
    keywords = [
        "error", "bug", "fail", "failure", "broken", "debug",
        "traceback", "exception", "crash", "not working", "fix", "webhook"
    ]
    t = text.lower()
    return any(k in t for k in keywords)

def needs_project_status(text: str) -> bool:
    keywords = [
        "status", "next action", "next step", "what are we working on",
        "blocker", "priority", "project"
    ]
    t = text.lower()
    return any(k in t for k in keywords)

def build_system_prompt(user_message: str) -> str:
    prompt = f"""
You are Leo, Caleb's local AI agent system.

=== IDENTITY ===
{load_file("IDENTITY.md")}

=== SOUL ===
{load_file("SOUL.md")}

Core rules:
- Be direct, practical, and action-oriented.
- Do not restate protocols.
- Do not explain the whole framework.
- Give only the next useful action unless asked for more.
- Maximum response length: 8 bullets or fewer.
"""

    if needs_project_status(user_message):
        prompt += f"""

=== PROJECT STATUS ===
{load_file("PROJECT_STATUS.md")}
"""

    if needs_troubleshooting(user_message):
        prompt += f"""

=== TROUBLESHOOTING PROTOCOL ===
{load_file("GENERAL-TROUBLESHOOTING-FRAMEWORK-V5-FINAL.md")}

Troubleshooting response contract:
- State current phase.
- Give the next test to run.
- State what positive evidence proves success/failure.
- Ask for the result.
- Do NOT list all phases.
- Do NOT give examples unless requested.
"""

    return prompt

@cl.on_chat_start
async def start():
    cl.user_session.set("history", [])
    await cl.Message(content=f"🚀 Leo ({MODEL}) is online. Context-builder v3 active.").send()

@cl.on_message
async def main(message: cl.Message):
    history = cl.user_session.get("history") or []
    system_prompt = build_system_prompt(message.content)

    messages = [{"role": "system", "content": system_prompt}]
    messages.extend(history[-MAX_HISTORY_MESSAGES:])
    messages.append({"role": "user", "content": message.content})

    msg = cl.Message(content="")
    await msg.send()

    try:
        client = ollama.AsyncClient(timeout=120.0)
        full_response = ""

        async for part in await client.chat(
            model=MODEL,
            messages=messages,
            stream=True,
            options={"num_ctx": 8192, "temperature": 0.2}
        ):
            token = part["message"]["content"]
            full_response += token
            await msg.stream_token(token)

        history.append({"role": "user", "content": message.content})
        history.append({"role": "assistant", "content": full_response})
        cl.user_session.set("history", history[-MAX_HISTORY_MESSAGES:])
        await msg.update()

    except Exception as e:
        await cl.Message(content=f"Error: {str(e)}").send()
