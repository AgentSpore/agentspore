"""Initial language: URL link, stored choice, .ru host, navigator.languages, then English."""

import json
import sys
import unittest
from urllib.parse import urlsplit

from playwright.sync_api import expect
import test_training as core

RU_HOST = 'safety.example.ru'
CASES = (
    ('ru host', RU_HOST, ['en-US'], None, '', 'ru'),
    ('navigator ru-RU', None, ['ru-RU'], None, '', 'ru'),
    ('navigator de-DE has german', None, ['de-DE'], None, '', 'de'),
    ('later navigator entry', None, ['sv-SE', 'ru'], None, '', 'ru'),
    ('unsupported navigator', None, ['sv-SE'], None, '', 'en'),
    ('simplified chinese', None, ['zh-CN'], None, '', 'zh-Hans'),
    ('traditional chinese has no catalog', None, ['zh-TW'], None, '', 'en'),
    ('stored beats host', RU_HOST, ['ru-RU'], 'de', '', 'de'),
    ('stored beats navigator', None, ['ru-RU'], 'en', '', 'en'),
    ('unsupported stored falls back', RU_HOST, ['en-US'], 'xx', '', 'ru'),
    ('shared link beats stored', None, ['en-US'], 'de', '?lang=ru', 'ru'),
)


class LanguageTest(unittest.TestCase):
    """Open the real page under a chosen host, browser language list and stored value."""

    @classmethod
    def setUpClass(cls):
        cls.addClassCleanup(core.TrainingTest.doClassCleanups)
        core.TrainingTest.setUpClass()
        cls.browser, cls.base = core.TrainingTest.browser, core.TrainingTest.base

    def open_page_as(self, context, host, languages, stored, query='', failing=None):
        """Map the pretend host onto the local server and block everything else."""
        origin = f'http://{host or "127.0.0.1"}:{urlsplit(self.base).port}/'

        def receive(route):
            url = route.request.url
            if url.endswith('/_agentspore/events'):
                route.fulfill(status=204)
            elif failing and url.endswith(failing):
                route.fulfill(status=503)
            elif url.startswith(origin):
                route.fulfill(response=route.fetch(url=self.base + url[len(origin):]))
            else:
                route.abort('blockedbyclient')

        context.route('**/*', receive)
        context.add_init_script(f'''
            Object.defineProperty(navigator, "languages", {{ get: () => {json.dumps(languages)} }});
            if ({json.dumps(stored)} !== null && localStorage.getItem("language") === null)
                localStorage.setItem("language", {json.dumps(stored)});''')
        page = context.new_page()
        page.goto(origin + query, wait_until='networkidle')
        expect(page.locator('#language')).to_be_enabled()
        return page

    def test_initial_language_precedence(self):
        """Every rule sets selector, html lang and the localized title together."""
        for name, host, languages, stored, query, expected in CASES:
            with self.subTest(name), self.browser.new_context() as context:
                page = self.open_page_as(context, host, languages, stored, query)
                title = json.loads((core.ROOT / f'content/{expected}.json').read_text())['ui']['title']
                expect(page.locator('html')).to_have_attribute('lang', expected)
                expect(page.locator('#language')).to_have_value(expected)
                self.assertEqual(page.title(), title)

    def test_failed_initial_catalog_falls_back_to_english_with_notice(self):
        """A linked language whose catalog fails shows English and says why."""
        with self.browser.new_context() as context:
            page = self.open_page_as(context, None, ['en-US'], None, '?lang=ru', failing='/content/ru.json')
            expect(page.locator('html')).to_have_attribute('lang', 'en')
            expect(page.locator('#load-error')).to_be_visible()

    def test_english_start_hides_the_load_notice(self):
        """A normal start keeps the load notice hidden."""
        with self.browser.new_context() as context:
            page = self.open_page_as(context, None, ['en-US'], None)
            expect(page.locator('#load-error')).to_be_hidden()

    def test_selector_choice_is_remembered_and_nothing_else_is_stored(self):
        """A manual choice beats the host on reload; only the language key is written."""
        with self.browser.new_context() as context:
            page = self.open_page_as(context, RU_HOST, ['en-US'], None)
            expect(page.locator('html')).to_have_attribute('lang', 'ru')
            page.locator('#language').select_option('en')
            expect(page.locator('html')).to_have_attribute('lang', 'en')
            page.reload(wait_until='networkidle')
            expect(page.locator('#language')).to_be_enabled()
            expect(page.locator('html')).to_have_attribute('lang', 'en')
            self.assertEqual(page.evaluate('Object.entries(localStorage)'), [['language', 'en']])


if __name__ == '__main__':
    unittest.main(argv=[sys.argv[0]], verbosity=2)
