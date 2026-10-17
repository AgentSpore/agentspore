"""Discover acceptance once, preserving output-directory and browser arguments."""

from pathlib import Path
import sys
import unittest


def main():
    """Use ordinary discovery; base fixtures have no inherited test methods."""
    directory = Path(__file__).resolve().parent
    suite = unittest.defaultTestLoader.discover(str(directory), pattern="test_*.py")
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
