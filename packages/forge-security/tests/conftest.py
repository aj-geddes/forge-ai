"""Pytest configuration for forge-security.

Ensures this package's ``tests/`` directory is on ``sys.path`` so sibling
test modules can share the ``_oidc_fixtures`` helper via a plain import,
independent of any cross-package ``tests`` package-name collisions under
the monorepo's ``--import-mode=importlib`` pytest configuration.
"""

from __future__ import annotations

import sys
from pathlib import Path

_TESTS_DIR = str(Path(__file__).parent)
if _TESTS_DIR not in sys.path:
    sys.path.insert(0, _TESTS_DIR)
