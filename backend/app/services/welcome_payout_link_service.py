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
RPC_FAILURES = (httpx.HTTPError, ValueError, KeyError, TypeError)
MAX_ITEMS = 500


def associated_token_account(wallet: Pubkey, mint: Pubkey) -> Pubkey:
    """Derive the Token-2022 associated token account address."""
    seeds = [bytes(wallet), bytes(TOKEN_2022), bytes(mint)]
    return Pubkey.find_program_address(seeds, ASSOCIATED_TOKEN_PROGRAM)[0]


def usable_token_account(value: dict | None, wallet: str, mint: str) -> bool:
    """Accept only an initialized Token-2022 account of this mint owned by this wallet."""
    data = value.get("data") if value else None
    parsed = data.get("parsed") if isinstance(data, dict) else None
    if not isinstance(parsed, dict) or not isinstance(parsed.get("info"), dict):
        return False
    info = parsed["info"]
    return (
        value is not None
        and value.get("owner") == str(TOKEN_2022)
        and parsed.get("type") == "account"
        and info.get("mint") == mint
        and info.get("owner") == wallet
        and info.get("state") == "initialized"
    )


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

    async def candidates(self, limit: int, after: tuple | None = None) -> list[dict]:
        async with self.session_factory() as db:
            try:
                return await WelcomePayoutLinkRepository(db).candidates(limit, after)
            finally:
                await db.rollback()

    def _check_genesis(self) -> None:
        genesis = rpc_call(self.client, str(self.config.rpc_endpoint), "getGenesisHash", [])
        if genesis != self.config.trusted_genesis_hash:
            raise NetworkMismatchError

    async def _bind_network(self) -> str | None:
        """Return a failure verdict unless the endpoint serves the trusted network."""
        try:
            await asyncio.to_thread(self._check_genesis)
        except NetworkMismatchError:
            return "configuration_error"
        except RPC_FAILURES:
            return "rpc_error"
        return None

    def _account_usable(self, address: Pubkey, wallet: str, mint: str) -> bool:
        params = [str(address), {"encoding": "jsonParsed", "commitment": "finalized"}]
        result = rpc_call(self.client, str(self.config.rpc_endpoint), "getAccountInfo", params)
        return usable_token_account(result["value"], wallet, mint)

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
        except ValueError:
            return "invalid_wallet", facts
        try:
            usable = await asyncio.to_thread(
                self._account_usable, address, facts["wallet"], facts["mint"]
            )
        except RPC_FAILURES:
            return "rpc_error", facts
        if not usable:
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

    async def link(self, page_size: int, apply: bool, cap: int = MAX_ITEMS) -> dict:
        """Walk candidates by keyset pages, so skipped grants never starve later ones."""
        counts: Counter[str] = Counter()
        intents: list[str] = []
        cursor, seen, bound = None, 0, False
        while seen < cap:
            page = await self.candidates(min(page_size, cap - seen), cursor)
            if not page:
                break
            if not bound and (failure := await self._bind_network()):
                return {"verdict": failure}
            bound = True
            for candidate in page:
                outcome, intent_id = await self.link_one(candidate["owner_user_id"], apply)
                counts[outcome] += 1
                if intent_id and outcome == "linked":
                    intents.append(intent_id)
            seen += len(page)
            cursor = (page[-1]["created_at"], page[-1]["grant_id"])
        totals = {name: counts.pop(name, 0) for name in ("linked", "replayed", "conflict")}
        return {
            "verdict": "ok",
            "applied": apply,
            **totals,
            "skipped": dict(counts),
            "intents": intents,
        }
