"""Server-side payout network configuration and the live network checks bound to it."""

import os
import stat

import httpx
from pydantic import ValidationError

from app.schemas.payout_delivery import NetworkConfig, TransferIntent
from app.services.payout_delivery_service import NetworkMismatchError, rpc_call


class ConfigurationError(Exception):
    """The network configuration is absent or unsafe; the cause is never disclosed."""


def load_network_config(path: str) -> NetworkConfig:
    """Read a regular owner-only JSON file; every failure is one opaque error."""
    if not path:
        raise ConfigurationError
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor) as handle:
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
                raise ConfigurationError
            payload = handle.read(65537)
        return NetworkConfig.model_validate_json(payload if len(payload) <= 65536 else "")
    except (OSError, ValidationError, ValueError):
        raise ConfigurationError from None


def intent_matches_config(intent: TransferIntent, config: NetworkConfig) -> bool:
    """Require the configured network, the signer as authority and fee payer, and the source."""
    return (
        intent.genesis_hash == config.trusted_genesis_hash
        and intent.authority == config.signer_pubkey
        and intent.fee_payer == config.signer_pubkey
        and intent.source == config.source_token_account
    )


def fetch_live_blockhash(client: httpx.Client, config: NetworkConfig) -> tuple[str, str]:
    """Bind the endpoint to the trusted genesis, then return a fresh blockhash and its height."""
    endpoint = str(config.rpc_endpoint)
    if rpc_call(client, endpoint, "getGenesisHash", []) != config.trusted_genesis_hash:
        raise NetworkMismatchError
    value = rpc_call(client, endpoint, "getLatestBlockhash", [{"commitment": "finalized"}])["value"]
    return value["blockhash"], str(value["lastValidBlockHeight"])


def fetch_finalized_height(client: httpx.Client, config: NetworkConfig) -> int:
    """Bind the endpoint to the trusted genesis, then return the finalized block height."""
    endpoint = str(config.rpc_endpoint)
    if rpc_call(client, endpoint, "getGenesisHash", []) != config.trusted_genesis_hash:
        raise NetworkMismatchError
    height = rpc_call(client, endpoint, "getBlockHeight", [{"commitment": "finalized"}])
    if type(height) is not int or not 0 <= height < 2**64:
        raise ValueError
    return height
