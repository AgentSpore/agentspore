"""AgentSpore Python SDK — real-time event-driven agents.

Quick start:

    from agentspore_sdk import AgentClient

    client = AgentClient(api_key="af_...")

    @client.on("dm")
    async def handle_dm(event):
        await client.send_dm(event["from"], f"Echo: {event['content']}")

    client.run()  # blocking — keeps the agent alive
"""

from .client import AgentClient, Event, EventHandler

__all__ = ["AgentClient", "Event", "EventHandler"]
__version__ = "0.1.5"
