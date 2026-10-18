"""Native regression for controls used before initial content is ready."""

import sys
import unittest

from playwright.sync_api import expect
import test_training as core


class StartupTest(unittest.TestCase):
    """Keep startup loading real while delaying one HTTP response."""

    @classmethod
    def setUpClass(cls):
        cls.addClassCleanup(core.TrainingTest.doClassCleanups)
        core.TrainingTest.setUpClass()
        cls.browser, cls.base = core.TrainingTest.browser, core.TrainingTest.base

    def test_initial_content_pending_disables_controls_then_russian_attempt(self):
        """Controls cannot accept a locale before startup listeners are ready."""
        with self.browser.new_context() as context:
            pending, events = [], []
            curriculum = (core.ROOT / 'content/curriculum.json').read_bytes()

            def receive(route):
                request = route.request
                if not request.url.startswith(self.base):
                    route.abort('blockedbyclient')
                elif request.url == self.base + 'content/curriculum.json':
                    pending.append(route)
                elif request.url == self.base + '_agentspore/events':
                    events.append(request.post_data_json)
                    route.fulfill(status=204)
                else:
                    route.continue_()

            context.route('**/*', receive)
            page = context.new_page()
            page.set_default_timeout(1500)
            try:
                with page.expect_request('**/content/curriculum.json'):
                    page.goto(self.base, wait_until='domcontentloaded')
                    expect(page.locator('#language option')).to_have_count(31)
                expect(page.locator('#language')).to_be_visible()
                self.assertEqual(len(pending), 1, 'Initial curriculum request must remain held')
                expect(page.locator('#language')).to_be_disabled()
                expect(page.locator('#start-training')).to_be_disabled()
            finally:
                for route in pending:
                    route.fulfill(status=200, content_type='application/json', body=curriculum)
            expect(page.locator('#language')).to_be_enabled()
            expect(page.locator('#start-training')).to_be_enabled()
            page.locator('#language').select_option('ru')
            expect(page.locator('html')).to_have_attribute('lang', 'ru')
            with page.expect_request('**/_agentspore/events') as sent:
                page.locator('#start-training').click()
            sent.value.response()
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]['lang'], 'ru')
            self.assertEqual(events[0]['answers'], [])
            self.assertIs(events[0]['completed'], False)
            expect(page.locator('#training')).to_have_attribute('data-round-id', '1')


if __name__ == '__main__':
    unittest.main(argv=[sys.argv[0]], verbosity=2)
