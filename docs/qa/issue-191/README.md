2026-10-25 capture; 2026-10-26 focused verification — Diagnostic QA, `feature/191`, build `j1J8eiaV8aFkCp-gN85xD`.

# Connection guide handoff

Four anonymous local cases exercised the real copy button and keyboard workspace link in English and Russian at 390 and 1440 CSS px. The guide asks for `owner_email`; the destination displays its anonymous sign-in state. The strict network gate remains **RED**. These artifacts do not approve a release.

| Locale and width | Guide | Focused workspace action | Anonymous workspace |
| --- | --- | --- | --- |
| EN 390 | [PNG](en-390-guide.png) | [PNG](en-390-action.png) | [PNG](en-390-workspace.png) |
| RU 390 | [PNG](ru-390-guide.png) | [PNG](ru-390-action.png) | [PNG](ru-390-workspace.png) |
| EN 1440 | [PNG](en-1440-guide.png) | [PNG](en-1440-action.png) | [PNG](en-1440-workspace.png) |
| RU 1440 | [PNG](ru-1440-guide.png) | [PNG](ru-1440-action.png) | [PNG](ru-1440-workspace.png) |

[Mobile video](connect-workspace-390.mp4), 9.36 s; [desktop video](connect-workspace-1440.mp4), 10.72 s. Both show actual headless interactions, English followed by Russian, with H.264 encoding. One brief white initial-document frame is retained at the fresh-context transition. These short clips are QA evidence, not a paced product demo.

Visual observations: readable translated prompt and confirmation, visible final action, retained Agents/Glossary links, and no horizontal overflow in the four cases. The copied public prompt matched the rendered text through a page-local clipboard stub; the OS clipboard was neither read nor written. Keyboard Enter opened the real anonymous workspace route.

The corrected capture retained 59 `net::ERR_ABORTED` events, each following a 200 `text/x-component` response. All 59 had the RSC flag; 56 had the prefetch flag and three did not. It recorded zero page errors, console errors, HTTP errors or blocked requests. The cancellation trigger is unknown; no exemption was applied. A failed lower verification rung means these visual observations cannot establish higher-rung release acceptance.

On 2026-10-26, the same final harness produced eight baseline UX failures in 26.120 s; the feature passed two methods in 8.374 s, including eight UX subtests, 26 negative predicate checks and positive checks for both prefetch purposes. Five native controls were verified in each case. All 56 feature cancellations uniquely matched positive-byte EOF observations with zero read errors, pending reads, native cancellation or release; the focused gate passed while the raw strict gate stayed RED. An in-memory guard mutant produced nine expected failures without changing source. Fetch/read wrappers change Promise identity and microtask timing; EOF proves neither semantic Flight validity nor the cancellation cause. Incomplete native cancellation/release evidence fails closed. These results do not retroactively classify the 59 original visual-capture cancellations.

Six contact sheets and 19 sampled video frames were inspected; both videos decoded completely without errors. This is sampled visual review, not inspection of every encoded frame. Asset hashes, dimensions, capture-time source hashes and exact scope: [receipt](receipt.json).

Not verified: automated accessibility, full Tab order, other state combinations, 320/768 widths, 200% zoom, ARIA snapshots, pinned visual baselines, performance budgets, real registration/login/ownership linking, authenticated data, backend or payments. Node 20 and standalone packaging were not verified.

The first capture is preserved locally under `.specs/evidence/connect-workspace-191/visual/first-run/`. Its six additional failures came from the capture policy omitting known public privacy/login reads; full-page screenshots also introduced recording resize flashes. The corrected capture permits those exact reads and uses viewport screenshots. No application, test, dependency or configuration was changed for this correction.
