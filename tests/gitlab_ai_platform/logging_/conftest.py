from __future__ import annotations

import logging

import pytest


@pytest.fixture(autouse=True)
def _reset_root_logger() -> None:
    yield
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
        handler.close()
    root.setLevel(logging.WARNING)
