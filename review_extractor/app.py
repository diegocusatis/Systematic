"""Local FastAPI transport. Business decisions live in service.py."""

from contextlib import asynccontextmanager
import hashlib
from importlib.resources import files
import json
import os
from pathlib import Path
from urllib.parse import urlsplit
import uuid

from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .scans import ScanQueue
from .keywords import suggest_keywords
from .service import ReviewService
from .workbooks import export_preview, export_workbook, export_download_workbook, preview_template, validate_snapshot

MAX_PDF_BYTES = 100 * 1024 * 1024
MAX_TEMPLATE_BYTES = 15 * 1024 * 1024
CHECKOUT_ROOT = Path(__file__).resolve().parent.parent


def default_data_dir() -> Path:
    explicit = os.environ.get("REVIEW_EXTRACTOR_DATA_DIR")
    if explicit:
        return Path(explicit).expanduser().resolve()
    # Existing development reviews predate the installed-app default. Resolve
    # from the module, not the shell's working directory, so a normal restart
    # does not silently open an empty database elsewhere. Never move the data.
    checkout_data = CHECKOUT_ROOT / ".review-data"
    if (CHECKOUT_ROOT / "pyproject.toml").is_file() and (checkout_data / "reviews.sqlite3").is_file():
        return checkout_data
    base = Path(os.environ.get("LOCALAPPDATA", Path.home() / ".local" / "share"))
    return base / "ReviewExtractor"


def create_app(data_dir: Path | str | None = None, *, run_background: bool = True) -> FastAPI:
    service = ReviewService(Path(data_dir) if data_dir else default_data_dir())
    scans = ScanQueue(service, background=run_background)

    @asynccontextmanager
    async def lifespan(_app):
        scans.recover()
        yield
        scans.close()
        service.store.close()

    app = FastAPI(title="Review Extractor", version="0.1.0", lifespan=lifespan)
    app.state.service, app.state.scans = service, scans
    app.add_middleware(TrustedHostMiddleware,
                       allowed_hosts=["127.0.0.1", "localhost", "[::1]", "testserver"])

    @app.middleware("http")
    async def local_origin(request: Request, call_next):
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("origin")
            if origin and urlsplit(origin).netloc != request.headers.get("host"):
                return JSONResponse({"detail": "Writes must originate from this local application."}, status_code=403)
            if request.headers.get("sec-fetch-site") == "cross-site":
                return JSONResponse({"detail": "Cross-site writes are not allowed."}, status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self' 'wasm-unsafe-eval'; "
            "style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
            "font-src 'self' data: blob:; connect-src 'self'; worker-src 'self' blob:; "
            "object-src 'none'; base-uri 'self'; frame-ancestors 'none'"
        )
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        elif request.url.path in {"/", "/static/index.html", "/static/app.js", "/static/viewer.js", "/static/style.css"}:
            # The local app is updated in place. Revalidate UI files on reload
            # rather than leaving an old interface attached to a newer API.
            response.headers["Cache-Control"] = "no-cache, must-revalidate"
        return response

    @app.exception_handler(ValueError)
    async def invalid_request(_request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=422)

    @app.exception_handler(KeyError)
    async def missing_record(_request, _exc):
        return JSONResponse({"detail": "This record does not exist in the selected review."}, status_code=404)

    @app.get("/api/health")
    def health():
        return {"status": "ok", "version": "0.1.0", "offline": True}

    @app.get("/api/reference-template")
    def reference():
        items = json.loads(files("review_extractor").joinpath("resources/reference_items.json").read_text(encoding="utf-8"))
        for item in items:
            item["keywords"] = suggest_keywords(item)
        return {"items": items, "source": "Systematic Example.xlsx / Sheet1 / row 1", "example_included": False}

    @app.post("/api/keyword-suggestions")
    def keyword_suggestions(data: dict):
        return {"keywords": suggest_keywords(data)}

    @app.get("/api/reviews")
    def reviews():
        return service.list_reviews()

    @app.post("/api/reviews", status_code=201)
    def create_review(data: dict):
        return service.create_review(data)

    @app.get("/api/reviews/{r}")
    def review(r: str):
        return service.get_review(r)

    @app.patch("/api/reviews/{r}")
    def update_review(r: str, data: dict):
        return service.update_review(r, data)

    @app.post("/api/templates/preview")
    async def template_preview(file: UploadFile = File(...), sheet: str | None = Form(None),
                               header_rows: str = Form("1"), mode: str = Form("columns"),
                               mapping: str | None = Form(None), all_sheets: bool = Form(False)):
        if not (file.filename or "").lower().endswith(".xlsx"):
            raise ValueError("Choose an .xlsx workbook.")
        content = await file.read(MAX_TEMPLATE_BYTES + 1)
        if len(content) > MAX_TEMPLATE_BYTES:
            raise ValueError("Template exceeds the 15 MB limit.")
        try:
            return preview_template(content, sheet=sheet or None, header_rows=header_rows,
                                    mode=mode, mapping=json.loads(mapping) if mapping else None, all_sheets=all_sheets)
        except (KeyError, ValueError):
            raise
        except Exception as exc:
            raise ValueError(f"The workbook could not be read: {exc}") from exc

    @app.get("/api/reviews/{r}/items")
    def items(r: str, include_deleted: bool = False):
        return service.list_items(r, include_deleted=include_deleted)

    @app.post("/api/reviews/{r}/items/import")
    def import_items(r: str, data: dict):
        incoming = data.get("items")
        if not isinstance(incoming, list) or not incoming:
            raise ValueError("Select at least one extraction item to import.")
        # Validate all fields before mutating to avoid a partially imported design.
        from .service import validate_item_data
        for item in incoming:
            validate_item_data(item)
        # Preview positions describe that template; appending must not collide
        # with columns already present in this review.
        with service.store.transaction():
            saved = [service.add_item(r, {key: value for key, value in item.items() if key != "position"}) for item in incoming]
        return {"items": saved}

    @app.post("/api/reviews/{r}/items/reorder")
    def reorder(r: str, data: dict):
        return service.reorder_items(r, data.get("ids", []))

    @app.post("/api/reviews/{r}/items", status_code=201)
    def add_item(r: str, data: dict):
        item = service.add_item(r, data)
        return item

    @app.patch("/api/reviews/{r}/items/{i}")
    def update_item(r: str, i: str, data: dict):
        item = service.update_item(r, i, data)
        return item

    @app.delete("/api/reviews/{r}/items/{i}")
    def delete_item(r: str, i: str):
        item = service.delete_item(r, i)
        return item

    @app.post("/api/reviews/{r}/items/{i}/restore")
    def restore_item(r: str, i: str):
        item = service.restore_item(r, i)
        return item

    @app.get("/api/reviews/{r}/papers")
    def papers(r: str):
        return service.list_papers(r)

    @app.post("/api/reviews/{r}/papers", status_code=202)
    async def upload_papers(r: str, files: list[UploadFile] = File(...)):
        service.get_review(r)
        result = []
        for upload in files:
            name = Path((upload.filename or "paper.pdf").replace("\\", "/")).name
            if not name.lower().endswith(".pdf"):
                raise ValueError(f"{name}: choose a PDF file.")
            folder = service.data_dir / "pdfs" / r
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / f"{uuid.uuid4()}.pdf"
            digest, size, first = hashlib.sha256(), 0, True
            try:
                with path.open("wb") as stream:
                    while chunk := await upload.read(1024 * 1024):
                        if first and b"%PDF-" not in chunk[:1024]:
                            raise ValueError(f"{name}: file does not have a PDF header.")
                        first = False
                        size += len(chunk)
                        if size > MAX_PDF_BYTES:
                            raise ValueError(f"{name}: PDF exceeds the 100 MB limit.")
                        digest.update(chunk)
                        stream.write(chunk)
                if size == 0:
                    raise ValueError(f"{name}: the file is empty.")
                paper = service.add_paper(r, {
                    "title": Path(name).stem, "filename": name, "path": str(path.resolve()),
                    "sha256": digest.hexdigest(), "processing_status": "pending",
                })
                if paper.get("duplicate"):
                    path.unlink(missing_ok=True)
                result.append(paper)
            except Exception:
                # The path is a newly generated UUID strictly inside this review folder.
                path.unlink(missing_ok=True)
                raise
            finally:
                await upload.close()
        return {"papers": result}

    @app.get("/api/reviews/{r}/papers/{p}")
    def paper(r: str, p: str):
        return service.get_paper(r, p)

    @app.delete("/api/reviews/{r}/papers/{p}")
    def remove_paper(r: str, p: str):
        return service.remove_paper(r, p)

    @app.patch("/api/reviews/{r}/papers/{p}")
    def update_paper(r: str, p: str, data: dict):
        allowed = {"title", "citation", "url", "included", "manual_review_ack"}
        if set(data) - allowed:
            raise ValueError("Only paper metadata, inclusion and manual review acknowledgment can be changed.")
        return service.update_paper(r, p, data)

    @app.get("/api/reviews/{r}/papers/{p}/outcomes")
    def outcomes(r: str, p: str, include_deleted: bool = False):
        return service.list_outcomes(r, p, include_deleted=include_deleted)

    @app.post("/api/reviews/{r}/papers/{p}/outcomes", status_code=201)
    def add_outcome(r: str, p: str, data: dict):
        return service.add_outcome(r, p, data)

    @app.patch("/api/reviews/{r}/papers/{p}/outcomes/{o}")
    def update_outcome(r: str, p: str, o: str, data: dict):
        return service.update_outcome(r, p, o, data)

    @app.delete("/api/reviews/{r}/papers/{p}/outcomes/{o}")
    def delete_outcome(r: str, p: str, o: str):
        return service.delete_outcome(r, p, o)

    @app.post("/api/reviews/{r}/papers/{p}/outcomes/{o}/restore")
    def restore_outcome(r: str, p: str, o: str):
        return service.restore_outcome(r, p, o)

    @app.get("/api/reviews/{r}/papers/{p}/pdf")
    def original_pdf(r: str, p: str):
        paper = service.get_paper(r, p)
        path = Path(paper["path"])
        if not path.is_file():
            raise HTTPException(404, "Original PDF is missing from local storage.")
        return FileResponse(path, media_type="application/pdf", filename=paper["filename"],
                            content_disposition_type="inline")

    @app.post("/api/reviews/{r}/papers/{p}/reprocess")
    def reprocess(r: str, p: str):
        return {"queued": scans.submit(r, p, reprocess=True)}

    @app.get("/api/reviews/{r}/papers/{p}/pages")
    def pages(r: str, p: str):
        return service.get_pages(r, p)

    @app.post("/api/reviews/{r}/refresh")
    def refresh(r: str, paper_id: str | None = None):
        service.get_review(r)
        papers = [service.get_paper(r, paper_id)] if paper_id else service.list_papers(r)
        count = sum(scans.submit(r, paper["id"]) for paper in papers)
        return {"queued": bool(count), "queued_count": count}

    @app.get("/api/reviews/{r}/papers/{p}/search")
    def search(r: str, p: str, q: str = Query(..., min_length=1, max_length=500)):
        return service.search_document(r, p, q)

    @app.get("/api/reviews/{r}/papers/{p}/suggestions")
    def suggestions(r: str, p: str, item_id: str, outcome_id: str | None = None):
        return service.get_suggestions(r, p, item_id, outcome_id)

    @app.post("/api/reviews/{r}/papers/{p}/items/{i}/candidates", status_code=201)
    def add_candidate(r: str, p: str, i: str, data: dict, outcome_id: str | None = None):
        return service.add_candidate(r, p, i, data, outcome_id)

    @app.get("/api/reviews/{r}/learning")
    def learning(r: str):
        return service.get_learning(r)

    @app.patch("/api/reviews/{r}/learning")
    def update_learning(r: str, data: dict):
        return service.update_learning(r, data)

    @app.patch("/api/reviews/{r}/learning/examples/{identity}")
    def exclude_learning_example(r: str, identity: str, data: dict):
        return service.exclude_learning_example(r, identity, data)

    @app.patch("/api/reviews/{r}/papers/{p}/suggestions/{s}")
    def decide_suggestion(r: str, p: str, s: str, data: dict, outcome_id: str | None = None):
        return service.decide_suggestion(r, p, s, data, outcome_id)

    @app.get("/api/reviews/{r}/papers/{p}/items/{i}/evidence")
    def evidence(r: str, p: str, i: str, outcome_id: str | None = None):
        return service.evidence_with_locations(r, p, i, outcome_id)

    @app.post("/api/reviews/{r}/papers/{p}/items/{i}/evidence", status_code=201)
    def add_evidence(r: str, p: str, i: str, data: dict, outcome_id: str | None = None):
        return service.add_evidence(r, p, i, data, outcome_id)

    @app.patch("/api/reviews/{r}/papers/{p}/evidence/{e}")
    def update_evidence(r: str, p: str, e: str, data: dict, outcome_id: str | None = None):
        return service.update_evidence(r, p, e, data, outcome_id)

    @app.get("/api/reviews/{r}/papers/{p}/items/{i}/field")
    def get_field(r: str, p: str, i: str, outcome_id: str | None = None):
        return service.get_field(r, p, i, outcome_id)

    @app.put("/api/reviews/{r}/papers/{p}/items/{i}/field")
    def save_field(r: str, p: str, i: str, data: dict, outcome_id: str | None = None):
        return service.save_field(r, p, i, data, outcome_id)

    @app.get("/api/reviews/{r}/validation")
    def validation(r: str):
        return validate_snapshot(service.export_snapshot(r))

    @app.get("/api/reviews/{r}/export-preview")
    def preview(r: str):
        preview = export_preview(service.export_snapshot(r), mark_unfinished=False, include_accepted=True)
        # The export page shows aggregate counts, not the detailed audit issue list.
        preview['validation'].pop('issues', None)
        return preview

    @app.get("/api/reviews/{r}/export")
    def export(r: str, draft: bool = True, verification: bool = False, view: str | None = None):
        if view is not None:
            content = export_download_workbook(service.export_snapshot(r), view)
            return Response(content, media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                            headers={'Content-Disposition': f'attachment; filename="review-{view}.xlsx"'})
        snapshot = service.snapshot(r)
        validation = validate_snapshot(snapshot)
        if not draft and not validation["can_finalize"]:
            return JSONResponse({"detail": "Resolve validation issues before a final export.",
                                 "validation": validation}, status_code=409)
        content = export_workbook(snapshot, draft=draft, verification=verification)
        name = f"review-{'verification-' if verification else ''}{'DRAFT' if draft else 'FINAL'}.xlsx"
        return Response(content,
                        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        headers={"Content-Disposition": f'attachment; filename="{name}"'})

    @app.get("/api/reviews/{r}/history")
    def history(r: str):
        return service.history(r)

    static = Path(__file__).parent / "static"
    app.mount("/static", StaticFiles(directory=static), name="static")

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(static / "index.html")

    return app
