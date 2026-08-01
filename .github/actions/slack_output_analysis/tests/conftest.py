"""Test setup for the Slack error grouping scripts.

The scripts import each other by bare module name, so the action directory has
to be on the path. The heavy similarity and HTTP dependencies are stubbed when
they are not installed, so the state and matching logic can be tested without
pulling in a sentence-transformer model.
"""

import os
import sys
import types
from pathlib import Path

ACTION_DIR = Path(__file__).resolve().parent.parent
if str(ACTION_DIR) not in sys.path:
    sys.path.insert(0, str(ACTION_DIR))

# Never let a test write into a real state directory.
os.environ.setdefault("GROUPING_STATE_DIR", str(Path(__file__).resolve().parent / "_state"))


def _install_stub(name: str, **attributes) -> None:
    module = types.ModuleType(name)
    for key, value in attributes.items():
        setattr(module, key, value)
    sys.modules[name] = module


try:
    import requests  # noqa: F401
except ImportError:
    _install_stub("requests", get=None, post=None)

try:
    import error_similarity  # noqa: F401
except ImportError:
    _install_stub(
        "error_similarity",
        SEMANTIC_THRESHOLD=85.0,
        RAPIDFUZZ_THRESHOLD=70.0,
        find_best_matching_centroid=lambda *args, **kwargs: (None, {"rapidfuzz": 0.0, "semantic": 0.0}),
    )
