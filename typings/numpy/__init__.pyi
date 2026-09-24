"""Minimal NumPy surface used by SagaMind's optional vectorized paths.

This keeps Python 3.10-targeted mypy runs independent of newer NumPy stubs that
use Python 3.12-only syntax. Runtime imports still resolve to real NumPy.
"""

from typing import Any

array: Any
asarray: Any
errstate: Any
exp: Any
linalg: Any
log1p: Any
maximum: Any
where: Any
