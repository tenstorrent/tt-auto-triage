"""Test setup for the Slack error grouping scripts.

The scripts import each other by bare module name, so the action directory has
to be on the path. The heavy similarity and HTTP dependencies are stubbed when
they are not installed, so the state and matching logic can be tested without
pulling in a sentence-transformer model.
"""

import atexit
import os
import shutil
import sys
import tempfile
import types
from pathlib import Path

ACTION_DIR = Path(__file__).resolve().parent.parent
if str(ACTION_DIR) not in sys.path:
    sys.path.insert(0, str(ACTION_DIR))

# Somewhere outside the checkout, and thrown away afterwards. state_paths reads
# this at import time, so it has to be set before any module under test is
# imported. Writing inside the checkout meant test runs left cache files in the
# working tree, where they get committed by accident, and a cache left behind by
# one run changed what the next one saw.
if "GROUPING_STATE_DIR" not in os.environ:
    _TEST_STATE_DIR = tempfile.mkdtemp(prefix="grouping-state-tests-")
    atexit.register(shutil.rmtree, _TEST_STATE_DIR, ignore_errors=True)
    os.environ["GROUPING_STATE_DIR"] = _TEST_STATE_DIR


def _install_stub(name: str, **attributes) -> None:
    module = types.ModuleType(name)
    for key, value in attributes.items():
        setattr(module, key, value)
    sys.modules[name] = module


class _StubRequestException(Exception):
    """Stands in for requests.RequestException when requests is not installed."""


try:
    import requests  # noqa: F401
except ImportError:
    # The stub has to carry RequestException, because the code under test catches
    # it. Leaving it out turns a missing dependency into an AttributeError raised
    # from inside the module being tested, which reads like a real failure.
    _install_stub("requests", get=None, post=None, RequestException=_StubRequestException)

try:
    import error_similarity  # noqa: F401
except ImportError:
    _install_stub(
        "error_similarity",
        SEMANTIC_THRESHOLD=85.0,
        RAPIDFUZZ_THRESHOLD=70.0,
        find_best_matching_centroid=lambda *args, **kwargs: (None, {"rapidfuzz": 0.0, "semantic": 0.0}),
    )
