"""app/__init__.py: the Python version guard."""
import importlib
import sys

import pytest

import app


def test_old_python_fails_with_clear_message(monkeypatch):
    """On Python < 3.11, importing the app raises a readable error instead of a syntax error later."""
    monkeypatch.setattr(sys, "version_info", (3, 9, 6))
    with pytest.raises(RuntimeError, match="needs Python 3.11"):
        importlib.reload(app)


def test_current_python_imports_fine():
    """The guard passes on the Python running the tests."""
    importlib.reload(app)
