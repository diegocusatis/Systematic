"""Validate copied reviews and all current Excel exports before activation."""
from pathlib import Path
import sys

from review_extractor.app import create_app
from review_extractor.workbooks import export_download_workbook


def validate(stage, final):
    app = create_app(Path(stage), run_background=False)
    service = app.state.service
    try:
        # Export is read-only and exercises definitions, evidence, locations and styles.
        for review in service.list_reviews():
            for paper in service.list_papers(review["id"], include_removed=True):
                service._validate_paper(paper)
            snapshot = service.export_snapshot(review["id"])
            for kind in ("data", "verification", "combined"):
                result = export_download_workbook(snapshot, kind)
                if not result:
                    raise ValueError("An Excel export could not be generated.")
    finally:
        app.state.scans.close()
        service.store.close()


if __name__ == "__main__":
    validate(*sys.argv[1:3])
    print("Saved reviews and workbook exports passed validation.", flush=True)
