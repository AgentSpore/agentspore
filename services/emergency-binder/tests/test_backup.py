"""Native WebCrypto protocol, restore rejection, races and outward privacy."""

import base64
import json
from pathlib import Path

from playwright.sync_api import expect
from test_questionnaire import PUBLIC, QuestionnaireBrowser, VALUES

PUBLIC["/src/backup.js"] = "src/backup.js"
PASSWORD = "D3-" + "synthetic-only-" * 2
NATIVE = """async ({envelope, password, plaintext}) => {
  const decode = text => Uint8Array.from(atob(text), char => char.charCodeAt(0));
  const material = await crypto.subtle.importKey('raw', new TextEncoder().encode(password), 'PBKDF2', false, ['deriveKey']);
  const key = await crypto.subtle.deriveKey({name:'PBKDF2', hash:'SHA-256', iterations:600000,
    salt:decode(envelope.salt)}, material, {name:'AES-GCM', length:256}, false, ['encrypt','decrypt']);
  const parameters = {name:'AES-GCM', iv:decode(envelope.iv), tagLength:128,
    additionalData:new TextEncoder().encode('agentspore.emergency-binder:v1')};
  if (plaintext === null) {
    const bytes = await crypto.subtle.decrypt(parameters, key, decode(envelope.ciphertext));
    return JSON.parse(new TextDecoder('utf-8', {fatal:true}).decode(bytes));
  }
  parameters.iv = crypto.getRandomValues(new Uint8Array(12));
  const bytes = await crypto.subtle.encrypt(parameters, key, new TextEncoder().encode(JSON.stringify(plaintext)));
  return {...envelope, iv:btoa(String.fromCharCode(...parameters.iv)),
    ciphertext:btoa(String.fromCharCode(...new Uint8Array(bytes)))};
}"""
GATE = """() => {
  window.__d3NativeDerive ??= crypto.subtle.deriveKey.bind(crypto.subtle);
  window.__d3Gate = {entered:false};
  crypto.subtle.deriveKey = (...args) => {
    window.__d3Gate.entered = true;
    return new Promise(resolve => {window.__d3Gate.release = resolve;})
      .then(() => window.__d3NativeDerive(...args));
  };
}"""


class BackupTest(QuestionnaireBrowser):
    """Use the real app and native crypto; substitute only external HTTP replies."""

    def begin_export(self, page, password=PASSWORD):
        """Fill actual password controls without accessing application state."""
        expect(page.locator("#backup-controls")).to_be_visible()
        page.locator("#backup-password").fill(password)
        page.locator("#backup-confirm").fill(password)
        page.locator("#backup-export").click()

    def export_file(self, page):
        """Read a browser-owned encrypted download, never print its contents."""
        with page.expect_download() as saved:
            self.begin_export(page)
        self.assertEqual(saved.value.suggested_filename, "family-folder.enc.json")
        raw = Path(saved.value.path()).read_bytes()
        expect(page.locator("#backup-controls")).to_have_attribute("aria-busy", "false")
        return json.loads(raw), raw

    def import_file(self, page, envelope, password=PASSWORD):
        """Send an owned synthetic file through the actual file input."""
        raw = json.dumps(envelope).encode() if isinstance(envelope, dict) else envelope
        page.locator("#backup-file").set_input_files({
            "name": "D3-name-must-not-echo.enc.json", "mimeType": "application/json", "buffer": raw})
        page.locator("#backup-password").fill(password)
        page.locator("#backup-import").click()

    def native_document(self, page, envelope, plaintext=None):
        """Independently implement the published wire protocol with native WebCrypto."""
        return page.evaluate(NATIVE, {"envelope": envelope, "password": PASSWORD, "plaintext": plaintext})

    def release_crypto(self, page):
        """Wait for actual operation settlement, not an arbitrary sleep."""
        page.evaluate("window.__d3Gate.release()")
        expect(page.locator("#backup-controls")).to_have_attribute("aria-busy", "false")
        page.evaluate("() => { crypto.subtle.deriveKey = window.__d3NativeDerive; }")

    def test_distinct_envelopes_independent_decrypt_and_explicit_restore(self):
        """Randomized exports conform to the protocol and retain all hidden care fields."""
        with self.browser.new_context() as context:
            page, _ = self.open_page(context)
            self.fill_sections(page, VALUES)
            page.locator("#country").select_option("GB")
            page.locator("#language").select_option("ru")
            page.locator("#mode").select_option("care")
            first, raw = self.export_file(page)
            second, _ = self.export_file(page)
            self.assertEqual(set(first), {"format", "version", "kdf", "iterations", "salt", "cipher", "iv", "ciphertext"})
            self.assertEqual({key: first[key] for key in ("format", "version", "kdf", "iterations", "cipher")}, {
                "format": "agentspore-emergency-binder", "version": 1, "kdf": "PBKDF2-SHA256",
                "iterations": 600000, "cipher": "AES-256-GCM"})
            self.assertIs(type(first["version"]), int)
            self.assertIs(type(first["iterations"]), int)
            self.assertLessEqual(len(raw), 98304)
            ciphertext = base64.b64decode(first["ciphertext"], validate=True)
            self.assertTrue(16 <= len(ciphertext) <= 65552)
            for field, size in (("salt", 16), ("iv", 12)):
                decoded = base64.b64decode(first[field], validate=True)
                self.assertEqual(len(decoded), size)
                self.assertEqual(base64.b64encode(decoded).decode(), first[field])
            self.assertTrue(all(first[key] != second[key] for key in ("salt", "iv", "ciphertext")))
            self.assertFalse(any(value.encode() in raw for pair in VALUES.values() for value in pair))
            self.assertFalse(PASSWORD.encode() in raw)
            document = self.native_document(page, first)
            self.assertEqual(document, {"version": 1, "country": "GB", "lang": "ru", "mode": "care",
                "sections": {key: dict(zip(("primary", "notes"), values)) for key, values in VALUES.items()}})
            page.locator("#section-primary").fill("D3-BEFORE-RESTORE")
            self.import_file(page, first)
            expect(page.locator("#restore-confirm")).to_be_enabled()
            expect(page.locator("#section-primary")).to_have_value("D3-BEFORE-RESTORE")
            page.locator("#restore-confirm").click()
            page.locator("#mode").select_option("family")
            self.assert_sections(page, VALUES)
            expect(page.locator("#backup-password")).to_have_value("")
            expect(page.locator("#backup-confirm")).to_have_value("")

    def test_invalid_files_share_one_error_preserve_original_and_bound_kdf(self):
        """Authenticated corruption and invalid schemas cannot partially replace data."""
        with self.browser.new_context() as context:
            page, _ = self.open_page(context)
            self.fill_sections(page, VALUES)
            envelope, _ = self.export_file(page)
            cipher = bytearray(base64.b64decode(envelope["ciphertext"]))
            cases = [(envelope, PASSWORD + "wrong"), (dict(envelope, extra=True), PASSWORD),
                     (dict(envelope, version=2), PASSWORD), (dict(envelope, kdf="PBKDF2-SHA512"), PASSWORD),
                     (dict(envelope, cipher="AES-CBC"), PASSWORD), (b" " * 98305, PASSWORD),
                     (dict(envelope, ciphertext=base64.b64encode(cipher[:-1]).decode()), PASSWORD)]
            for offset in (0, len(cipher) - 1):
                changed = bytearray(cipher)
                changed[offset] ^= 1
                cases.append((dict(envelope, ciphertext=base64.b64encode(changed).decode()), PASSWORD))
            document = self.native_document(page, envelope)
            invalid = (dict(document, extra=True), dict(document, mode="unknown"), {"version": 1, "sections": {}})
            for plaintext in invalid:
                cases.append((self.native_document(page, envelope, plaintext), PASSWORD))
            error = None
            for file, password in cases:
                self.import_file(page, file, password)
                expect(page.locator("#status")).to_have_attribute("data-state", "error")
                message = page.locator("#status").inner_text()
                self.assertTrue(bool(message.strip()))
                self.assertFalse("D3-name-must-not-echo" in message or password in message)
                error = message if error is None else error
                self.assertTrue(message == error)
                expect(page.locator("#restore-confirm")).to_be_hidden()
                self.assert_sections(page, VALUES)
            page.evaluate("""() => {
              window.__d3Calls = 0;
              const original = crypto.subtle.deriveKey.bind(crypto.subtle);
              crypto.subtle.deriveKey = (...args) => {window.__d3Calls++; return original(...args);};
            }""")
            self.import_file(page, dict(envelope, iterations=600001))
            expect(page.locator("#status")).to_have_attribute("data-state", "error")
            self.assertEqual(page.evaluate("window.__d3Calls"), 0)
            self.assert_sections(page, VALUES)

    def test_native_delay_keeps_export_snapshot_but_clear_and_edits_cancel_restore(self):
        """Pause real derivation; Clear cancels delivery, ordinary edits do not change exports."""
        with self.browser.new_context() as context:
            page, _ = self.open_page(context)
            self.fill_sections(page, VALUES)
            envelope, _ = self.export_file(page)
            page.evaluate(GATE)
            with page.expect_download() as saved:
                self.begin_export(page)
                page.wait_for_function("window.__d3Gate.entered")
                page.locator("#section-primary").fill("D3-EDIT-DURING-EXPORT")
                page.locator("#mode").select_option("care")
                self.release_crypto(page)
            snapshot = self.native_document(page, json.loads(Path(saved.value.path()).read_bytes()))
            self.assertEqual(snapshot["mode"], "family")
            self.assertTrue(snapshot["sections"]["care"]["primary"] == VALUES["care"][0])
            downloads = []
            page.on("download", lambda saved: downloads.append(saved))
            for operation in ("export", "import"):
                page.evaluate(GATE)
                if operation == "export":
                    self.begin_export(page)
                else:
                    self.import_file(page, envelope)
                page.wait_for_function("window.__d3Gate.entered")
                page.once("dialog", lambda dialog: dialog.accept())
                page.locator("#clear-folder").click()
                self.release_crypto(page)
                self.assertFalse(downloads)
                expect(page.locator("#restore-confirm")).to_be_hidden()
            page.evaluate(GATE)
            self.import_file(page, envelope)
            page.wait_for_function("window.__d3Gate.entered")
            page.locator("#section-primary").fill("D3-KEEP-NEW-EDIT")
            self.release_crypto(page)
            expect(page.locator("#restore-confirm")).to_be_hidden()
            expect(page.locator("#section-primary")).to_have_value("D3-KEEP-NEW-EDIT")
            self.import_file(page, envelope)
            expect(page.locator("#restore-confirm")).to_be_enabled()
            page.locator("#restore-cancel").click()
            expect(page.locator("#restore-confirm")).to_be_hidden()
            expect(page.locator("#section-primary")).to_have_value("D3-KEEP-NEW-EDIT")

    def test_password_limits_unavailable_crypto_and_failed_metrics_keep_data_private(self):
        """Real downloads tolerate gateway failures; invalid passwords cannot export."""
        for status in (503, "abort"):
            with self.subTest(status=status), self.browser.new_context() as context:
                page, observed = self.open_page(context, status)
                page.on("pageerror", lambda error: observed["console"].append(str(error)))
                self.fill_sections(page, VALUES)
                expect(page.locator("#backup-controls")).to_be_visible()
                for password, confirm in (("x" * 11, "x" * 11), ("x" * 129, "x" * 129), (PASSWORD, PASSWORD + "x")):
                    page.locator("#backup-password").fill(password)
                    page.locator("#backup-confirm").fill(confirm)
                    expect(page.locator("#backup-export")).to_be_disabled()
                page.evaluate("""() => {
                  for (const id of ['backup-password','backup-confirm']) {
                    const node = document.getElementById(id);
                    node.value = String.fromCharCode(0xd800).repeat(12);
                    node.dispatchEvent(new Event('input', {bubbles:true}));
                  }
                }""")
                expect(page.locator("#backup-export")).to_be_disabled()
                for length in (12, 128):
                    page.locator("#backup-password").fill("p" * length)
                    page.locator("#backup-confirm").fill("p" * length)
                    expect(page.locator("#backup-export")).to_be_enabled()
                self.export_file(page)
                self.import_file(page, self.export_file(page)[0])
                expect(page.locator("#restore-confirm")).to_be_enabled()
                page.locator("#restore-cancel").click()
                page.locator("#print-folder").click()
                self.assertTrue(page.pdf(format="A4").startswith(b"%PDF"))
                events = observed["events"]
                self.assertEqual(sum(row["event"] == "backup_download" for row in events), 2)
                self.assertTrue(all(set(row) == {"event", "section", "lang"} for row in events))
                outward = json.dumps(observed, ensure_ascii=False)
                private_values = [PASSWORD, "x" * 11, "x" * 129, "p" * 12, "D3-name-must-not-echo"] + [value for pair in VALUES.values() for value in pair]
                self.assertFalse(any(value in outward for value in private_values))
                self.assertEqual(page.evaluate("location.search + location.hash"), "")
                self.assertEqual(page.evaluate("localStorage.length + sessionStorage.length"), 0)
                self.assertEqual(context.cookies(), [])
                self.assertEqual(page.evaluate("async () => (await indexedDB.databases()).length"), 0)
                self.assertEqual(page.evaluate("async () => (await navigator.serviceWorker.getRegistrations()).length"), 0)
        with self.browser.new_context() as context:
            context.add_init_script("Object.defineProperty(window, 'isSecureContext', {value:false});")
            page, _ = self.open_page(context)
            expect(page.locator("#backup-export")).to_be_disabled()
            expect(page.locator("#backup-import")).to_be_disabled()
            self.assertTrue(bool(page.locator("#status").inner_text().strip()))
            self.fill_sections(page, VALUES)
            page.locator("#print-folder").click()
            self.assertTrue(page.pdf(format="A4").startswith(b"%PDF"))
