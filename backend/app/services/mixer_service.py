"""MixerService — business logic for Privacy Mixer.

Crypto: AES-256-GCM encryption of sensitive fragments.
Leak detection: scan agent outputs for accidentally revealed original values.
Assembly: decrypt fragments, substitute placeholders, build final output.
"""

import hashlib
import os
import re
import secrets

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes
from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.repositories.mixer_repo import MixerRepository, get_mixer_repo

from loguru import logger

PBKDF2_ITERATIONS = 600_000
KEY_LENGTH = 32  # AES-256
SALT_LENGTH = 32
IV_LENGTH = 12  # AES-GCM nonce

# Regex for {{PRIVATE:value}} and {{PRIVATE:category:value}}
PRIVATE_MARKER_RE = re.compile(r"\{\{PRIVATE(?::(\w+))?:(.*?)\}\}", re.DOTALL)

TERMINAL_CHUNK_STATUSES = {"approved", "failed"}


class MixerService:
    """Privacy Mixer: fragment extraction, encryption, leak detection, assembly."""

    def __init__(self, db: AsyncSession, repo: MixerRepository):
        self.db = db
        self.repo = repo

    # ── Crypto ──────────────────────────────────────────────────────────

    @staticmethod
    def _derive_key(passphrase: str, salt: bytes) -> bytes:
        """PBKDF2-HMAC-SHA256 → 32-byte AES-256 key."""
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=KEY_LENGTH,
            salt=salt,
            iterations=PBKDF2_ITERATIONS,
        )
        return kdf.derive(passphrase.encode("utf-8"))

    @staticmethod
    def _encrypt_value(plaintext: str, key: bytes, iv: bytes) -> bytes:
        """AES-256-GCM encrypt. Each fragment gets a unique nonce derived from iv + counter."""
        aesgcm = AESGCM(key)
        return aesgcm.encrypt(iv, plaintext.encode("utf-8"), None)

    @staticmethod
    def _decrypt_value(ciphertext: bytes, key: bytes, iv: bytes) -> str:
        """AES-256-GCM decrypt."""
        aesgcm = AESGCM(key)
        return aesgcm.decrypt(iv, ciphertext, None).decode("utf-8")

    @staticmethod
    def _hash_passphrase(passphrase: str, salt: bytes) -> str:
        """SHA-256 hash for passphrase verification (separate derivation from encryption key)."""
        h = hashlib.pbkdf2_hmac("sha256", passphrase.encode("utf-8"), salt + b"verify", PBKDF2_ITERATIONS)
        return h.hex()

    @staticmethod
    def _verify_passphrase(passphrase: str, salt: bytes, stored_hash: str) -> bool:
        """Verify passphrase against stored hash."""
        computed = hashlib.pbkdf2_hmac("sha256", passphrase.encode("utf-8"), salt + b"verify", PBKDF2_ITERATIONS)
        return secrets.compare_digest(computed.hex(), stored_hash)

    # ── Fragment Extraction ─────────────────────────────────────────────

    @staticmethod
    def extract_private_markers(text: str) -> list[dict]:
        """Parse {{PRIVATE:value}} and {{PRIVATE:category:value}} markers."""
        fragments = []
        seen_placeholders: set[str] = set()

        for match in PRIVATE_MARKER_RE.finditer(text):
            category = match.group(1)
            value = match.group(2)
            original = match.group(0)

            while True:
                placeholder = f"MIX_{secrets.token_hex(3)}"
                if placeholder not in seen_placeholders:
                    seen_placeholders.add(placeholder)
                    break

            fragments.append({
                "original": original,
                "value": value,
                "placeholder": placeholder,
                "category": category,
            })

        return fragments

    @staticmethod
    def replace_markers_with_placeholders(text: str, fragments: list[dict]) -> str:
        """Replace {{PRIVATE:...}} markers with {{MIX_xxxxxx}} placeholders."""
        result = text
        for f in fragments:
            result = result.replace(f["original"], "{{" + f["placeholder"] + "}}", 1)
        return result

    # ── Session Lifecycle ───────────────────────────────────────────────

    async def create_session(
        self, user_id: str,
        title: str, description: str | None,
        task_text: str, passphrase: str,
        fragment_ttl_hours: int = 24,
    ) -> dict:
        """Create mixer session: parse markers, encrypt fragments, store."""
        fragments = self.extract_private_markers(task_text)
        if not fragments:
            raise ValueError("No {{PRIVATE:...}} markers found in task text")

        salt = os.urandom(SALT_LENGTH)
        iv = os.urandom(IV_LENGTH)
        key = self._derive_key(passphrase, salt)
        passphrase_hash = self._hash_passphrase(passphrase, salt)

        sanitized_text = self.replace_markers_with_placeholders(task_text, fragments)

        session = await self.repo.create_session(
            user_id, title, description, sanitized_text,
            salt, passphrase_hash, iv, fragment_ttl_hours,
        )
        session_id = str(session["id"])

        for i, f in enumerate(fragments):
            frag_iv = int.from_bytes(iv, "big") ^ i
            frag_iv_bytes = frag_iv.to_bytes(IV_LENGTH, "big")
            encrypted = self._encrypt_value(f["value"], key, frag_iv_bytes)
            await self.repo.create_fragment(session_id, f["placeholder"], encrypted, f["category"])

        await self.repo.log_audit(
            session_id, "user", user_id, "session_created",
            details={"fragment_count": len(fragments), "ttl_hours": fragment_ttl_hours},
        )

        logger.info("Mixer session %s created with %d fragments", session_id, len(fragments))
        return {
            **session,
            "fragment_count": len(fragments),
            "placeholders": [{"placeholder": f["placeholder"], "category": f["category"]} for f in fragments],
            "sanitized_text": sanitized_text,
        }

    async def start_session(self, session_id: str, user_id: str) -> dict:
        """Validate and start session: dispatch chunks to agents."""
        session = await self.repo.get_session_by_id(session_id)
        if not session:
            raise ValueError("Session not found")
        if str(session["user_id"]) != user_id:
            raise ValueError("Not your session")
        if session["status"] != "draft":
            raise ValueError(f"Cannot start session in status '{session['status']}'")

        chunks = await self.repo.get_session_chunks(session_id)
        if not chunks:
            raise ValueError("Session has no chunks")

        warnings = self.check_provider_diversity(chunks)

        for chunk in chunks:
            await self.repo.update_chunk_status(str(chunk["id"]), "ready")

        await self.repo.update_session_status(session_id, "running")

        await self.repo.log_audit(
            session_id, "user", user_id, "session_started",
            details={"chunk_count": len(chunks), "warnings": warnings},
        )

        logger.info("Mixer session %s started with %d chunks", session_id, len(chunks))
        return {"session_id": session_id, "status": "running", "warnings": warnings}

    @staticmethod
    def check_provider_diversity(chunks: list[dict]) -> list[str]:
        """Warn if multiple chunks use agents with the same LLM provider."""
        provider_chunks: dict[str, list[str]] = {}
        for c in chunks:
            provider = c.get("model_provider", "unknown")
            provider_chunks.setdefault(provider, []).append(c.get("agent_name", "?"))

        warnings = []
        for provider, agents in provider_chunks.items():
            if len(agents) > 1:
                warnings.append(
                    f"Provider '{provider}' is used by {len(agents)} chunks "
                    f"({', '.join(agents)}). Consider using different providers for better privacy."
                )
        return warnings

    # ── Leak Detection ──────────────────────────────────────────────────

    def scan_for_leaks(
        self, output_text: str, fragments: list[dict],
        key: bytes, session_iv: bytes,
    ) -> tuple[bool, str | None]:
        """Decrypt fragments and check if any values appear in agent output."""
        output_lower = output_text.lower()
        leaked = []

        for i, frag in enumerate(fragments):
            frag_iv = int.from_bytes(session_iv, "big") ^ i
            frag_iv_bytes = frag_iv.to_bytes(IV_LENGTH, "big")

            try:
                original_value = self._decrypt_value(frag["encrypted_value"], key, frag_iv_bytes)
            except Exception:
                continue

            if len(original_value) <= 2:
                continue

            if original_value.lower() in output_lower:
                leaked.append(frag["placeholder"])

        if leaked:
            details = f"Leaked fragments: {', '.join(leaked)}"
            return True, details

        return False, None

    # ── Agent Interaction ───────────────────────────────────────────────

    async def agent_complete_chunk(self, chunk_id: str, output_text: str) -> dict:
        """Agent submits output. Scan for leaks, then set status."""
        chunk = await self.repo.get_chunk_by_id(chunk_id)
        if not chunk:
            raise ValueError("Chunk not found")
        if chunk["status"] != "active":
            raise ValueError(f"Cannot complete chunk in status '{chunk['status']}'")

        session_id = str(chunk["session_id"])
        session = await self.repo.get_session_by_id(session_id)

        fragments = await self.repo.get_fragments(session_id)
        key = self._derive_key_from_session(session)

        if key and fragments:
            has_leak, details = self.scan_for_leaks(
                output_text, fragments, key, bytes(session["encryption_iv"]),
            )
        else:
            has_leak, details = False, None

        if has_leak:
            await self.repo.update_chunk_status(
                chunk_id, "failed",
                output_text=output_text, leak_detected=True, leak_details=details,
            )
            await self.repo.log_audit(
                session_id, "system", session_id, "leak_scan_failed",
                target_type="chunk", target_id=chunk_id,
                details={"leak_details": details},
            )
            logger.warning("Leak detected in chunk %s: %s", chunk_id, details)
            return {"status": "failed", "leak_detected": True, "leak_details": details}

        await self.repo.update_chunk_status(chunk_id, "review", output_text=output_text)
        await self.repo.log_audit(
            session_id, "system", session_id, "leak_scan_passed",
            target_type="chunk", target_id=chunk_id,
        )

        await self._check_all_chunks_done(session_id)
        return {"status": "review", "leak_detected": False}

    def _derive_key_from_session(self, session: dict) -> bytes | None:
        """Cannot derive key without passphrase. Return None."""
        return None

    async def _check_all_chunks_done(self, session_id: str) -> None:
        """If all chunks are in terminal state, move session to assembling."""
        chunks = await self.repo.get_session_chunks(session_id)
        all_done = all(c["status"] in {"review", "approved", "failed"} for c in chunks)
        if all_done:
            await self.repo.update_session_status(session_id, "assembling")

    # ── Chunk Actions ───────────────────────────────────────────────────

    async def approve_chunk(self, session_id: str, chunk_id: str, user_id: str) -> dict:
        chunk = await self.repo.get_chunk_by_id(chunk_id)
        if not chunk or str(chunk["session_id"]) != session_id:
            raise ValueError("Chunk not found in this session")
        if chunk["status"] != "review":
            raise ValueError(f"Cannot approve chunk in status '{chunk['status']}'")

        await self.repo.update_chunk_status(chunk_id, "approved")
        await self.repo.log_audit(
            session_id, "user", user_id, "chunk_approved",
            target_type="chunk", target_id=chunk_id,
        )

        await self._check_all_chunks_done(session_id)
        return await self.repo.get_chunk_by_id(chunk_id)

    async def reject_chunk(
        self, session_id: str, chunk_id: str,
        user_id: str, feedback: str,
    ) -> dict:
        chunk = await self.repo.get_chunk_by_id(chunk_id)
        if not chunk or str(chunk["session_id"]) != session_id:
            raise ValueError("Chunk not found in this session")
        if chunk["status"] != "review":
            raise ValueError(f"Cannot reject chunk in status '{chunk['status']}'")

        await self.repo.update_chunk_status(chunk_id, "active")
        await self.repo.insert_message(
            chunk_id, "system", str(session_id),
            f"Chunk rejected. Feedback: {feedback}",
        )
        await self.repo.log_audit(
            session_id, "user", user_id, "chunk_rejected",
            target_type="chunk", target_id=chunk_id,
            details={"feedback": feedback},
        )
        return await self.repo.get_chunk_by_id(chunk_id)

    # ── Assembly ────────────────────────────────────────────────────────

    async def assemble_output(self, session_id: str, passphrase: str, user_id: str) -> str:
        """Verify passphrase, decrypt fragments, substitute in chunk outputs, return final text."""
        session = await self.repo.get_session_by_id(session_id)
        if not session:
            raise ValueError("Session not found")
        if str(session["user_id"]) != user_id:
            raise ValueError("Not your session")
        if session["status"] not in ("assembling", "running"):
            raise ValueError(f"Cannot assemble session in status '{session['status']}'")

        salt = bytes(session["passphrase_salt"])
        if not self._verify_passphrase(passphrase, salt, session["passphrase_hash"]):
            await self.repo.log_audit(session_id, "user", user_id, "passphrase_failed")
            raise ValueError("Invalid passphrase")

        await self.repo.log_audit(session_id, "user", user_id, "passphrase_verified")

        key = self._derive_key(passphrase, salt)
        session_iv = bytes(session["encryption_iv"])

        fragments = await self.repo.get_fragments(session_id)
        placeholder_map: dict[str, str] = {}

        for i, frag in enumerate(fragments):
            frag_iv = int.from_bytes(session_iv, "big") ^ i
            frag_iv_bytes = frag_iv.to_bytes(IV_LENGTH, "big")
            original_value = self._decrypt_value(bytes(frag["encrypted_value"]), key, frag_iv_bytes)
            placeholder_map[frag["placeholder"]] = original_value

        chunks = await self.repo.get_session_chunks(session_id)
        parts: list[str] = []

        for chunk in chunks:
            if chunk["status"] == "failed":
                parts.append(f"## {chunk['title']} [FAILED — leak detected]\n{chunk.get('leak_details', 'Leak detected')}")
                continue
            if not chunk.get("output_text"):
                parts.append(f"## {chunk['title']} [NO OUTPUT]")
                continue

            output = chunk["output_text"]

            output_lower = output.lower()
            for placeholder, original in placeholder_map.items():
                if len(original) > 2 and original.lower() in output_lower:
                    await self.repo.log_audit(
                        session_id, "system", session_id, "leak_detected_at_assembly",
                        target_type="chunk", target_id=str(chunk["id"]),
                        details={"placeholder": placeholder},
                    )

            for placeholder, original in placeholder_map.items():
                output = output.replace("{{" + placeholder + "}}", original)

            parts.append(f"## {chunk['title']}\n{output}")

        assembled = "\n\n".join(parts)

        encrypted_output = self._encrypt_value(assembled, key, session_iv)
        await self.repo.update_session(session_id, assembled_output=encrypted_output.hex())
        await self.repo.update_session_status(session_id, "completed")

        await self.repo.log_audit(
            session_id, "user", user_id, "assembly_completed",
            details={"chunk_count": len(chunks), "output_length": len(assembled)},
        )

        logger.info("Mixer session %s assembled (%d chars)", session_id, len(assembled))
        return assembled

    # ── Cancel ──────────────────────────────────────────────────────────

    async def cancel_session(self, session_id: str, user_id: str) -> dict:
        session = await self.repo.get_session_by_id(session_id)
        if not session:
            raise ValueError("Session not found")
        if str(session["user_id"]) != user_id:
            raise ValueError("Not your session")
        if session["status"] in ("completed", "cancelled"):
            raise ValueError("Cannot cancel this session")

        await self.repo.update_session_status(session_id, "cancelled")
        deleted = await self.repo.delete_fragments(session_id)
        await self.repo.log_audit(
            session_id, "user", user_id, "session_cancelled",
            details={"fragments_deleted": deleted},
        )
        return await self.repo.get_session_by_id(session_id)

    # ── Cleanup ─────────────────────────────────────────────────────────

    async def cleanup_expired(self) -> int:
        """Background task: delete fragments from expired sessions."""
        expired = await self.repo.get_expired_sessions()
        count = 0
        for sess in expired:
            sid = str(sess["id"])
            deleted = await self.repo.cleanup_expired_fragments(sid)
            if deleted:
                await self.repo.log_audit(
                    sid, "system", sid, "fragments_cleaned_up",
                    details={"fragments_deleted": deleted},
                )
                count += 1
                logger.info("Mixer cleanup: deleted %d fragments from session %s", deleted, sid)
        return count


def get_mixer_service(
    db: AsyncSession = Depends(get_db),
    repo: MixerRepository = Depends(get_mixer_repo),
) -> MixerService:
    return MixerService(db, repo)
