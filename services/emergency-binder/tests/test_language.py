"""Initial language: stored choice, .ru host, navigator.languages, then English."""

import json
import unittest
from urllib.parse import urlsplit

from playwright.sync_api import expect
import test_questionnaire as core

RU_HOST = 'binder.example.ru'
core.PUBLIC.setdefault('/src/backup.js', 'src/backup.js')
CASES = (
    ('ru host', RU_HOST, ['en-US'], None, 'ru'),
    ('navigator ru-RU', None, ['ru-RU'], None, 'ru'),
    ('later navigator entry', None, ['de-DE', 'ru'], None, 'ru'),
    ('unsupported navigator', None, ['de-DE'], None, 'en'),
    ('stored beats host', RU_HOST, ['ru-RU'], 'en', 'en'),
    ('stored beats navigator', None, ['ru-RU'], 'en', 'en'),
    ('unsupported stored falls back', RU_HOST, ['en-US'], 'xx', 'ru'),
)


class LanguageTest(core.QuestionnaireBrowser):
    """Open the real page under a chosen host, browser language list and stored value."""

    def open_page_as(self, context, host, languages, stored):
        """Map the pretend host onto the local server and block everything else."""
        origin = f'http://{host or "127.0.0.1"}:{urlsplit(self.base).port}/'

        def receive(route):
            url = route.request.url
            if url.endswith('/_agentspore/events'):
                route.fulfill(status=204)
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
        page.goto(origin, wait_until='networkidle')
        expect(page.locator('#language')).to_be_enabled()
        return page

    def test_initial_language_precedence(self):
        """Every rule sets selector, html lang and the localized title together."""
        for name, host, languages, stored, expected in CASES:
            with self.subTest(name), self.browser.new_context() as context:
                page = self.open_page_as(context, host, languages, stored)
                title = json.loads((core.ROOT / f'content/{expected}.json').read_text())['ui']['title']
                expect(page.locator('html')).to_have_attribute('lang', expected)
                expect(page.locator('#language')).to_have_value(expected)
                self.assertEqual(page.title(), title)

    def test_selector_choice_is_remembered_and_nothing_else_is_stored(self):
        """A manual choice beats the host on reload; only the language key is written."""
        with self.browser.new_context() as context:
            page = self.open_page_as(context, RU_HOST, ['en-US'], None)
            expect(page.locator('html')).to_have_attribute('lang', 'ru')
            page.locator('#language').select_option('en')
            page.reload(wait_until='networkidle')
            expect(page.locator('#language')).to_be_enabled()
            expect(page.locator('html')).to_have_attribute('lang', 'en')
            self.assertEqual(page.evaluate('Object.entries(localStorage)'), [['language', 'en']])


if __name__ == '__main__':
    unittest.main(argv=[core.sys.argv[0]], verbosity=2)
