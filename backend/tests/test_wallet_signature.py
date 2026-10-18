"""EIP-191 signature verification in PATCH /users/wallet (eth-account)."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from eth_account import Account
from eth_account.messages import encode_defunct
from fastapi import HTTPException

from app.api.v1 import ownership
from app.schemas.ownership import WalletConnectRequest

MESSAGE = "Connect wallet to AgentSpore"


@pytest.fixture
def account():
    return Account.create()


@pytest.fixture
def repo(monkeypatch):
    mock = SimpleNamespace(
        check_wallet_uniqueness=AsyncMock(return_value=False),
        update_user_wallet=AsyncMock(),
    )
    monkeypatch.setattr(ownership, "ownership_repo", mock)
    return mock


@pytest.fixture
def db():
    session = MagicMock()
    session.commit = AsyncMock()
    return session


def _sign(account, message: str) -> str:
    return account.sign_message(encode_defunct(text=message)).signature.hex()


async def test_valid_signature_connects_wallet(account, repo, db):
    body = WalletConnectRequest(
        wallet_address=account.address, signature=_sign(account, MESSAGE), message=MESSAGE
    )
    result = await ownership.connect_wallet(body, SimpleNamespace(id="u1"), db)

    assert result == {"wallet_address": account.address.lower(), "status": "connected"}
    repo.update_user_wallet.assert_awaited_once()


async def test_signature_from_other_key_rejected(account, repo, db):
    other = Account.create()
    body = WalletConnectRequest(
        wallet_address=account.address, signature=_sign(other, MESSAGE), message=MESSAGE
    )
    with pytest.raises(HTTPException) as exc:
        await ownership.connect_wallet(body, SimpleNamespace(id="u1"), db)

    assert exc.value.status_code == 400
    repo.update_user_wallet.assert_not_awaited()


async def test_malformed_signature_rejected(account, repo, db):
    body = WalletConnectRequest(wallet_address=account.address, signature="0xdead", message=MESSAGE)
    with pytest.raises(HTTPException) as exc:
        await ownership.connect_wallet(body, SimpleNamespace(id="u1"), db)

    assert exc.value.status_code == 400
