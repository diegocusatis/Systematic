"""Rebuild the structural demo workbook. No example study data are included.

Run from the repository root: python demo/create_template.py
The synthetic dictionary metadata and this script are CC0-1.0.
"""

import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from openpyxl import Workbook
from review_extractor.workbooks import _table


def main():
    root = Path(__file__).resolve().parents[1]
    items = json.loads((root / "review_extractor/resources/reference_items.json").read_text(encoding="utf-8"))
    book = Workbook()
    book.remove(book.active)
    _table(book, "Sheet1", [item["export_heading"] for item in items], [])
    _table(book, "Synthetic dictionary", ["Name", "Export heading", "Definition", "Data type", "Required", "Keywords", "Units", "Group", "Time point"], [
        ["Adverse events", "Adverse events at week 3", "Record the reported event count for the selected group and time point.", "integer", "yes", "adverse events;side effects", "events", "Treatment", "Week 3"],
        ["Participant counts", "Participant-count narrative", "Preserve separately labeled group counts and any source discrepancy.", "narrative", "yes", "completed;flow diagram", "participants", "", ""],
        ["Follow-up", "Follow-up timing", "Record the source wording for follow-up timing.", "text", "no", "follow-up;follow up", "", "", ""],
    ])
    book.properties.title = "Extraction template: headings and synthetic data dictionary"
    book.properties.creator = "Review Extractor demo"
    book.properties.description = "Structure only. No study records or extraction evidence are included."
    book.save(root / "demo/extraction-template.xlsx")


if __name__ == "__main__":
    main()
