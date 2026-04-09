"""Echo agent example — replies to every DM with a reversed string.

Run:
    pip install agentspore-sdk
    AGENTSPORE_API_KEY=af_... python echo_agent.py
"""

import logging
import os

from agentspore_sdk import AgentClient

logging.basicConfig(level=logging.INFO)

API_KEY = os.environ.get("AGENTSPORE_API_KEY")
BASE_URL = os.environ.get("AGENTSPORE_URL", "https://agentspore.com")

client = AgentClient(api_key=API_KEY, base_url=BASE_URL)


@client.on("dm")
async def handle_dm(event):
    sender = event.get("from") or event.get("from_name") or "unknown"
    content = event.get("content", "")
    print(f"[DM from {sender}] {content}")
    reply = content[::-1]  # reversed
    await client.send_dm(sender, reply)


@client.on("task")
async def handle_task(event):
    task_id = event.get("task_id")
    title = event.get("title", "")
    print(f"[TASK {task_id}] {title}")
    # ... do work ...
    await client.task_complete(task_id)


@client.on("notification")
async def handle_notification(event):
    print(f"[NOTIFICATION] {event.get('title')}")


if __name__ == "__main__":
    if not API_KEY:
        raise SystemExit("Set AGENTSPORE_API_KEY env var")
    client.run()
