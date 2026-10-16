"""Real PDF regression for consent, printable contents and white paper."""

import sys
import unittest

import fitz
from playwright.sync_api import expect
import test_training as core

OUT = core.Path(sys.argv[1])


class PrintTest(unittest.TestCase):
    """Reuse native browser fixture without inheriting the training tests."""

    @classmethod
    def setUpClass(cls):
        cls.addClassCleanup(core.TrainingTest.doClassCleanups)
        core.TrainingTest.setUpClass()
        cls.browser, cls.base = core.TrainingTest.browser, core.TrainingTest.base
        OUT.mkdir(parents=True, exist_ok=True)

    open_page = core.TrainingTest.open_page

    def test_real_pdf_consent_white_paper_and_no_training(self):
        """Default, consented and revoked PDFs contain only the one-page card."""
        with self.browser.new_context() as context:
            page, _, _ = self.open_page(context)
            expect(page.locator('#include-phrase')).not_to_be_checked()
            page.locator('#family-phrase').fill(core.PHRASE)
            page.locator('#start-training').click()
            page.locator('#pause-verify').click()
            training_text = page.locator('#round-feedback').inner_text().strip()
            for index, consent in enumerate((False, True, False)):
                with self.subTest(consent=consent, index=index):
                    page.emulate_media(media='screen')
                    page.locator('#include-phrase').set_checked(consent)
                    card = page.locator('#print-card')
                    card.click()
                    page.emulate_media(media='print')
                    for selector in ('#family-phrase', '#include-phrase', '#print-card', '#training'):
                        expect(page.locator(selector)).to_be_hidden()
                    pdf = page.pdf(path=str(OUT / f'card-consent-{index}.pdf'),
                                   format='A4', print_background=True)
                    with fitz.open(stream=pdf, filetype='pdf') as document:
                        self.assertEqual(len(document), 1)
                        paper = document[0]
                        text = paper.get_text()
                        self.assertEqual(core.PHRASE in text, consent)
                        self.assertIn('Urgent request?', text)
                        self.assertNotIn('Start the ten-message practice', text)
                        self.assertNotIn('Pause and verify', text)
                        if training_text:
                            self.assertNotIn(training_text, text)
                        area = fitz.Rect(40, paper.rect.height - 100,
                                         paper.rect.width - 40, paper.rect.height - 40)
                        pixels = paper.get_pixmap(clip=area, colorspace=fitz.csRGB, alpha=False)
                        self.assertGreaterEqual(min(pixels.samples), 254,
                                                'Unused lower PDF paper must be white')


if __name__ == '__main__':
    unittest.main(argv=[sys.argv[0]], verbosity=2)
