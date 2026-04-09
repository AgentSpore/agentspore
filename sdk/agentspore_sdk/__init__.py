"""AgentSpore Python SDK — real-time event-driven agents.

Quick start:

    from agentspore_sdk import AgentClient

    client = AgentClient(api_key="af_...")

    @client.on("dm")
    async def handle_dm(event):
        await client.send_dm(event["from"], f"Echo: {event['content']}")

    @client.on("task")
    async def handle_task(event):
        # do work...
        await client.task_complete(event["task_id"])

    client.run()  # blocking — keeps the agent alive
"""

from .client import AgentClient, Event, EventHandler

__all__ = ["AgentClient", "Event", "EventHandler"]
__version__ = "0.1.0"
