"""FlowService — business logic for Agent Flows (DAG pipelines)."""

from collections import defaultdict, deque

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_db
from app.repositories.agent_repo import AgentRepository, get_agent_repo
from app.repositories.flow_repo import FlowRepository, get_flow_repo

from loguru import logger

TERMINAL_STATUSES = {"approved", "skipped", "failed"}


class FlowService:
    """DAG validation, flow execution engine, pricing."""

    def __init__(self, repo: FlowRepository, agent_repo: AgentRepository):
        self.repo = repo
        self.agent_repo = agent_repo

    # ── Agent verification ──────────────────────────────────────────────

    async def verify_agent_exists(self, agent_id: str) -> dict:
        """Verify agent exists and return it. Raises ValueError if not found."""
        agent = await self.agent_repo.get_agent_by_id(agent_id)
        if not agent:
            raise ValueError("Agent not found")
        return agent

    # ── DAG Validation (Kahn's algorithm) ──────────────────────────────

    def validate_dag(self, steps: list[dict]) -> list[str]:
        """Check for cycles and dangling references. Returns list of errors."""
        errors: list[str] = []
        step_ids = {str(s["id"]) for s in steps}

        # Check dangling depends_on references
        for s in steps:
            deps = s.get("depends_on") or []
            for dep in deps:
                if dep not in step_ids:
                    errors.append(f"Step '{s['title']}' depends on unknown step {dep}")

        if errors:
            return errors

        # Kahn's algorithm — topological sort to detect cycles
        in_degree: dict[str, int] = {sid: 0 for sid in step_ids}
        adjacency: dict[str, list[str]] = defaultdict(list)

        for s in steps:
            sid = str(s["id"])
            deps = s.get("depends_on") or []
            in_degree[sid] = len(deps)
            for dep in deps:
                adjacency[dep].append(sid)

        queue = deque(sid for sid, deg in in_degree.items() if deg == 0)
        visited = 0

        while queue:
            node = queue.popleft()
            visited += 1
            for child in adjacency[node]:
                in_degree[child] -= 1
                if in_degree[child] == 0:
                    queue.append(child)

        if visited < len(step_ids):
            errors.append("Flow contains a cycle — steps cannot depend on each other in a loop")

        return errors

    # ── Start Flow ─────────────────────────────────────────────────────

    async def start_flow(self, flow_id: str) -> dict:
        """Validate DAG and start execution. Returns flow or raises ValueError."""
        flow = await self.repo.get_flow_by_id(flow_id)
        if not flow:
            raise ValueError("Flow not found")
        if flow["status"] != "draft":
            raise ValueError(f"Cannot start flow in status '{flow['status']}'")

        steps = await self.repo.get_flow_steps(flow_id)
        if not steps:
            raise ValueError("Flow has no steps")

        errors = self.validate_dag(steps)
        if errors:
            raise ValueError(f"Invalid DAG: {'; '.join(errors)}")

        # Set flow to running
        await self.repo.update_flow_status(flow_id, "running")

        # Advance — mark root steps (no dependencies) as ready
        await self._advance_flow(flow_id)

        return await self.repo.get_flow_by_id(flow_id)

    # ── Advance Flow (core execution engine) ───────────────────────────

    async def _advance_flow(self, flow_id: str) -> None:
        """After any step state change, check which steps become ready
        and whether the flow is complete."""
        steps = await self.repo.get_flow_steps(flow_id)

        done_ids = {str(s["id"]) for s in steps if s["status"] in TERMINAL_STATUSES}
        all_terminal = all(s["status"] in TERMINAL_STATUSES for s in steps)

        if all_terminal:
            await self.repo.update_flow_status(flow_id, "completed")
            await self.repo.update_flow_totals(flow_id)
            logger.info("Flow {} completed", flow_id)
            return

        for s in steps:
            if s["status"] != "pending":
                continue
            deps = s.get("depends_on") or []
            if all(dep in done_ids for dep in deps):
                # Assemble input from upstream outputs
                input_text = self._assemble_input(s, steps)
                await self.repo.update_step(str(s["id"]), input_text=input_text)
                await self.repo.update_step_status(str(s["id"]), "ready")
                logger.info("Step {} ({}) → ready", s["id"], s["title"])

    def _assemble_input(self, step: dict, all_steps: list[dict]) -> str:
        """Build input_text for a step from its instructions + upstream outputs."""
        parts: list[str] = []

        if step.get("instructions"):
            parts.append(f"## Instructions\n{step['instructions']}")

        deps = step.get("depends_on") or []
        if deps:
            step_map = {str(s["id"]): s for s in all_steps}
            for dep_id in deps:
                dep = step_map.get(dep_id)
                if dep and dep.get("output_text"):
                    parts.append(f"## Input from: {dep['title']}\n{dep['output_text']}")

        return "\n\n".join(parts) if parts else ""

    # ── Step Actions ───────────────────────────────────────────────────

    async def approve_step(
        self, flow_id: str, step_id: str,
        edited_output: str | None = None,
    ) -> dict:
        step = await self.repo.get_step_by_id(step_id)
        if not step or str(step["flow_id"]) != flow_id:
            raise ValueError("Step not found in this flow")
        if step["status"] != "review":
            raise ValueError(f"Cannot approve step in status '{step['status']}'")

        extra = {}
        if edited_output is not None:
            extra["output_text"] = edited_output

        await self.repo.update_step_status(step_id, "approved", **extra)
        await self._advance_flow(flow_id)
        return await self.repo.get_step_by_id(step_id)

    async def reject_step(self, flow_id: str, step_id: str, feedback: str) -> dict:
        step = await self.repo.get_step_by_id(step_id)
        if not step or str(step["flow_id"]) != flow_id:
            raise ValueError("Step not found in this flow")
        if step["status"] != "review":
            raise ValueError(f"Cannot reject step in status '{step['status']}'")

        # Back to active — agent reworks
        await self.repo.update_step_status(step_id, "active")
        # Post system message with feedback
        await self.repo.insert_message(
            step_id, "system", str(step["flow_id"]),
            f"Step rejected. Feedback: {feedback}", "text",
        )
        return await self.repo.get_step_by_id(step_id)

    async def skip_step(
        self, flow_id: str, step_id: str, reason: str | None = None,
    ) -> dict:
        step = await self.repo.get_step_by_id(step_id)
        if not step or str(step["flow_id"]) != flow_id:
            raise ValueError("Step not found in this flow")
        if step["status"] not in ("pending", "ready", "active", "review"):
            raise ValueError(f"Cannot skip step in status '{step['status']}'")

        await self.repo.update_step_status(step_id, "skipped")
        if reason:
            await self.repo.insert_message(
                step_id, "system", str(step["flow_id"]),
                f"Step skipped. Reason: {reason}", "text",
            )
        await self._advance_flow(flow_id)
        return await self.repo.get_step_by_id(step_id)

    async def agent_complete_step(
        self, step_id: str,
        output_text: str, output_files: list[dict] | None = None,
    ) -> dict:
        """Agent marks step as done → goes to review (or auto-approved)."""
        step = await self.repo.get_step_by_id(step_id)
        if not step:
            raise ValueError("Step not found")
        if step["status"] != "active":
            raise ValueError(f"Cannot complete step in status '{step['status']}'")

        extra = {"output_text": output_text}
        if output_files:
            import json
            extra["output_files"] = json.dumps(output_files)

        if step["auto_approve"]:
            await self.repo.update_step_status(step_id, "approved", **extra)
            await self._advance_flow(str(step["flow_id"]))
        else:
            await self.repo.update_step_status(step_id, "review", **extra)

        return await self.repo.get_step_by_id(step_id)

    # ── Flow Control ───────────────────────────────────────────────────

    async def pause_flow(self, flow_id: str) -> dict:
        flow = await self.repo.get_flow_by_id(flow_id)
        if not flow or flow["status"] != "running":
            raise ValueError("Can only pause a running flow")
        await self.repo.update_flow_status(flow_id, "paused")
        return await self.repo.get_flow_by_id(flow_id)

    async def resume_flow(self, flow_id: str) -> dict:
        flow = await self.repo.get_flow_by_id(flow_id)
        if not flow or flow["status"] != "paused":
            raise ValueError("Can only resume a paused flow")
        await self.repo.update_flow_status(flow_id, "running")
        await self._advance_flow(flow_id)
        return await self.repo.get_flow_by_id(flow_id)

    async def cancel_flow(self, flow_id: str) -> dict:
        flow = await self.repo.get_flow_by_id(flow_id)
        if not flow or flow["status"] in ("completed", "cancelled"):
            raise ValueError("Cannot cancel this flow")
        await self.repo.update_flow_status(flow_id, "cancelled")
        return await self.repo.get_flow_by_id(flow_id)

    # ── Pricing ────────────────────────────────────────────────────────

    def calculate_step_pricing(self, price_tokens: int) -> tuple[int, int]:
        """Returns (price_tokens, platform_fee)."""
        settings = get_settings()
        if not settings.rental_payment_enabled:
            return 0, 0
        fee = int(price_tokens * settings.rental_platform_fee_pct)
        return price_tokens, fee


def get_flow_service(
    repo: FlowRepository = Depends(get_flow_repo),
    agent_repo: AgentRepository = Depends(get_agent_repo),
) -> FlowService:
    return FlowService(repo, agent_repo)
