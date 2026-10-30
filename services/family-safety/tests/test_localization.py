"""Native browser acceptance for all supported locales and language lifecycle."""

from collections import Counter
import json
import re
import sys
import unittest
from urllib.parse import unquote
from uuid import UUID

from playwright.sync_api import expect
import test_training as core

LOCALES = 'en ru es pt fr de it nl pl uk cs sk ro hu el tr ar he fa hi bn ur id ms vi th ko ja zh-Hans sw tl'.split()
RTL = {'ar', 'he', 'fa', 'ur'}
UI = '''brandNote language eyebrow title intro stepOne phraseTitle phraseIntro phraseLabel
phrasePrivacy phraseTooLong phraseUnsupported includePhrase printCard cardEyebrow cardTitle
cardPause cardCall cardAsk cardPhraseLabel phraseLimit stepTwo practiceTitle practiceIntro
startTraining pauseVerify ordinaryRequest nextRound finishTraining restartTraining roundStatus
correct incorrect resultScore resultAdvice stepThree shareTitle sharePrivacy shareWhatsApp
shareTelegram shareText metricsPrivacy fictionNote loadError'''.split()
OUT = core.Path(sys.argv[1])


class LocalizationTest(unittest.TestCase):
    """Reuse server ownership without discovering or inheriting core tests."""

    @classmethod
    def setUpClass(cls):
        cls.addClassCleanup(core.TrainingTest.doClassCleanups)
        core.TrainingTest.setUpClass()
        cls.browser, cls.base = core.TrainingTest.browser, core.TrainingTest.base
        OUT.mkdir(parents=True, exist_ok=True)

    open_page = core.TrainingTest.open_page

    def test_catalog_contract(self):
        """All locale catalogs retain the frozen 64-string English shape."""
        directory = core.ROOT / 'content'
        self.assertEqual({p.stem for p in directory.glob('*.json')} - {'curriculum'}, set(LOCALES))
        english = json.loads((directory / 'en.json').read_text())
        def leaves(value, path=''):
            if isinstance(value, dict):
                return {key: text for name, item in value.items()
                        for key, text in leaves(item, path + '/' + name).items()}
            self.assertIsInstance(value, str)
            self.assertTrue(value.strip())
            self.assertNotRegex(value, r'<[^>]+>')
            return {path: value}
        baseline = leaves(english)
        self.assertEqual(len(set(baseline) - {'/locale', '/name'}), 64)
        for lang in LOCALES:
            with self.subTest(lang=lang):
                data = json.loads((directory / f'{lang}.json').read_text())
                self.assertEqual(set(data), {'locale', 'name', 'ui', 'rounds'})
                self.assertEqual(data['locale'], lang)
                self.assertEqual(set(data['ui']), set(UI))
                self.assertEqual(set(data['rounds']), {str(n) for n in range(1, 11)})
                for row in data['rounds'].values():
                    self.assertEqual(set(row), {'message', 'explanation'})
                strings = leaves(data)
                self.assertEqual(set(strings), set(baseline))
                for key, value in strings.items():
                    self.assertEqual(Counter(re.findall(r'\{[^{}]+\}', value)),
                                     Counter(re.findall(r'\{[^{}]+\}', baseline[key])))

    def test_all_locales_training_and_visual_capture(self):
        """Each native locale completes one attempt with eleven private snapshots."""
        for lang in LOCALES:
            with self.subTest(lang=lang), self.browser.new_context() as context:
                page, events, requests = self.open_page(context)
                page.locator('#language').select_option(lang)
                expect(page.locator('html')).to_have_attribute('lang', lang)
                self.assertEqual(page.locator('html').evaluate('(e) => getComputedStyle(e).direction'),
                                 'rtl' if lang in RTL else 'ltr')
                page.locator('#family-phrase').fill(core.PHRASE)
                if lang in {'en', 'ru', 'es', 'ar'}:
                    for width in (390, 1280):
                        page.set_viewport_size({'width': width, 'height': 900})
                        self.assertTrue(page.evaluate('document.documentElement.scrollWidth <= innerWidth'))
                with page.expect_request('**/_agentspore/events') as sent:
                    page.locator('#start-training').click()
                sent.value.response()
                for index in range(1, 11):
                    expect(page.locator('#training')).to_have_attribute('data-round-id', str(index))
                    with page.expect_request('**/_agentspore/events') as sent:
                        page.locator('#pause-verify').click()
                    sent.value.response()
                    expect(page.locator('#round-feedback')).to_be_visible()
                    if index == 1 and lang in {'en', 'ru', 'es', 'ar'}:
                        for width in (390, 1280):
                            page.set_viewport_size({'width': width, 'height': 900})
                            self.assertTrue(page.evaluate('document.documentElement.scrollWidth <= innerWidth'))
                            page.screenshot(path=str(OUT / f'{lang}-{width}-feedback.png'), full_page=True)
                    page.locator('#next-round').click()
                expect(page.locator('#training-result')).to_contain_text('8/10')
                self.assertEqual(len(events), 11)
                attempt = events[0]['attempt_id']
                self.assertEqual(UUID(attempt).version, 4)
                for count, event in enumerate(events):
                    self.assertEqual(event, dict(event='training_progress', attempt_id=attempt,
                        curriculum_version=1, lang=lang, answers=core.CORRECT[:count], completed=count == 10))
                    self.assertEqual(set(event), core.FIELDS)
                self.assertNotIn(core.PHRASE, unquote(json.dumps(requests)))
                for request in requests:
                    self.assertTrue(json.loads(request)[0].startswith(self.base))
                self.assertEqual(context.cookies(), [])
                self.assertEqual(page.evaluate('Object.keys(localStorage).filter(k => k !== "language").length + sessionStorage.length'), 0)
                self.assertIn(page.evaluate('localStorage.getItem("language")'), (None, lang))

    def test_url_language_switch_and_offline_lifecycle(self):
        """URL locale starts measurement; cached switches preserve private state."""
        with self.browser.new_context() as context:
            page, events, _ = self.open_page(context)
            page.goto(self.base + '?lang=ru', wait_until='networkidle')
            expect(page.locator('html')).to_have_attribute('lang', 'ru')
            page.locator('#family-phrase').fill(core.PHRASE)
            with page.expect_request('**/_agentspore/events') as sent:
                page.locator('#start-training').click()
            sent.value.response()
            page.locator('#pause-verify').click()
            page.locator('#next-round').click()
            page.locator('#language').select_option('en')
            expect(page.locator('html')).to_have_attribute('lang', 'en')
            expect(page.locator('#family-phrase')).to_have_value(core.PHRASE)
            expect(page.locator('#training')).to_have_attribute('data-round-id', '2')
            context.set_offline(True)
            try:
                page.locator('#language').select_option('ru')
                expect(page.locator('html')).to_have_attribute('lang', 'ru')
                page.locator('#language').select_option('en')
                expect(page.locator('html')).to_have_attribute('lang', 'en')
                before = page.locator('#training').inner_text()
                page.locator('#language').select_option('hi')
                error = json.loads((core.ROOT / 'content/en.json').read_text())['ui']['loadError']
                expect(page.get_by_text(error, exact=True)).to_be_visible()
                expect(page.locator('html')).to_have_attribute('lang', 'en')
                expect(page.locator('#language')).to_have_value('en')
                expect(page.locator('#family-phrase')).to_have_value(core.PHRASE)
                self.assertEqual(page.locator('#training').inner_text(), before)
                expect(page.locator('#training')).to_have_attribute('data-round-id', '2')
            finally:
                context.set_offline(False)
            with page.expect_request('**/_agentspore/events') as sent:
                page.locator('#pause-verify').click()
            sent.value.response()
            self.assertEqual(len(events), 3)
            self.assertTrue(all(row['lang'] == 'ru' for row in events))
            self.assertEqual(events[-1]['answers'], [True, True])
            self.assertEqual(len({row['attempt_id'] for row in events}), 1)


if __name__ == '__main__':
    unittest.main(argv=[sys.argv[0]], verbosity=2)
