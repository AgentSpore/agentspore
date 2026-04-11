"""CouncilService — orchestrates council lifecycle.

State machine: convening → briefing → round_1..N → voting → synthesizing → done.
Runs as a background asyncio task per council. Persists every step to DB so the
frontend can stream progress via SSE or polling.
"""

from __future__ import annotations

import asyncio
import json
import os
import random
from typing import Any

import httpx
from fastapi import Depends
from loguru import logger

from app.core.database import async_session_maker
from app.repositories.council_repo import CouncilRepository, get_council_repo
from app.services.council_adapters import PanelistAdapter, PlatformWSAdapter, PureLLMAdapter
from app.services.openrouter_service import OpenRouterService, get_openrouter_service

# Default diverse free-model selection for PureLLM recruitment.
# Picks one model per provider family so the panel speaks with distinct voices.
# Ordered by how reliably they resolve through shared OpenRouter accounts —
# providers blocked by account-level privacy settings fall to the back.
_DEFAULT_DIVERSE_PROVIDERS = (
    "google", "minimax", "z-ai", "openai", "nvidia",
    "qwen", "meta-llama", "mistralai", "deepseek",
)


class CouncilService:
    """Business logic + background state machine for councils."""

    MIN_PANEL = 3
    MAX_PANEL = 7
    MAX_ROUNDS_CAP = 5

    def __init__(
        self,
        repo: CouncilRepository,
        openrouter: OpenRouterService,
    ):
        self.repo = repo
        self.openrouter = openrouter

    # ── Convening ────────────────────────────────────────────────────

    async def convene(
        self,
        topic: str,
        brief: str,
        *,
        mode: str = "round_robin",
        panel_size: int = 5,
        max_rounds: int = 3,
        max_tokens_per_msg: int = 500,
        timebox_seconds: int = 600,
        panelists: list[dict] | None = None,
        convener_user_id: str | None = None,
        convener_agent_id: str | None = None,
        convener_ip: str | None = None,
        is_public: bool = True,
    ) -> dict:
        """Create council, recruit panel, kick off background state machine."""
        panel_size = max(self.MIN_PANEL, min(self.MAX_PANEL, panel_size))
        max_rounds = max(1, min(self.MAX_ROUNDS_CAP, max_rounds))

        council = await self.repo.create(
            topic=topic, brief=brief, mode=mode,
            panel_size=panel_size, max_rounds=max_rounds,
            max_tokens_per_msg=max_tokens_per_msg, timebox_seconds=timebox_seconds,
            convener_user_id=convener_user_id,
            convener_agent_id=convener_agent_id,
            convener_ip=convener_ip, is_public=is_public,
        )

        if panelists is None:
            panelists = await self._auto_recruit_pure_llm(panel_size)

        for p in panelists:
            await self.repo.add_panelist(
                council["id"],
                adapter=p["adapter"],
                display_name=p.get("display_name") or p.get("model_id") or "panelist",
                role=p.get("role", "panelist"),
                agent_id=p.get("agent_id"),
                model_id=p.get("model_id"),
                perspective=p.get("perspective"),
            )
        await self.repo.db.commit()

        # Kick off state machine in the background.
        asyncio.create_task(run_council(str(council["id"])))
        return council

    async def _auto_recruit_pure_llm(self, size: int) -> list[dict]:
        """Pick up to `size` diverse free models from OpenRouter."""
        models = await self.openrouter.get_models()
        picked: list[dict] = []
        used_providers: set[str] = set()

        # First pass: one per known provider family (diversity over count).
        for provider in _DEFAULT_DIVERSE_PROVIDERS:
            for m in models:
                if m["id"].startswith(provider + "/") and provider not in used_providers:
                    picked.append({
                        "adapter": "pure_llm",
                        "model_id": m["id"],
                        "display_name": m["name"].split(" — ")[0],
                        "role": "panelist",
                        "perspective": None,
                    })
                    used_providers.add(provider)
                    break
            if len(picked) >= size - 1:
                break

        # Always add devil's advocate. Prefer a model from a provider NOT
        # already on the panel so the skeptic has a distinct voice.
        used_models = {p["model_id"] for p in picked}
        advocate_model = None
        for m in models:
            if m["id"] not in used_models:
                advocate_model = m["id"]
                break
        if advocate_model is None:
            advocate_model = models[0]["id"] if models else "google/gemma-4-26b-a4b-it:free"
        picked.append({
            "adapter": "pure_llm",
            "model_id": advocate_model,
            "display_name": "Devil's Advocate",
            "role": "devil_advocate",
            "perspective": (
                "You are the devil's advocate. Your job is to find weaknesses, "
                "hidden assumptions, and risks in what the other panelists say. "
                "Be blunt but constructive. Push back even on consensus."
            ),
        })

        return picked[:size]


def get_council_service(
    repo: CouncilRepository = Depends(get_council_repo),
    openrouter: OpenRouterService = Depends(get_openrouter_service),
) -> CouncilService:
    return CouncilService(repo, openrouter)


# ── Background state machine ────────────────────────────────────────────


def _build_adapter(panelist: dict, council: dict) -> PanelistAdapter:
    a = panelist["adapter"]
    if a == "pure_llm":
        return PureLLMAdapter(panelist, council)
    if a == "platform_ws":
        return PlatformWSAdapter(panelist, council)
    raise ValueError(f"Unknown adapter type: {a}")


def _build_system_prompt(council: dict, panelist: dict) -> str:
    role = panelist["role"]
    persp = panelist.get("perspective") or ""
    base = (
        f"You are a panelist in a multi-agent council on AgentSpore. "
        f"Your display name is '{panelist['display_name']}'. "
        f"Topic: {council['topic']}. "
        f"Mode: {council['mode']}. "
        f"You will be given the full discussion so far and asked to contribute. "
        f"Keep your reply under {council['max_tokens_per_msg']} tokens. "
        f"Focus on new information, do not repeat points already made. "
        f"Cite previous speakers by name when disagreeing."
    )
    if role == "devil_advocate":
        return base + " " + (persp or "Challenge the consensus. Find weaknesses.")
    if role == "moderator":
        return base + " You are the moderator — keep discussion on track and summarize."
    if persp:
        return base + " " + persp
    return base


def _build_history_for_panelist(council: dict, all_messages: list[dict], self_panelist_id: str) -> list[dict]:
    """Convert council messages into OpenAI-style chat history for a given panelist."""
    history: list[dict] = []
    for m in all_messages:
        if m["kind"] == "brief":
            history.append({"role": "user", "content": f"[Brief]\n{m['content']}"})
        elif m["kind"] == "message":
            # Other panelists are user-turns with a name prefix; own messages are assistant.
            if str(m.get("panelist_id") or "") == str(self_panelist_id):
                history.append({"role": "assistant", "content": m["content"]})
            else:
                speaker = m.get("speaker_name") or "Panelist"
                history.append({"role": "user", "content": f"[{speaker}]: {m['content']}"})
        elif m["kind"] == "vote_call":
            history.append({"role": "user", "content": m["content"]})
    return history


async def run_council(council_id: str) -> None:
    """Background task driving a council through its full lifecycle."""
    try:
        async with async_session_maker() as session:
            repo = CouncilRepository(session)
            council = await repo.get_by_id(council_id)
            if not council:
                return

            panelists = await repo.list_panelists(council_id)
            if not panelists:
                await repo.update_status(council_id, "aborted", ended=True)
                await session.commit()
                return

            # Briefing
            await repo.update_status(council_id, "briefing", started=True)
            await repo.add_message(council_id, kind="brief", content=council["brief"], round_num=0)
            await session.commit()

        # Discussion rounds
        for round_num in range(1, council["max_rounds"] + 1):
            await _run_round(council_id, round_num, panelists)

        # Voting
        await _run_vote(council_id, panelists)

        # Synthesize
        await _run_synthesize(council_id)

    except Exception as exc:
        logger.exception("run_council crashed for {}: {}", council_id, exc)
        async with async_session_maker() as session:
            repo = CouncilRepository(session)
            await repo.update_status(council_id, "aborted", ended=True)
            await repo.add_message(council_id, kind="system", content=f"[aborted: {exc}]")
            await session.commit()


async def _run_round(council_id: str, round_num: int, panelists: list[dict]) -> None:
    """Run one round of discussion (round-robin across panelists)."""
    async with async_session_maker() as session:
        repo = CouncilRepository(session)
        await repo.update_status(council_id, "round", round_num=round_num)
        council = await repo.get_by_id(council_id)
        all_messages = await repo.list_messages(council_id)
        await session.commit()

    # Attach speaker_name to messages for history building.
    panelist_by_id = {str(p["id"]): p for p in panelists}
    for m in all_messages:
        pid = str(m.get("panelist_id") or "")
        m["speaker_name"] = panelist_by_id.get(pid, {}).get("display_name", "")

    for panelist in panelists:
        pname = panelist["display_name"]
        logger.info("council {} round {} → panelist '{}' start", council_id, round_num, pname)
        # Delay-free but sequential round-robin; each panelist sees all prior messages.
        system_prompt = _build_system_prompt(council, panelist)
        history = _build_history_for_panelist(council, all_messages, str(panelist["id"]))
        history.append({
            "role": "user",
            "content": f"[Round {round_num}/{council['max_rounds']}] Your turn. Contribute your next point.",
        })

        try:
            adapter = _build_adapter(panelist, council)
            result = await adapter.generate(system_prompt, history)
            logger.info("council {} round {} → panelist '{}' got {} chars", council_id, round_num, pname, len(result.get("content") or ""))
        except Exception as exc:
            logger.exception("council {} round {} adapter.generate failed for '{}': {}", council_id, round_num, pname, exc)
            result = {"content": f"[error: adapter crashed: {type(exc).__name__}]", "meta": {"error": str(exc)[:200]}}

        try:
            async with async_session_maker() as session:
                repo = CouncilRepository(session)
                msg = await repo.add_message(
                    council_id,
                    kind="message",
                    content=result["content"],
                    round_num=round_num,
                    panelist_id=str(panelist["id"]),
                    meta=result.get("meta"),
                )
                await repo.mark_spoke(str(panelist["id"]), round_num)
                await session.commit()
            logger.info("council {} round {} → panelist '{}' saved msg {}", council_id, round_num, pname, msg.get("id"))
        except Exception as exc:
            logger.exception("council {} round {} failed to save message for '{}': {}", council_id, round_num, pname, exc)
            raise

        # Append to in-memory list so next panelist sees it.
        msg["speaker_name"] = panelist["display_name"]
        all_messages.append(msg)


async def _run_vote(council_id: str, panelists: list[dict]) -> None:
    """Ask each panelist to cast a vote (approve/reject/abstain) with reasoning."""
    async with async_session_maker() as session:
        repo = CouncilRepository(session)
        await repo.update_status(council_id, "voting")
        council = await repo.get_by_id(council_id)
        all_messages = await repo.list_messages(council_id)
        await repo.add_message(
            council_id,
            kind="vote_call",
            content=(
                "Voting time. Reply with a single JSON object: "
                '{"vote": "approve|reject|abstain", "confidence": 0..1, "reasoning": "..."}'
            ),
        )
        await session.commit()

    panelist_by_id = {str(p["id"]): p for p in panelists}
    for m in all_messages:
        pid = str(m.get("panelist_id") or "")
        m["speaker_name"] = panelist_by_id.get(pid, {}).get("display_name", "")

    for panelist in panelists:
        system_prompt = _build_system_prompt(council, panelist)
        history = _build_history_for_panelist(council, all_messages, str(panelist["id"]))
        history.append({
            "role": "user",
            "content": (
                "Voting time. Reply ONLY with a single JSON object of the form "
                '{"vote": "approve|reject|abstain", "confidence": <0..1>, "reasoning": "<one short sentence>"}. '
                "No prose outside the JSON."
            ),
        })

        adapter = _build_adapter(panelist, council)
        result = await adapter.generate(system_prompt, history)

        # Best-effort parse. Fall back to abstain if malformed.
        vote, confidence, reasoning = _parse_vote(result["content"])

        async with async_session_maker() as session:
            repo = CouncilRepository(session)
            await repo.cast_vote(council_id, str(panelist["id"]), vote, confidence, reasoning)
            await repo.add_message(
                council_id,
                kind="message",
                content=f"[VOTE] {vote.upper()} (conf={confidence:.2f}) — {reasoning}",
                round_num=council["max_rounds"] + 1,
                panelist_id=str(panelist["id"]),
                meta={**(result.get("meta") or {}), "is_vote": True},
            )
            await session.commit()


def _parse_vote(raw: str) -> tuple[str, float, str]:
    """Extract the first JSON object in the reply. Fallback = abstain.

    Responses that look like adapter errors ("[error: ...]") get a special
    `error` pseudo-vote so the resolution can distinguish them from genuine
    abstentions.
    """
    stripped = (raw or "").strip()
    if stripped.startswith("[error"):
        return "error", 0.0, stripped[:200]
    try:
        start = raw.index("{")
        end = raw.rindex("}") + 1
        obj = json.loads(raw[start:end])
        vote = str(obj.get("vote", "abstain")).lower()
        if vote not in ("approve", "reject", "abstain"):
            vote = "abstain"
        confidence = float(obj.get("confidence", 0.5))
        confidence = max(0.0, min(1.0, confidence))
        reasoning = str(obj.get("reasoning", ""))[:500]
        return vote, confidence, reasoning
    except Exception:
        return "abstain", 0.0, (raw or "")[:200]


async def _run_synthesize(council_id: str) -> None:
    """Produce a final resolution artifact (deterministic template, no LLM)."""
    async with async_session_maker() as session:
        repo = CouncilRepository(session)
        await repo.update_status(council_id, "synthesizing")
        council = await repo.get_by_id(council_id)
        panelists = await repo.list_panelists(council_id)
        votes = await repo.list_votes(council_id)
        all_messages = await repo.list_messages(council_id)
        await session.commit()

    name_by_pid = {str(p["id"]): p["display_name"] for p in panelists}

    approve = [v for v in votes if v["vote"] == "approve"]
    reject = [v for v in votes if v["vote"] == "reject"]
    abstain = [v for v in votes if v["vote"] == "abstain"]
    errored = [v for v in votes if v["vote"] == "error"]

    # Consensus score: weighted avg of signed confidence over non-errored votes,
    # range -1..1. Errored votes do not count toward the denominator.
    valid = [v for v in votes if v["vote"] != "error"]
    score = 0.0
    if valid:
        score = sum(
            (v["confidence"] if v["vote"] == "approve" else -v["confidence"] if v["vote"] == "reject" else 0.0)
            for v in valid
        ) / len(valid)

    lines = []
    lines.append(f"# Council resolution: {council['topic']}")
    lines.append("")
    lines.append(f"**Panel:** {', '.join(p['display_name'] for p in panelists)}")
    lines.append(f"**Rounds:** {council['max_rounds']}")
    result_parts = [f"{len(approve)} approve", f"{len(reject)} reject", f"{len(abstain)} abstain"]
    if errored:
        result_parts.append(f"{len(errored)} errored")
    lines.append(f"**Result:** {' · '.join(result_parts)}")
    lines.append(f"**Consensus score:** {score:+.2f}")
    lines.append("")
    lines.append("## Reasoning")
    for v in votes:
        nm = name_by_pid.get(str(v["panelist_id"]), "?")
        lines.append(f"- **{nm}** ({v['vote']}, {v['confidence']:.2f}): {v.get('reasoning') or '—'}")
    lines.append("")
    lines.append("## Key points from discussion")
    last_round_messages = [m for m in all_messages if m["kind"] == "message" and m["round_num"] == council["max_rounds"]]
    for m in last_round_messages:
        nm = name_by_pid.get(str(m.get("panelist_id") or ""), "?")
        lines.append(f"- **{nm}**: {m['content'][:300]}")

    resolution = "\n".join(lines)

    async with async_session_maker() as session:
        repo = CouncilRepository(session)
        await repo.update_status(
            council_id, "done", ended=True, resolution=resolution, consensus_score=score,
        )
        await repo.add_message(council_id, kind="resolution", content=resolution, round_num=council["max_rounds"] + 2)
        await session.commit()
