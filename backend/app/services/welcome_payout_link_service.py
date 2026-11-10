"""Bind available welcome grants to payout intents; never signs or sends."""

import asyncio
from collections import Counter
from uuid import UUID

import httpx
from pydantic import ValidationError
from solders.pubkey import Pubkey
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.repositories.payout_delivery_repo import PayoutDeliveryRepository
from app.repositories.welcome_payout_link_repo import NAMESPACE, WelcomePayoutLinkRepository
from app.repositories.welcome_reward_repo import WelcomeRewardRepository
from app.schemas.payout_delivery import AllocationIntent, NetworkConfig
from app.services.payout_delivery_service import NetworkMismatchError, admit_intent, rpc_call
from app.services.welcome_reward_service import WelcomeRewardService

TOKEN_2022 = Pubkey.from_string("TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb")
ASSOCIATED_TOKEN_PROGRAM = Pubkey.from_string("ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL")
OUTCOMES = {"accepted": "linked", "replayed": "replayed", "conflict": "conflict"}


def associated_token_account(wallet: Pubkey, mint: Pubkey) -> Pubkey:
    """Derive the Token-2022 associated token account address."""
    seeds = [bytes(wallet), bytes(TOKEN_2022), bytes(mint)]
    return Pubkey.find_program_address(seeds, ASSOCIATED_TOKEN_PROGRAM)[0]


class WelcomePayoutLinkService:
    """Holds the trusted network binding and a reusable RPC client."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        config: NetworkConfig,
        client: httpx.Client,
    ):
        self.session_factory = session_factory
        self.config = config
        self.client = client

    async def candidates(self, limit: int) -> list[dict]:
        async with self.session_factory() as db:
            try:
                return await WelcomePayoutLinkRepository(db).candidates(limit)
            finally:
                await db.rollback()

    def _account_exists(self, address: Pubkey) -> bool:
        """Bind the endpoint to the trusted genesis, then look the account up on chain."""
        endpoint = str(self.config.rpc_endpoint)
        genesis = rpc_call(self.client, endpoint, "getGenesisHash", [])
        if genesis != self.config.trusted_genesis_hash:
            raise NetworkMismatchError
        params = [str(address), {"encoding": "base64", "commitment": "finalized"}]
        value = rpc_call(self.client, endpoint, "getAccountInfo", params)["value"]
        return value is not None and value["owner"] == str(TOKEN_2022)

    @staticmethod
    async def _available(db: AsyncSession, owner: UUID) -> bool:
        """Advance reservation until available; the owner row stays locked in this transaction."""
        service = WelcomeRewardService(WelcomeRewardRepository(db))
        view = await service.advance(owner)
        if view.state == "frozen" and view.reason == "reserved":
            view = await service.advance(owner)
        return view.state == "available" and view.reason == "available"

    async def _precheck(self, owner: UUID) -> tuple[str | None, dict]:
        """Decide without holding a transaction across the network call."""
        async with self.session_factory() as db:
            try:
                if not await self._available(db, owner):
                    return "not_available", {}
                facts = await WelcomePayoutLinkRepository(db).payout_facts(owner) or {}
            finally:
                await db.rollback()
        if not facts.get("wallet"):
            return "no_wallet", facts
        try:
            address = associated_token_account(
                Pubkey.from_string(facts["wallet"]), Pubkey.from_string(facts["mint"])
            )
            exists = await asyncio.to_thread(self._account_exists, address)
        except ValueError:
            return "invalid_wallet", facts
        except (httpx.HTTPError, KeyError, TypeError, NetworkMismatchError):
            return "rpc_error", facts
        if not exists:
            return "no_token_account", facts
        return None, facts | {"destination": str(address)}

    def _intent(self, facts: dict) -> AllocationIntent:
        return AllocationIntent(
            producer_namespace=NAMESPACE,
            allocation_id=str(facts["grant_id"]),
            genesis_hash=self.config.trusted_genesis_hash,
            mint=facts["mint"],
            program=facts["token_program"],
            source=self.config.source_token_account,
            destination=facts["destination"],
            authority=self.config.signer_pubkey,
            fee_payer=self.config.signer_pubkey,
            recipient=facts["wallet"],
            decimals=facts["decimals"],
            raw_amount=str(facts["amount_raw"]),
        )

    async def link_one(self, owner: UUID, apply: bool) -> tuple[str, str | None]:
        """Return (outcome, intent_id); one transaction covers advance and admission."""
        reason, checked = await self._precheck(owner)
        if reason:
            return reason, None
        async with self.session_factory() as db:
            try:
                if not await self._available(db, owner):
                    return "not_available", None
                facts = await WelcomePayoutLinkRepository(db).payout_facts(owner)
                if not facts or facts["wallet"] != checked["wallet"]:
                    return "wallet_changed", None
                try:
                    intent = self._intent(facts | {"destination": checked["destination"]})
                except ValidationError:
                    return "invalid_intent", None
                result = await admit_intent(PayoutDeliveryRepository(db), intent)
                outcome = OUTCOMES.get(result["verdict"])
                if outcome is None:
                    raise RuntimeError("admission failed")
                if apply and outcome != "conflict":
                    await db.commit()
                return outcome, result.get("intent_id") if apply else None
            finally:
                await db.rollback()

    async def link(self, limit: int, apply: bool) -> dict:
        """Process candidates one transaction at a time and report counts and intent ids only."""
        counts: Counter[str] = Counter()
        intents: list[str] = []
        for candidate in await self.candidates(limit):
            outcome, intent_id = await self.link_one(candidate["owner_user_id"], apply)
            counts[outcome] += 1
            if intent_id and outcome == "linked":
                intents.append(intent_id)
        totals = {name: counts.pop(name, 0) for name in ("linked", "replayed", "conflict")}
        return {
            "verdict": "ok",
            "applied": apply,
            **totals,
            "skipped": dict(counts),
            "intents": intents,
        }
