"""Synthetic fixtures: throwaway keys, no network, no real addresses."""

import base64
import hashlib
import json
import os
from pathlib import Path
from typing import Any

import pytest
from solders.hash import Hash
from solders.instruction import AccountMeta, Instruction
from solders.keypair import Keypair
from solders.message import Message
from solders.pubkey import Pubkey
from solders.token.associated import get_associated_token_address

TOKEN_2022 = Pubkey.from_string("TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb")
ATA_PROGRAM = Pubkey.from_string("ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL")
SYSTEM = Pubkey.default()
BLOCKHASH = Hash.from_bytes(bytes(range(32)))
LIMITS = {
    "max_amount_per_transfer": 1000,
    "max_amount_per_recipient_24h": 1500,
    "max_amount_total_24h": 2500,
    "max_transfers_24h": 3,
    "max_ata_creations_24h": 2,
}
DECIMALS = 6


class Env:
    """Paths, keys and message builders for one test."""

    def __init__(self, root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.root = root
        self.signer = Keypair()
        self.mint = Keypair().pubkey()
        creds = root / "creds"
        creds.mkdir()
        (creds / "keypair").write_text(json.dumps(list(bytes(self.signer))))
        monkeypatch.setenv("CREDENTIALS_DIRECTORY", str(creds))
        self.pause = root / "paused"
        self.config = root / "config.toml"
        self.ledger = root / "ledger.sqlite"
        self.write_config()

    def write_config(self, **overrides: object) -> None:
        values: dict[str, object] = {
            "keypair_credential": "keypair",
            "mint": str(self.mint),
            "decimals": DECIMALS,
            "pause_file": str(self.pause),
            "ledger_path": str(self.ledger),
            **LIMITS,
        } | overrides
        lines = [
            f'{k} = "{v}"' if isinstance(v, str) else f"{k} = {v}"
            for k, v in values.items()
            if v is not None
        ]
        self.config.write_text("\n".join(lines) + "\n")

    @property
    def ata(self) -> Pubkey:
        return get_associated_token_address(self.signer.pubkey(), self.mint, TOKEN_2022)

    def message(
        self,
        amount: int = 100,
        dest: Pubkey | None = None,
        **tweak: Any,
    ) -> bytes:
        """Build the exact valid message; keyword tweaks produce one malformation each."""
        authority = tweak.get("authority") or self.signer.pubkey()
        program = tweak.get("program") or TOKEN_2022
        decimals = tweak.get("decimals", DECIMALS)
        data = bytes([12]) + amount.to_bytes(8, "little") + bytes([decimals])
        metas = [
            AccountMeta(tweak.get("source") or self.ata, False, True),
            AccountMeta(tweak.get("mint") or self.mint, False, False),
            AccountMeta(dest or Keypair().pubkey(), False, True),
            AccountMeta(authority, True, False),
        ]
        instructions = [Instruction(program, data, metas)]
        if tweak.get("extra"):
            instructions.append(Instruction(program, data, metas))
        payer = tweak.get("payer") or self.signer.pubkey()
        return bytes(Message.new_with_blockhash(instructions, payer, BLOCKHASH))

    def message_b(self, wallet: Pubkey | None = None, amount: int = 100, **tweak: Any) -> bytes:
        """Create-idempotent then transfer; keyword tweaks produce one malformation each."""
        wallet = wallet or Keypair().pubkey()
        signer = self.signer.pubkey()
        ata = get_associated_token_address(wallet, self.mint, TOKEN_2022)
        create_metas = [
            AccountMeta(tweak.get("payer") or signer, True, True),
            AccountMeta(tweak.get("ata") or ata, False, True),
            AccountMeta(tweak.get("owner") or wallet, False, False),
            AccountMeta(tweak.get("create_mint") or self.mint, False, False),
            AccountMeta(tweak.get("system") or SYSTEM, False, False),
            AccountMeta(tweak.get("token_program") or TOKEN_2022, False, False),
        ]
        if tweak.get("swap"):
            create_metas[1], create_metas[2] = create_metas[2], create_metas[1]
        create = Instruction(
            tweak.get("ata_program") or ATA_PROGRAM, tweak.get("data", b"\x01"), create_metas
        )
        data = bytes([12]) + amount.to_bytes(8, "little") + bytes([DECIMALS])
        transfer_metas = [
            AccountMeta(self.ata, False, True),
            AccountMeta(self.mint, False, False),
            AccountMeta(ata, False, True),
            AccountMeta(signer, True, False),
        ]
        transfer = Instruction(TOKEN_2022, data, transfer_metas)
        instructions = [create, transfer] + ([transfer] if tweak.get("extra") else [])
        return bytes(Message.new_with_blockhash(instructions, signer, BLOCKHASH))

    def request(self, raw: bytes, name: str = "request.json", digest: str | None = None) -> Path:
        body = {
            "intent_id": "00000000-0000-4000-8000-000000000001",
            "worker_id": "00000000-0000-4000-8000-000000000002",
            "epoch": 1,
            "genesis_hash": str(Hash.from_bytes(bytes(32))),
            "last_valid_block_height": "1000",
            "recent_blockhash": str(BLOCKHASH),
            "format_version": 1,
            "unsigned_message_base64": base64.b64encode(raw).decode(),
            "message_digest": digest or hashlib.sha256(raw).hexdigest(),
        }
        path = self.root / name
        path.write_text(json.dumps(body))
        os.chmod(path, 0o600)
        return path


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Env:
    return Env(tmp_path, monkeypatch)
