"""Native control boundaries, keyboard flow and cached offline settings."""

import itertools
import json
import unittest

from playwright.sync_api import expect
import test_questionnaire as core


class UIBoundariesTest(core.QuestionnaireBrowser):
    """Reuse the public browser fixture without importing its test class."""

    def test_unicode_limit_rejects_without_truncation_and_recovers(self):
        """Count astral code points correctly and reject an unpaired surrogate."""
        with self.browser.new_context() as context:
            page, observed = self.open_page(context)
            primary = page.locator('#section-primary')
            valid, invalid = '🦊' * 1000, '🦊' * 1001
            primary.fill(valid)
            expect(primary).to_have_attribute('aria-invalid', 'false')
            expect(page.locator('#print-folder')).to_be_enabled()
            expect(primary).to_have_value(valid)
            primary.fill(invalid)
            expect(primary).to_have_attribute('aria-invalid', 'true')
            expect(primary).to_have_value(invalid)
            expect(page.locator('#print-folder')).to_be_disabled()
            self.assertTrue(page.locator('#status').inner_text().strip())
            primary.fill('D3-REPAIRED')
            expect(primary).to_have_attribute('aria-invalid', 'false')
            expect(page.locator('#print-folder')).to_be_enabled()
            expect(page.locator('#status')).to_be_empty()
            primary.evaluate('''el => {
                Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')
                    .set.call(el, String.fromCharCode(0xD800));
                el.dispatchEvent(new Event('input', {bubbles: true}));
            }''')
            expect(primary).to_have_attribute('aria-invalid', 'true')
            expect(page.locator('#print-folder')).to_be_disabled()
            self.assertTrue(primary.evaluate('el => el.value.length === 1 && el.value.charCodeAt(0) === 0xD800'))
            primary.fill('D3-REPAIRED-AGAIN')
            expect(primary).to_have_attribute('aria-invalid', 'false')
            expect(page.locator('#print-folder')).to_be_enabled()
            outward = json.dumps(observed, ensure_ascii=False)
            for private in (valid, invalid, 'D3-REPAIRED', 'D3-REPAIRED-AGAIN'):
                self.assertNotIn(private, outward)
            self.assertNotIn('\\ud800', outward.lower())

    def test_keyboard_review_and_cached_offline_settings_preserve_fields(self):
        """Both languages support native keyboard editing at mobile and desktop widths."""
        for lang, width in itertools.product(('en', 'ru'), (390, 1280)):
            with self.subTest(lang=lang, width=width), self.browser.new_context() as context:
                page, observed = self.open_page(context)
                page.set_viewport_size({'width': width, 'height': 900})
                page.locator('#language').select_option(lang)
                catalog = json.loads((core.ROOT / f'content/{lang}.json').read_text())
                home = page.locator('#section-nav [data-section="home"]')
                home.focus()
                expect(home).to_be_focused()
                page.keyboard.press('Enter')
                expect(page.locator('#section-title')).to_be_focused()
                expect(page.locator('#section-title')).to_have_text(catalog['sections']['home']['title'])
                page.keyboard.press('Tab')
                expect(page.locator('#section-primary')).to_be_focused()
                page.keyboard.type('D3-KEYBOARD-PRIVATE')
                page.keyboard.press('Tab')
                expect(page.locator('#section-notes')).to_be_focused()
                page.keyboard.type('D3-KEYBOARD-NOTES')
                page.keyboard.press('Tab')
                reviewed = page.locator('#mark-complete')
                expect(reviewed).to_be_focused()
                expect(reviewed).to_have_text(catalog['ui']['markComplete'])
                with page.expect_request('**/_agentspore/events') as sent:
                    page.keyboard.press('Enter')
                sent.value.response()
                expect(reviewed).to_be_disabled()
                expect(page.locator('#status')).to_be_empty()
                self.assertIn(dict(event='section_open', section='home', lang=lang), observed['events'])
                self.assertIn(dict(event='section_complete', section='home', lang=lang), observed['events'])
                other = 'ru' if lang == 'en' else 'en'
                context.set_offline(True)
                try:
                    page.locator('#language').select_option(other)
                    page.locator('#country').select_option('GB')
                    page.locator('#mode').select_option('care')
                    translated = json.loads((core.ROOT / f'content/{other}.json').read_text())
                    expect(page.locator('html')).to_have_attribute('lang', other)
                    expect(page.locator('#section-title')).to_have_text(translated['sections']['home']['title'])
                    expect(page.locator('#country')).to_have_value('GB')
                    expect(page.locator('#mode')).to_have_value('care')
                    expect(page.locator('#section-primary')).to_have_value('D3-KEYBOARD-PRIVATE')
                    expect(page.locator('#section-notes')).to_have_value('D3-KEYBOARD-NOTES')
                    self.assertTrue(page.evaluate('document.documentElement.scrollWidth <= innerWidth'))
                finally:
                    context.set_offline(False)
                self.assertEqual(page.evaluate('localStorage.length + sessionStorage.length'), 0)
                self.assertEqual(context.cookies(), [])
                outward = json.dumps(observed, ensure_ascii=False)
                self.assertNotIn('D3-KEYBOARD-PRIVATE', outward)
                self.assertNotIn('D3-KEYBOARD-NOTES', outward)
