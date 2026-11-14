"""Welcome reward journey on a REAL local stand: no HTTP mocks, mail read from a capture file.

Persona "newcomer without GitHub": email registration, wallet, review, operator approval,
payout display states, disposable-email change, signed-out confirmation page.

Requires a local stand (never a shared one); every variable is explicit, otherwise the suite skips:
  WELCOME_E2E_FE        frontend origin, e.g. http://localhost:13000 (production build)
  WELCOME_E2E_MAIL      JSONL file the stand's mail capture appends to ({"to": [...], "html": "..."})
  WELCOME_E2E_BACKEND   backend directory; the operator CLI runs there via `uv run` with the stand env
  WELCOME_E2E_PSQL      command prefix that runs SQL on the stand DB, SQL is appended as the last argument
  WELCOME_E2E_SHOTS     screenshot directory (<step>-<390|1280>.png)
The stand must have the welcome program enabled and funded with synthetic rows only.
"""

import json
import os
import re
import secrets
import shlex
import subprocess
import time
import unittest
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

ENV = {k: os.environ.get(f"WELCOME_E2E_{k}") for k in ("FE", "MAIL", "BACKEND", "PSQL", "SHOTS")}
WIDTHS = {390: 844, 1280: 900}
ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
PASSWORD = "Journey-pass-182x"
# 4xx the journey provokes on purpose; anything else with status >= 400 fails the console gate.
EXPECTED_4XX = {("PATCH", "/api/v1/users/solana-wallet", 422)}


def base58(raw: bytes) -> str:
    """Synthetic Solana-shaped address from random bytes; never a real wallet."""
    number, out = int.from_bytes(raw, "big"), ""
    while number:
        number, rest = divmod(number, 58)
        out = ALPHABET[rest] + out
    return out


def throwaway_pubkey() -> str:
    return base58(b"\x01" + secrets.token_bytes(31))


def mails_to(address: str, subject: str) -> list[dict]:
    path = Path(ENV["MAIL"])
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    return [r for r in rows if address in r["to"] and subject in r["subject"]]


def mail_link(address: str, subject: str, timeout=15) -> str:
    """Wait for the captured mail and return its single link."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        found = mails_to(address, subject)
        if found:
            links = set(re.findall(r'href="([^"]+)"', found[-1]["html"]))
            assert len(links) == 1, links
            return links.pop()
        time.sleep(0.3)
    raise AssertionError(f"no mail {subject!r} to {address}")


def sql(statement: str) -> str:
    out = subprocess.run([*shlex.split(ENV["PSQL"]), statement], capture_output=True, text=True)
    assert out.returncode == 0, f"psql failed: {out.stderr.strip()[-400:]}"
    return out.stdout.strip()


def operator(*args: str) -> str:
    """Run the real operator CLI against the stand DB; returns stdout, fails on non-zero."""
    cmd = ["uv", "run", "python", "-m", "app.cli.welcome_reward_decisions", *args]
    done = subprocess.run(cmd, cwd=ENV["BACKEND"], capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, (done.returncode, done.stdout[-600:], done.stderr[-600:])
    return done.stdout


@unittest.skipUnless(all(ENV.values()), "WELCOME_E2E_* stand variables are not set")
class WelcomeJourney(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch()
        cls.shots = Path(ENV["SHOTS"])
        cls.shots.mkdir(parents=True, exist_ok=True)
        cls.problems: list[str] = []
        cls.run_id = secrets.token_hex(3)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()

    def context(self, width=1280):
        ctx = self.browser.new_context(viewport={"width": width, "height": WIDTHS[width]}, locale="en-US")
        page = ctx.new_page()
        page.on("pageerror", lambda e: self.problems.append(f"pageerror {e}"))
        page.on("console", lambda m: m.type == "error" and self.problems.append(f"console {m.text[:160]}"))

        def watch(response):
            req = response.request
            path = response.url.split("?")[0].replace(ENV["FE"], "").replace("http://localhost:18000", "")
            if response.status >= 400 and (req.method, path, response.status) not in EXPECTED_4XX:
                self.problems.append(f"http {response.status} {req.method} {path}")

        page.on("response", watch)
        return ctx, page

    def snap(self, page, name):
        """Screenshot the page at 1280 and 390, asserting no horizontal scroll at 390."""
        for width, height in WIDTHS.items():
            page.set_viewport_size({"width": width, "height": height})
            page.wait_for_timeout(250)
            overflow = page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
            if width == 390:
                self.assertLessEqual(overflow, 0, f"{name}: horizontal scroll {overflow}px at 390")
            page.screenshot(path=str(self.shots / f"{name}-{width}.png"), full_page=True)
            card = page.locator("section[aria-labelledby=welcome-reward-title]")
            if card.count():
                card.screenshot(path=str(self.shots / f"{name}-card-{width}.png"))
        page.set_viewport_size({"width": 1280, "height": 900})

    def register(self, page, email):
        page.goto(f"{ENV['FE']}/signup")
        page.locator("#auth-name").fill("Journey Newcomer")
        page.locator("#auth-email").fill(email)
        page.locator("#auth-password").fill(PASSWORD)
        page.locator("form button[type=submit]").click()
        link = mail_link(email, "Verify your AgentSpore")
        page.goto(link)
        page.goto(f"{ENV['FE']}/login")
        page.locator("#auth-email").fill(email)
        page.locator("#auth-password").fill(PASSWORD)
        page.locator("form button[type=submit]").click()
        page.wait_for_url(re.compile(r"/profile|/dashboard|/workspace|/$"))

    def card(self, page):
        page.goto(f"{ENV['FE']}/profile")
        card = page.locator("section[aria-labelledby=welcome-reward-title]")
        expect(card.get_by_role("heading", name="Welcome reward")).to_be_visible(timeout=15000)
        expect(card.locator("ol li").first).to_be_visible(timeout=15000)
        return card

    def current_step(self, card):
        return card.locator("ol li[aria-current=step]").inner_text().splitlines()[0]

    def test_newcomer_journey(self):
        email = f"newcomer-{self.run_id}@example.org"
        ctx, page = self.context()
        self.register(page, email)

        # Step 1: registered + verified -> the card asks for a wallet, inline.
        card = self.card(page)
        self.assertEqual(self.current_step(card), "Add wallet")
        expect(card.get_by_label("Solana wallet address")).to_be_visible()
        self.snap(page, "01-wallet-needed")

        # Invalid wallet: honest error, no dead end.
        card.get_by_label("Solana wallet address").fill("not-a-wallet")
        card.get_by_role("button", name="Save wallet").click()
        expect(card.get_by_text("This is not a valid Solana address.")).to_be_visible()
        self.snap(page, "02-wallet-invalid")

        # Keyboard: the field is reachable and submit works with Enter.
        wallet = throwaway_pubkey()
        field = card.get_by_label("Solana wallet address")
        field.fill(wallet)
        field.press("Enter")
        expect(card.get_by_text("We are checking your application. You do not need to do anything.")).to_be_visible(timeout=15000)
        self.assertEqual(self.current_step(card), "Review")
        self.snap(page, "03-in-review")

        # Operator sees the application, masked, and approves it.
        listing = operator("review")
        self.assertNotIn(email, listing, "review must mask the email")
        self.assertIn(f"{email[0]}***@example.org", listing)
        dry = operator("approve-clean")
        self.assertEqual(sql("SELECT count(*) FROM welcome_reward_grants WHERE state<>'application'"), "0", dry)
        applied = operator("approve-clean", "--apply")
        print("approve-clean --apply:", applied.strip()[-300:])
        again = operator("approve-clean", "--apply")
        print("approve-clean again:", again.strip()[-300:])
        page.reload()
        card = self.card(page)
        self.snap(page, "04-approved")

        # Payout display states from realistic DB rows (no network send).
        for stage in ("pending", "sent", "confirmed"):
            self.set_payout(email, wallet, stage)
            page.reload()
            card = self.card(page)
            expect(card.get_by_role("status").filter(has_text=re.compile("payout|Sending|Sent", re.I))).to_be_visible()
            self.snap(page, f"05-payout-{stage}")
        ctx.close()
        self.assertEqual(self.problems, [])

    def set_payout(self, email, wallet, stage):
        """Insert/advance a welcome payout intent for the user's grant, as the real delivery tables hold it."""
        grant = sql(f"SELECT g.id FROM welcome_reward_grants g JOIN users u ON u.id=g.owner_user_id WHERE u.email='{email}'")
        self.assertTrue(grant)
        key = lambda: throwaway_pubkey()  # noqa: E731
        if stage == "pending":
            sql(
                "INSERT INTO payout_delivery_intents(producer_namespace,allocation_id,genesis_hash,mint,source,destination,"
                f"authority,fee_payer,recipient,program,decimals,raw_amount) VALUES('welcome','{grant}','{key()}',"
                f"'5ZkjEjfDAPuSg7pRxCRJsJuZ8FByRSyAgAA8SLMMpump','{key()}','{key()}','{key()}','{key()}','{wallet}',"
                f"'TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb',6,'10000000000')"
            )
        intent = sql(f"SELECT intent_id FROM payout_delivery_intents WHERE producer_namespace='welcome' AND allocation_id='{grant}'")
        if stage == "sent":
            worker = secrets.token_hex(16)
            worker = f"{worker[:8]}-{worker[8:12]}-4{worker[13:16]}-8{worker[17:20]}-{worker[20:]}"
            sql(
                "INSERT INTO payout_delivery_claims(intent_id,epoch,state,worker_id,expires_at) "
                f"VALUES('{intent}',1,'claimed','{worker}',now()+interval '1 hour');"
                f"UPDATE payout_delivery_claims SET state='prepared' WHERE intent_id='{intent}';"
                "INSERT INTO payout_delivery_attempts(intent_id,epoch,worker_id,signed_bytes,first_signature,recent_blockhash,"
                f"last_valid_block_height) VALUES('{intent}',1,'{worker}',decode('00','hex'),decode('{secrets.token_hex(64)}','hex'),"
                f"'{key()}','1')"
            )
        if stage == "confirmed":
            sql(
                "INSERT INTO payout_delivery_confirmations(intent_id,finalized_slot,verifier_version,evidence_digest) "
                f"VALUES('{intent}','1',1,repeat('a',64))"
            )

    def test_disposable_email_change_and_signed_out_link(self):
        old = f"temp-{self.run_id}@mailinator.com"
        new = f"regular-{self.run_id}@example.org"
        ctx, page = self.context()
        self.register(page, old)
        card = self.card(page)
        expect(card.get_by_text("temporary mailbox service")).to_be_visible()
        expect(card.get_by_label("New email address")).to_be_visible()
        self.snap(page, "10-disposable-hold")

        card.get_by_label("New email address").fill(new)
        card.get_by_label("Current password").fill(PASSWORD)
        card.get_by_role("button", name="Change email").click()
        expect(card.get_by_text("Check the inbox of the new address")).to_be_visible(timeout=15000)
        self.snap(page, "11-change-link-sent")

        link = mail_link(new, "Confirm your new AgentSpore email")
        self.assertIn("/confirm-email-change#token=", link)

        # Signed-out path: a fresh context opens the emailed link.
        out_ctx, out_page = self.context()
        out_page.goto(link)
        expect(out_page.get_by_text("Sign in to finish changing your email")).to_be_visible(timeout=15000)
        expect(out_page.get_by_role("link", name="Sign in")).to_be_visible()
        self.snap(out_page, "20-confirm-signed-out")
        out_ctx.close()
        bare_ctx, bare = self.context()
        bare.goto(f"{ENV['FE']}/confirm-email-change")
        expect(bare.get_by_text("This page needs the link from the email")).to_be_visible(timeout=15000)
        self.snap(bare, "21-confirm-no-token")
        bare_ctx.close()

        # Signed-in path: the link completes the change and the card moves on.
        page.goto(link)
        expect(page.get_by_text("Your email address was changed")).to_be_visible(timeout=15000)
        self.snap(page, "12-email-changed")
        card = self.card(page)
        expect(card.get_by_text("temporary mailbox service")).to_have_count(0)
        self.snap(page, "13-after-change")
        ctx.close()
        self.assertEqual(self.problems, [])

    @unittest.expectedFailure
    def test_confirm_page_is_readable(self):
        """KNOWN DEFECT (#182 acceptance): /confirm-email-change paints light text on a white page.

        Remove expectedFailure when the page gets the site's dark ground; it then guards the contrast.
        """
        ctx, page = self.context()
        page.goto(f"{ENV['FE']}/confirm-email-change")
        expect(page.get_by_role("heading", name="Confirm your new email")).to_be_visible(timeout=15000)
        ground = page.evaluate("getComputedStyle(document.body).backgroundColor")
        channels = [int(v) for v in re.findall(r"\d+", ground)[:3]]
        ctx.close()
        self.assertLess(sum(channels) / 3, 60, f"page ground {ground} is not dark")


if __name__ == "__main__":
    unittest.main()
