"""S-3: every collector and log source that can bring data in is listed in docs/PHI-PII-COLLECTION-POINTS.md."""
from pathlib import Path

import pytest

DOC = Path(__file__).resolve().parents[2] / "docs" / "PHI-PII-COLLECTION-POINTS.md"

# Collectors that bring in no data of their own, so they need no row of their own.
# hub_import is covered by the "local import" row.
COVERED_BY_OTHER_ROWS = {"hub_import": "local import"}


def _doc() -> str:
    return DOC.read_text()


def test_the_document_exists_and_names_the_required_points():
    text = _doc().lower()
    for point in ("logs", "pcap", "uploads", "rti", "grafana", "kubernetes events"):
        assert point in text, f"{point} is missing from the collection-point list"


def test_every_registered_tool_plugin_is_listed():
    pytest.importorskip("kubernetes")
    from app.collectors.registry import PLUGINS
    text = _doc()
    for entry in PLUGINS:
        module = entry.split(":")[0].rsplit(".", 1)[-1]          # e.g. "tshark"
        assert module in text or module.replace("_", " ") in text.lower(), f"{module} has no row in {DOC.name}"


def test_every_registered_log_source_is_listed():
    pytest.importorskip("sqlalchemy")
    from app.sources import registry
    text = _doc()
    for source in registry.all_sources():
        module = {"axone": "axone_export", "local": "local_import"}.get(source.id, source.id)
        assert module in text, f"log source {source.id} has no row in {DOC.name}"


def test_each_row_says_what_we_do_and_what_remains():
    rows = [line for line in _doc().splitlines() if line.startswith("| ") and line[2].isdigit()]
    assert len(rows) >= 12
    for row in rows:
        cells = [c.strip() for c in row.strip("|").split("|")]
        assert len(cells) == 6 and all(cells), f"incomplete row: {row[:60]}"


def test_the_readme_points_to_the_document_instead_of_repeating_it():
    readme = (DOC.parents[1] / "backend" / "README.md").read_text()
    assert "PHI-PII-COLLECTION-POINTS.md" in readme
    assert "PHI/PII collection point:" not in readme
