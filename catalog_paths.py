"""Locate the catalog selected for new builder processes."""

from pathlib import Path
import re

CATALOG_DATA = Path(__file__).parent / "data" / "smogon"
ACTIVE_CATALOG = "active_catalog.txt"


def catalog_dir(root, snapshot):
    if snapshot == "original":
        return root
    if not re.fullmatch(r"\d{4}-\d{2}-[0-9a-f]{8}", snapshot):
        raise ValueError(f"Invalid catalog snapshot ID: {snapshot!r}")
    return root / "staged" / snapshot


def active_catalog_dir(root=CATALOG_DATA):
    try:
        snapshot = (root / ACTIVE_CATALOG).read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return root
    return catalog_dir(root, snapshot)
