"""Test environment.

Set before any app module is imported, because settings and the engine are built
at import time.
"""

import os
import tempfile

os.environ.setdefault("CLASSIFIER_BACKEND", "mock")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(
    tempfile.mkdtemp(prefix="masroufi-test-"), "test.db"
)
