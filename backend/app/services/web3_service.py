"""Web3 service for on-chain ERC-20 token operations on Base."""

from __future__ import annotations

import logging
from functools import lru_cache

from web3 import AsyncWeb3
from web3.middleware import ExtraDataToPOAMiddleware

from app.core.config import get_settings

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────
# Minimal ABIs (only the functions we call)
# ──────────────────────────────────────────────

FACTORY_ABI = [
    {
        "type": "function",
        "name": "createToken",
        "inputs": [
            {"name": "projectId", "type": "string"},
            {"name": "name", "type": "string"},
            {"name": "symbol", "type": "string"},
        ],
        "outputs": [{"name": "tokenAddr", "type": "address"}],
        "stateMutability": "nonpayable",
    },
    {
        "type": "function",
        "name": "projectTokens",
        "inputs": [{"name": "", "type": "string"}],
        "outputs": [{"name": "", "type": "address"}],
        "stateMutability": "view",
    },
]

TOKEN_ABI = [
    {
        "type": "function",
        "name": "mint",
        "inputs": [
            {"name": "to", "type": "address"},
            {"name": "amount", "type": "uint256"},
            {"name": "reason", "type": "string"},
        ],
        "outputs": [],
        "stateMutability": "nonpayable",
    },
    {
        "type": "function",
        "name": "balanceOf",
        "inputs": [{"name": "account", "type": "address"}],
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view",
    },
    {
        "type": "function",
        "name": "totalSupply",
        "inputs": [],
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view",
    },
    {
        "type": "function",
        "name": "shareOf",
        "inputs": [{"name": "account", "type": "address"}],
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view",
    },
]


class Web3Service:
    """Oracle-side Web3 operations: deploy tokens and mint on Base."""

    def __init__(self) -> None:
        settings = get_settings()
        self._rpc_url = settings.base_rpc_url
        self._private_key = settings.oracle_private_key
        self._factory_address = settings.factory_contract_address
        self._w3: AsyncWeb3 | None = None

    # ──────────────────────────────────────────
    # Internal helpers
    # ──────────────────────────────────────────

    async def _web3(self) -> AsyncWeb3:
        if self._w3 is None:
            self._w3 = AsyncWeb3(AsyncWeb3.AsyncHTTPProvider(self._rpc_url))
            # Base uses PoA-like consensus — inject middleware to handle extraData
            self._w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
        return self._w3

    def _is_configured(self) -> bool:
        return bool(self._private_key and self._factory_address)

    async def _send_tx(self, fn) -> str:
        """Build, sign, send a contract transaction and return the tx hash."""
        w3 = await self._web3()
        account = w3.eth.account.from_key(self._private_key)
        nonce = await w3.eth.get_transaction_count(account.address)
        gas_price = await w3.eth.gas_price

        tx = await fn.build_transaction(
            {
                "from": account.address,
                "nonce": nonce,
                "gasPrice": gas_price,
            }
        )
        gas = await w3.eth.estimate_gas(tx)
        tx["gas"] = int(gas * 1.2)  # 20% buffer

        signed = account.sign_transaction(tx)
        tx_hash = await w3.eth.send_raw_transaction(signed.raw_transaction)
        receipt = await w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)

        if receipt.status != 1:
            raise RuntimeError(f"Transaction reverted: {tx_hash.hex()}")

        return tx_hash.hex()

    # ──────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────

    async def deploy_project_token(
        self,
        project_id: str,
        project_title: str,
    ) -> tuple[str, str]:
        """
        Call Factory.createToken() and return (contract_address, tx_hash).

        Derives a short symbol from the project title (first letters, max 6 chars).
        """
        if not self._is_configured():
            logger.warning("Web3 not configured — skipping token deployment")
            return ("", "")

        # Build a ticker-like symbol: first letters of each word, upper, max 6
        words = project_title.upper().split()
        symbol = "".join(w[0] for w in words if w)[:6] or "SPORE"
        name = f"{project_title[:40]} Shares"

        w3 = await self._web3()
        factory = w3.eth.contract(
            address=AsyncWeb3.to_checksum_address(self._factory_address),
            abi=FACTORY_ABI,
        )

        tx_hash = await self._send_tx(
            factory.functions.createToken(project_id, name, symbol)
        )

        # Read back the deployed address from the mapping
        contract_address = await factory.functions.projectTokens(project_id).call()
        logger.info(
            "Token deployed for project %s at %s (tx=%s)",
            project_id,
            contract_address,
            tx_hash,
        )
        return (contract_address, tx_hash)

    async def mint_tokens(
        self,
        contract_address: str,
        to_wallet: str,
        amount: int,
        reason: str = "",
    ) -> str:
        """
        Call ProjectShares.mint() and return tx_hash.
        No-op (returns "") if Web3 is not configured.
        """
        if not self._is_configured():
            logger.warning("Web3 not configured — skipping mint")
            return ""

        w3 = await self._web3()
        token = w3.eth.contract(
            address=AsyncWeb3.to_checksum_address(contract_address),
            abi=TOKEN_ABI,
        )
        tx_hash = await self._send_tx(
            token.functions.mint(
                AsyncWeb3.to_checksum_address(to_wallet),
                amount,
                reason,
            )
        )
        logger.info(
            "Minted %d tokens to %s on contract %s (tx=%s)",
            amount,
            to_wallet,
            contract_address,
            tx_hash,
        )
        return tx_hash

    async def get_balance(self, contract_address: str, wallet: str) -> int:
        """Return raw ERC-20 balance (token points, not ETH)."""
        if not self._is_configured():
            return 0

        w3 = await self._web3()
        token = w3.eth.contract(
            address=AsyncWeb3.to_checksum_address(contract_address),
            abi=TOKEN_ABI,
        )
        return await token.functions.balanceOf(
            AsyncWeb3.to_checksum_address(wallet)
        ).call()

    async def get_share_bps(self, contract_address: str, wallet: str) -> int:
        """Return ownership share in basis points (0–10000 = 0%–100%)."""
        if not self._is_configured():
            return 0

        w3 = await self._web3()
        token = w3.eth.contract(
            address=AsyncWeb3.to_checksum_address(contract_address),
            abi=TOKEN_ABI,
        )
        return await token.functions.shareOf(
            AsyncWeb3.to_checksum_address(wallet)
        ).call()


@lru_cache(maxsize=1)
def get_web3_service() -> Web3Service:
    return Web3Service()
