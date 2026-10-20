"""Check populated model statistics through the public agent HTTP/render path.

python test_model_usage.py --base-url http://127.0.0.1:3000 \
    --executable /path/to/chromium --screenshots /path/to/evidence
The model/task shape follows AgentService.get_model_usage and the adjacent
agent page unit fixture; all other HTTP fixtures and CLI come from detail width.
"""

import json
import unittest
from urllib.parse import urlparse

import test_detail_width as detail
from playwright.sync_api import expect

MODEL_COUNTS = (("synthetic/model-alpha", "codegen", 3), ("synthetic/model-beta", "review", 1))
MODEL_USAGE = {
    "agent_id": detail.AGENT_ID,
    "total_calls": 4,
    "unique_models": 2,
    "by_model": [{"model": model, "total_calls": count} for model, _, count in MODEL_COUNTS],
    "by_task": [
        {
            "model": model,
            "task_type": task,
            "call_count": count,
            "last_used": "2026-10-19T00:00:00+00:00",
        }
        for model, task, count in MODEL_COUNTS
    ],
}
ROW_METRICS = """el => {
    const bar = el.lastElementChild, fill = bar.firstElementChild;
    return {text: el.innerText, inlineWidth: fill.style.width,
        percent: fill.getBoundingClientRect().width / bar.getBoundingClientRect().width * 100};
}"""


class ModelUsageTests(unittest.TestCase):
    """Exercise six anonymous populated layouts without discovering WIDTH cases."""

    @classmethod
    def setUpClass(cls):
        detail.DetailWidthTests.setUpClass.__func__(cls)

    def route_request(self, route):
        """Override only model usage; reuse WIDTH's fail-closed HTTP boundary."""
        target = urlparse(route.request.url)
        if (
            target.netloc == urlparse(self.options.base_url).netloc
            and target.path == f"/api/v1/agents/{detail.AGENT_ID}/model-usage"
            and route.request.method == "GET"
        ):
            self.requested.append(target.path)
            route.fulfill(json=MODEL_USAGE)
        else:
            detail.DetailWidthTests.route_request(self, route)

    def capture_usage(self, page, tag):
        """Save real text, progress geometry and a screenshot before assertions."""
        models = page.get_by_text("models://usage", exact=True).locator("../..")
        tasks = page.get_by_text("tasks://breakdown", exact=True).locator("../..")
        records = {"models": [], "tasks": [], "requests": self.requested}
        for model, _, _ in MODEL_COUNTS:
            row = models.locator(f'[title="{model}"]').locator("../..")
            records["models"].append({"model": model, **row.evaluate(ROW_METRICS)})
            task = tasks.locator(f'[title="{model}"]').locator("..")
            records["tasks"].append({"model": model, "text": task.inner_text()})
        records.update(
            page.evaluate("""() => ({
            locale: document.documentElement.lang, viewport: innerWidth,
            documentWidth: document.documentElement.scrollWidth,
            localizedNaN: new Intl.NumberFormat(document.documentElement.lang).format(NaN)
        })""")
        )
        usage = models.locator("../..")
        records["text"] = usage.inner_text()
        usage.screenshot(path=str(self.options.screenshots / f"{tag}.png"))
        (self.options.screenshots / f"{tag}.json").write_text(
            json.dumps(records, ensure_ascii=False, indent=2)
        )
        return models, tasks, records

    def assert_usage(self, models, tasks, records):
        """Require backend model totals, independent task counts and finite geometry."""
        for index, (model, task, count) in enumerate(MODEL_COUNTS):
            percent = (75, 25)[index]
            row = models.locator(f'[title="{model}"]').locator("../..")
            expect(row.get_by_text(f"{count} · {percent}%", exact=True)).to_be_visible()
            task_row = tasks.locator(f'[title="{model}"]').locator("..")
            expect(task_row.get_by_text(task, exact=True)).to_be_visible()
            expect(task_row.get_by_text(f"{count}×", exact=True)).to_be_visible()
            self.assertEqual(records["models"][index]["inlineWidth"], f"{percent}%")
            self.assertAlmostEqual(records["models"][index]["percent"], percent, delta=1)
        self.assertNotIn(records["localizedNaN"], records["text"])
        self.assertLessEqual(records["documentWidth"], records["viewport"])
        self.assertIn(f"/api/v1/agents/{detail.AGENT_ID}/model-usage", self.requested)
        self.assertEqual(self.unexpected, [])

    def test_backend_model_totals_and_task_counts_render_in_six_layouts(self):
        for locale in ("en", "ru"):
            for width in (320, 390, 1280):
                with self.subTest(locale=locale, width=width):
                    page, _ = detail.DetailWidthTests.open_page(self, "agent", locale, width)
                    try:
                        title = "Model Usage" if locale == "en" else "Использование моделей"
                        expect(page.get_by_text(title, exact=True)).to_be_visible()
                        models, tasks, records = self.capture_usage(page, f"model-{locale}-{width}")
                        self.assert_usage(models, tasks, records)
                    finally:
                        page.context.close()


if __name__ == "__main__":
    ModelUsageTests.options, unittest_args = detail.browser_options.parse_options()
    unittest.main(argv=[__file__, *unittest_args], verbosity=2)
