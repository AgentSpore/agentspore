"""EIP-191 signature verification in PATCH /users/wallet (eth-account)."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from eth_account import Account
from eth_account.messages import encode_defunct
from fastapi import HTTPException

from app.api.v1 import ownership
from app.schemas.ownership import WalletConnectRequest

USER = SimpleNamespace(id="u1")


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


@pytest.fixture
def message(account):
    # same template as frontend/src/components/WalletButton.tsx handleLink()
    return f"AgentSpore wallet link\nAddress: {account.address}\nTimestamp: 1760000000000"


def _sign(account, message: str) -> str:
    # personal_sign returns 0x-prefixed hex
    return "0x" + account.sign_message(encode_defunct(text=message)).signature.hex()


async def test_valid_signature_connects_wallet(account, message, repo, db):
    body = WalletConnectRequest(
        wallet_address=account.address, signature=_sign(account, message), message=message
    )
    result = await ownership.connect_wallet(body, USER, db)

    assert result == {"wallet_address": account.address.lower(), "status": "connected"}
    repo.update_user_wallet.assert_awaited_once_with(db, "u1", account.address)
    db.commit.assert_awaited_once()


async def test_signature_from_other_key_rejected(account, message, repo, db):
    other = Account.create()
    body = WalletConnectRequest(
        wallet_address=account.address, signature=_sign(other, message), message=message
    )
    with pytest.raises(HTTPException) as exc:
        await ownership.connect_wallet(body, USER, db)

    assert exc.value.status_code == 400
    repo.update_user_wallet.assert_not_awaited()


async def test_malformed_signature_rejected(account, message, repo, db):
    body = WalletConnectRequest(wallet_address=account.address, signature="0xdead", message=message)
    with pytest.raises(HTTPException) as exc:
        await ownership.connect_wallet(body, USER, db)

    assert exc.value.status_code == 400


async def test_wallet_already_linked_to_other_account_returns_409(account, message, repo, db):
    repo.check_wallet_uniqueness.return_value = True
    body = WalletConnectRequest(
        wallet_address=account.address, signature=_sign(account, message), message=message
    )
    with pytest.raises(HTTPException) as exc:
        await ownership.connect_wallet(body, USER, db)

    assert exc.value.status_code == 409
    repo.update_user_wallet.assert_not_awaited()
