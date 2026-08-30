"""Every top-level module must be in the Dockerfile's COPY list.

The list is spelled out file by file, so a new module is simply absent from
the image unless someone remembers to add it. Measured 2026-08-30:
`proxy_chain.py` shipped in `routes/agents.py`'s import but not in the COPY
line, and the rebuilt runner crash-looped on `ModuleNotFoundError` — the
build itself exited 0.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_every_top_level_module_is_copied_into_the_image():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    modules = {p.name for p in ROOT.glob("*.py")}

    missing = sorted(name for name in modules if name not in dockerfile)

    assert not missing, (
        f"modules present in the source tree but absent from the Dockerfile "
        f"COPY list, so they will be missing from the image: {missing}"
    )
