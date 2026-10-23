"""Persistent single-reviewer decisions, review designs, and search orchestration."""
from copy import deepcopy
from datetime import date, datetime, timezone
from functools import wraps
import math
import os
import re
import shutil
from pathlib import Path
from uuid import uuid4

from .storage import Store
from .locations import source_location
from .keywords import suggest_keywords
from .retrieval import ENGINE_VERSION, search_item
from .search import boxes_for_span, search_pages, validate_rules
from .hybrid.models import ITEM_FIELDS, LIST_FIELDS
from .hybrid.chunking import chunk_document
from .hybrid.semantic import LocalSemantic
from .hybrid.neural_reranker import LocalReranker
from .feedback_state import (REJECTION_REASONS, config_for, document_fingerprints,
                             effective_examples, example_id, item_signature, latest_events)

DATA_TYPES = {"text", "number", "integer", "boolean", "date", "narrative"}
FIELD_STATUSES = {"unreviewed", "approved", "not_reported", "not_applicable", "unclear", "conflicting"}
DECISIONS = {"pending", "accepted", "rejected"}
ITEM_PROPERTIES = {"name", "export_heading", "worksheet_name", "section", "template_style", "source_column", "definition", "instructions", "data_type", "allowed_values", "units", "required", "position", "context", "keywords", "rules"} | ITEM_FIELDS
MATERIAL_PROPERTIES = {"definition", "instructions", "data_type", "allowed_values", "units", "context", "section"}


def now():
    return datetime.now(timezone.utc).isoformat()


def uid():
    return str(uuid4())


def transaction(method):
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        with self.store.transaction():
            return method(self, *args, **kwargs)
    return wrapped


def validate_item_data(data):
    if not isinstance(data, dict):
        raise ValueError("An extraction item must be an object")
    if not str(data.get("name") or data.get("export_heading") or "").strip():
        raise ValueError("An extraction item needs a name")
    if not isinstance(data.get("data_type", "text"), str) or data.get("data_type", "text") not in DATA_TYPES:
        raise ValueError("Unsupported data type")
    if not isinstance(data.get("section", "study"), str) or data.get("section", "study") not in {"study", "meta_analysis"}:
        raise ValueError("Item section must be study or meta_analysis")
    if "template_style" in data and not isinstance(data["template_style"], dict):
        raise ValueError("Template style must be an object")
    if "source_column" in data and (isinstance(data["source_column"], bool) or not isinstance(data["source_column"], int) or not 1 <= data["source_column"] <= 16384):
        raise ValueError("Source column must be an Excel column number")
    for key in ("name", "export_heading", "definition", "instructions", "units"):
        if key in data and not isinstance(data[key], str):
            raise ValueError(f"{key} must be text")
    if 'worksheet_name' in data:
        name = data['worksheet_name']
        if not isinstance(name, str) or not name.strip() or len(name) > 31 or any(c in name for c in '[]:*?/\\') or any(ord(c) < 32 for c in name):
            raise ValueError('Worksheet name must be 1–31 characters without Excel-reserved characters.')
    if "export_heading" in data and not data["export_heading"].strip():
        raise ValueError("Export heading cannot be empty")
    if not isinstance(data.get("keywords", []), list) or not all(isinstance(value, str) and value.strip() for value in data.get("keywords", [])):
        raise ValueError("Keywords must be nonempty strings")
    if not isinstance(data.get("allowed_values", []), list):
        raise ValueError("Allowed values must be a list")
    if any(isinstance(value, (dict, list)) for value in data.get("allowed_values", [])):
        raise ValueError("Allowed values must be scalar values")
    if not isinstance(data.get("context", {}), dict):
        raise ValueError("Item context must be an object")
    for key in LIST_FIELDS:
        if key in data and (not isinstance(data[key], list) or not all(isinstance(v, str) and v.strip() for v in data[key])):
            raise ValueError(f"{key} must be a list of nonempty strings")
    for key in ("display_name", "description", "expected_type", "search_mode"):
        if key in data and not isinstance(data[key], str):
            raise ValueError(f"{key} must be text")
    if data.get("search_mode", "intelligent") not in {"fast", "intelligent"}:
        raise ValueError("Search mode must be fast or intelligent")
    if data.get("expected_type", "text") not in DATA_TYPES | {"number_unit", "statistic", "trial_id"}:
        raise ValueError("Unsupported expected type")
    penalty = data.get("negative_penalty", .35)
    if isinstance(penalty, bool) or not isinstance(penalty, (int, float)) or not math.isfinite(penalty) or not 0 <= penalty <= 1:
        raise ValueError("Negative penalty must be between zero and one")
    if not isinstance(data.get("required", False), bool):
        raise ValueError("Required must be true or false")
    if "position" in data and (isinstance(data["position"], bool) or not isinstance(data["position"], int) or data["position"] < 0):
        raise ValueError("Position must be a nonnegative integer")
    validate_rules(data.get("rules", []))
    return data


class ReviewService:
    def __init__(self, data_dir):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.store = Store(self.data_dir / "reviews.sqlite3")
        self.semantic = LocalSemantic(self.data_dir / "retrieval-cache")
        self.reranker = LocalReranker(self.data_dir / "retrieval-cache")

    def retrieval_model_config(self):
        return dict(embedding_model=self.semantic.model_name,
                    embedding_revision=os.environ.get("SYSTEMATIC_EMBEDDING_REVISION", "main"),
                    reranker_model=self.reranker.model_name if self.reranker.enabled else None,
                    reranker_revision=self.reranker.revision if self.reranker.enabled else None,
                    reranker_weight=os.environ.get("SYSTEMATIC_RERANKER_WEIGHT", ".35")
                    if self.reranker.enabled else None)

    def _review(self, review_id):
        return self.store.get("reviews", review_id)

    def _item(self, review_id, item_id):
        item = self.store.get("items", item_id)
        if item["review_id"] != review_id:
            raise KeyError(item_id)
        return item

    def _paper(self, review_id, paper_id):
        paper = self.store.get("papers", paper_id)
        if paper["review_id"] != review_id:
            raise KeyError(paper_id)
        return paper

    def _scoped_record(self, table, review_id, paper_id, record_id):
        self._paper(review_id, paper_id)
        record = self.store.get(table, record_id)
        if record["review_id"] != review_id or record["paper_id"] != paper_id:
            raise KeyError(record_id)
        return record

    def _outcome(self, review_id, paper_id, outcome_id, *, include_deleted=False):
        row = self._scoped_record("outcomes", review_id, paper_id, outcome_id)
        if row.get("deleted") and not include_deleted:
            raise ValueError("This outcome row was removed; restore it before editing")
        return row

    def _field_scope(self, review_id, paper_id, item_id, outcome_id=None):
        """Validate the whole context before reading or mutating a field or evidence."""
        self._paper(review_id, paper_id)
        item = self._item(review_id, item_id)
        if outcome_id is not None:
            if not isinstance(outcome_id, str) or not outcome_id:
                raise ValueError("Outcome ID must be a nonempty string")
            self._outcome(review_id, paper_id, outcome_id)
            if item.get("section", "study") != "meta_analysis":
                raise ValueError("Outcome rows use items in the meta-analysis section")
        elif item.get("section", "study") == "meta_analysis":
            raise ValueError("Choose an outcome row for this meta-analysis item")
        return item

    def _field_records(self, **filters):
        for table in ("fields", "outcome_fields"):
            for field in self.store.list(table, **filters):
                yield table, field

    def _history(self, review_id, action, before=None, after=None, paper_id=None, item_id=None, reviewer=None, outcome_id=None):
        review = self._review(review_id)
        context = after if isinstance(after, dict) else before if isinstance(before, dict) else {}
        return self.store.put("history", {
            "id": uid(), "review_id": review_id, "paper_id": paper_id, "item_id": item_id,
            "outcome_id": outcome_id or context.get("outcome_id"),
            "action": action, "before": before, "after": after,
            "reviewer": review["reviewer"] if reviewer is None else reviewer,
            "created_at": now(), "schema_version": review["schema_version"], "rule_version": review["rule_version"],
        })

    def _bump(self, review_id, schema=False, rules=False):
        review = self._review(review_id)
        review["schema_version"] += int(schema)
        review["rule_version"] += int(rules)
        review["updated_at"] = now()
        self.store.put("reviews", review)
        if rules:
            for paper in self.store.list("papers", review_id=review_id):
                paper["search_status"] = "stale"
                self.store.put("papers", paper)
        return review

    def list_reviews(self):
        return sorted(self.store.list("reviews"), key=lambda row: row["updated_at"], reverse=True)

    @transaction
    def create_review(self, data):
        if not isinstance(data.get("name", ""), str) or not isinstance(data.get("reviewer", ""), str):
            raise ValueError("Review name and reviewer must be text")
        name = data.get("name", "").strip()
        if not name:
            raise ValueError("Review name is required")
        reviewer = data.get("reviewer", "").strip()
        review = {"id": uid(), "name": name, "reviewer": reviewer, "schema_version": 1,
                  "rule_version": 1, "created_at": now(), "updated_at": now(), "settings": data.get("settings", {})}
        if not isinstance(review["settings"], dict):
            raise ValueError("Settings must be an object")
        self.store.put("reviews", review)
        self._history(review["id"], "review_created", after=review)
        return review

    def get_review(self, review_id):
        return self._review(review_id)

    def delete_review(self, review_id, confirm_name):
        """Permanently delete a review, its records and its stored PDF copies.

        The caller must repeat the exact review name, so a stray request cannot
        remove work. Files are only removed inside this workspace's pdfs folder.
        """
        review = self._review(review_id)
        if not isinstance(confirm_name, str) or confirm_name.strip() != review["name"].strip():
            raise ValueError("Type the review name exactly to confirm deletion.")
        papers = self.store.list("papers", review_id=review_id)
        self.store.delete_review(review_id)
        storage = (self.data_dir / "pdfs").resolve()
        for paper in papers:
            try:
                path = Path(paper.get("path") or "").resolve()
                if path.is_file() and path.is_relative_to(storage):
                    path.unlink()
            except OSError:
                pass
        folder = storage / review_id
        if folder.is_dir() and folder.resolve().is_relative_to(storage):
            shutil.rmtree(folder, ignore_errors=True)
        return {"deleted": review_id, "name": review["name"], "papers": len(papers)}

    @transaction
    def update_review(self, review_id, data):
        before = self._review(review_id)
        review = deepcopy(before)
        for key in ("name", "reviewer"):
            if key in data:
                if not isinstance(data[key], str) or (key == "name" and not data[key].strip()):
                    raise ValueError(f"Invalid {key}")
                review[key] = data[key].strip()
        if "settings" in data:
            if not isinstance(data["settings"], dict):
                raise ValueError("Settings must be an object")
            settings = data["settings"]
            for key in ("last_paper_id", "last_item_id", "last_outcome_id"):
                if settings.get(key) is not None and not isinstance(settings[key], str):
                    raise ValueError("Resume locations must be paper/item/outcome IDs")
            if settings.get("last_paper_id"):
                self._paper(review_id, settings["last_paper_id"])
            if settings.get("last_item_id"):
                self._item(review_id, settings["last_item_id"])
            if "last_section" in settings and (not isinstance(settings["last_section"], str) or settings["last_section"] not in {"study", "meta_analysis"}):
                raise ValueError("Resume section must be study or meta_analysis")
            if settings.get("last_outcome_id"):
                last_paper_id = settings.get("last_paper_id", review["settings"].get("last_paper_id"))
                if not last_paper_id:
                    raise ValueError("Choose a paper before saving an outcome resume location")
                self._outcome(review_id, last_paper_id, settings["last_outcome_id"])
            elif "last_paper_id" in settings and settings["last_paper_id"] != review["settings"].get("last_paper_id") and "last_outcome_id" not in settings:
                review["settings"]["last_outcome_id"] = None
            review["settings"].update(settings)
        review["updated_at"] = now()
        self.store.put("reviews", review)
        self._history(review_id, "review_updated", before, review)
        return review

    def get_learning(self, review_id):
        review = self._review(review_id)
        examples = effective_examples(review, self.store.list("items", review_id=review_id),
            self.store.list("papers", review_id=review_id), self.store.list("pages", review_id=review_id),
            self.store.list("suggestions", review_id=review_id),
            [row for row in self.store.list("evidence", review_id=review_id) if not row.get("outcome_id")],
            self.store.list("feedback", review_id=review_id))
        return dict(config=config_for(review), examples=examples)

    def _learning_changed(self, review_id, *, configuration=False):
        review = self._review(review_id)
        config = config_for(review)
        config["revision"] += 1
        if configuration: config["version"] += 1
        review["learning"] = config
        self.store.put("reviews", review)
        if config["enabled"] or configuration:
            for paper in self.store.list("papers", review_id=review_id):
                if not paper.get("removed"):
                    paper["search_status"] = "stale"
                    self.store.put("papers", paper)
        return config

    @transaction
    def update_learning(self, review_id, data):
        before = self._review(review_id)
        config = config_for(before)
        if not isinstance(data, dict) or not set(data) <= {"enabled", "reset"}:
            raise ValueError("Learning accepts enabled or reset")
        for key in data:
            if not isinstance(data[key], bool): raise ValueError("Learning controls must be boolean")
        if "enabled" in data: config["enabled"] = data["enabled"]
        if data.get("reset"): config["epoch"] += 1
        if config == config_for(before): return self.get_learning(review_id)
        self.store.put("reviews", dict(before, learning=config))
        self._learning_changed(review_id, configuration=True)
        if config["enabled"]: self._import_manual_feedback(review_id)
        self._history(review_id, "learning_configuration_changed", config_for(before), config_for(self._review(review_id)))
        return self.get_learning(review_id)

    @transaction
    def exclude_learning_example(self, review_id, identity, data):
        if not isinstance(data.get("excluded"), bool): raise ValueError("Excluded must be boolean")
        if identity not in {e["example_id"] for e in self.get_learning(review_id)["examples"]}: raise KeyError(identity)
        review = self._review(review_id)
        before = config_for(review)
        config = deepcopy(before)
        excluded = set(config["excluded_examples"])
        if data["excluded"]: excluded.add(identity)
        else: excluded.discard(identity)
        config["excluded_examples"] = sorted(excluded)
        if config != before:
            self.store.put("reviews", dict(review, learning=config))
            self._learning_changed(review_id, configuration=True)
            self._history(review_id, "learning_example_excluded", before, config_for(self._review(review_id)))
        return self.get_learning(review_id)

    def _import_manual_feedback(self, review_id):
        """Add legacy manual decisions only with their historical item snapshot."""
        events = latest_events(self.store.list("feedback", review_id=review_id))
        # SQLite insertion order resolves Windows clock ties (several decisions
        # can share the same timestamp). Never import a later item definition.
        history = self.store.list("history", review_id=review_id)
        for evidence in self.store.list("evidence", review_id=review_id):
            if evidence.get("outcome_id") or evidence.get("suggestion_id") or example_id(evidence, True) in events: continue
            decisions = [index for index, h in enumerate(history) if h["action"] in {"evidence_added", "evidence_decided", "evidence_updated"}
                         and (h.get("after") or {}).get("id") == evidence["id"]
                         and (h.get("before") or {}).get("decision") != (h.get("after") or {}).get("decision")]
            if not decisions:
                continue
            snapshots = [h["after"] for h in history[:decisions[-1]] if h.get("item_id") == evidence["item_id"]
                         and h["action"] in {"item_added", "item_updated"}]
            if snapshots:
                self._save_feedback(review_id, evidence["paper_id"], evidence, evidence=True,
                                    item_snapshot=snapshots[-1], epoch=0)

    def list_items(self, review_id, include_deleted=False):
        self._review(review_id)
        return sorted((dict(item, section=item.get("section", "study")) for item in self.store.list("items", review_id=review_id) if include_deleted or not item["deleted"]),
                      key=lambda item: (item["position"], item["id"]))

    @transaction
    def initialize_keywords(self, review_id):
        """One-time upgrade of untouched legacy lists, including deleted items.

        An empty list after a recorded keyword edit is intentional. Persist the
        initialization marker even for empty lists so restart never refills them.
        """
        items = self.list_items(review_id, include_deleted=True)
        pending = [item for item in items if not item.get("keywords_initialized")]
        if not pending:
            return
        edited = {event["item_id"] for event in self.store.list("history", review_id=review_id)
                  if event["action"] == "item_updated"
                  and (event.get("before") or {}).get("keywords") != (event.get("after") or {}).get("keywords")}
        for item in pending:
            before = deepcopy(item)
            if not item["keywords"] and item["id"] not in edited:
                item = self.update_item(review_id, item["id"], {"keywords": suggest_keywords(item)})
            item["keywords_initialized"] = True
            self.store.put("items", item)
            self._history(review_id, "item_keywords_initialized", before, item, item_id=item["id"])

    @transaction
    def add_item(self, review_id, data):
        self._review(review_id)
        data = {key: deepcopy(value) for key, value in data.items() if key in ITEM_PROPERTIES}
        data.setdefault("name", data.get("export_heading", ""))
        data.setdefault("export_heading", data["name"])
        validate_item_data(data)
        if "keywords" not in data:
            data["keywords"] = suggest_keywords(data)
        review = self._bump(review_id, schema=True, rules=True)
        item = {"id": uid(), "review_id": review_id, "name": data["name"].strip(), "export_heading": data["export_heading"].strip(),
                "definition": "", "instructions": "", "data_type": "text", "allowed_values": [], "units": "", "required": False,
                "position": max([row["position"] for row in self.list_items(review_id, True)], default=-1) + 1,
                "context": {}, "keywords": [], "keywords_initialized": True, "rules": [], "deleted": False,
                "section": "study",
                "schema_version": review["schema_version"], "rule_version": review["rule_version"]}
        # New columns append. The dedicated reorder operation moves active columns explicitly.
        item.update({key: value for key, value in data.items() if key != "position"})
        self.store.put("items", item)
        for paper in self.list_papers(review_id):
            if item["section"] == "study":
                self._ensure_field(review_id, paper["id"], item["id"])
            else:
                for outcome in self.list_outcomes(review_id, paper["id"]):
                    self._ensure_field(review_id, paper["id"], item["id"], outcome["id"])
        self._history(review_id, "item_added", after=item, item_id=item["id"])
        return item

    @transaction
    def update_item(self, review_id, item_id, data):
        before = self._item(review_id, item_id)
        item = deepcopy(before)
        item.update({key: deepcopy(value) for key, value in data.items() if key in ITEM_PROPERTIES})
        validate_item_data(item)
        if "keywords" in data:
            item["keywords_initialized"] = True
        changed = {key for key in ITEM_PROPERTIES if item.get(key) != before.get(key)}
        if not changed:
            if item.get("keywords_initialized") != before.get("keywords_initialized"):
                self.store.put("items", item)
            return item
        rules_changed = bool(changed & ({"keywords", "rules", "name", "export_heading", "definition", "instructions", "context", "units", "data_type"} | ITEM_FIELDS)) or item_signature(before) != item_signature(item)
        schema_changed = bool(changed - {"keywords", "rules"})
        review = self._bump(review_id, schema=schema_changed, rules=rules_changed)
        if item_signature(before) != item_signature(item):
            item["learning_version"] = before.get("learning_version", 0) + 1
        item.update(schema_version=review["schema_version"], rule_version=review["rule_version"])
        self.store.put("items", item)
        if "position" in changed and not item["deleted"]:
            active = [row for row in self.list_items(review_id) if row["id"] != item_id]
            active.insert(min(item["position"], len(active)), item)
            for position, row in enumerate(active):
                row.update(position=position, schema_version=review["schema_version"])
                self.store.put("items", row)
            item = self._item(review_id, item_id)
        if changed & (MATERIAL_PROPERTIES | {"description", "expected_type", "expected_units"}):
            for table, field in self._field_records(review_id=review_id, item_id=item_id):
                if field["status"] != "unreviewed":
                    previous = deepcopy(field)
                    field["needs_reconsideration"] = True
                    self.store.put(table, field)
                    self._history(review_id, "field_reconsideration_required", previous, field, field["paper_id"], item_id)
        self._history(review_id, "item_updated", before, item, item_id=item_id)
        return item

    @transaction
    def reorder_items(self, review_id, ids):
        items = self.list_items(review_id)
        if not isinstance(ids, list) or not all(isinstance(item_id, str) for item_id in ids) or len(ids) != len(set(ids)) or set(ids) != {item["id"] for item in items}:
            raise ValueError("Reordering requires each active item exactly once")
        before = [item["id"] for item in items]
        review = self._bump(review_id, schema=True)
        for position, item_id in enumerate(ids):
            item = self._item(review_id, item_id)
            item.update(position=position, schema_version=review["schema_version"])
            self.store.put("items", item)
        self._history(review_id, "items_reordered", before, ids)
        return self.list_items(review_id)

    @transaction
    def delete_item(self, review_id, item_id):
        return self._set_deleted(review_id, item_id, True)

    @transaction
    def restore_item(self, review_id, item_id):
        return self._set_deleted(review_id, item_id, False)

    def _set_deleted(self, review_id, item_id, deleted):
        before = self._item(review_id, item_id)
        if before["deleted"] == deleted:
            return before
        review = self._bump(review_id, schema=True, rules=True)
        item = dict(before, deleted=deleted, schema_version=review["schema_version"], rule_version=review["rule_version"])
        self.store.put("items", item)
        active = [row for row in self.list_items(review_id) if row["id"] != item_id]
        if not deleted:
            active.insert(min(item["position"], len(active)), item)
        for position, row in enumerate(active):
            row.update(position=position, schema_version=review["schema_version"])
            self.store.put("items", row)
        item = self._item(review_id, item_id)
        if deleted:
            for suggestion in self.store.list("suggestions", review_id=review_id, item_id=item_id):
                suggestion["active"] = False
                self.store.put("suggestions", suggestion)
        else:
            for paper in self.list_papers(review_id):
                if item.get("section", "study") == "study":
                    self._ensure_field(review_id, paper["id"], item_id)
                else:
                    for outcome in self.list_outcomes(review_id, paper["id"]):
                        self._ensure_field(review_id, paper["id"], item_id, outcome["id"])
        self._history(review_id, "item_deleted" if deleted else "item_restored", before, item, item_id=item_id)
        return item

    def list_papers(self, review_id, include_removed=False):
        self._review(review_id)
        return sorted((paper for paper in self.store.list("papers", review_id=review_id)
                       if include_removed or not paper.get("removed", False)), key=lambda row: row["created_at"])

    @transaction
    def remove_paper(self, review_id, paper_id):
        before = self._paper(review_id, paper_id)
        if before.get("removed"):
            return before
        paper = dict(before, removed=True)
        self.store.put("papers", paper)
        for suggestion in self.store.list("suggestions", review_id=review_id, paper_id=paper_id):
            suggestion["active"] = False
            self.store.put("suggestions", suggestion)
        review = self._review(review_id)
        if review["settings"].get("last_paper_id") == paper_id:
            remaining = self.list_papers(review_id)
            review["settings"]["last_paper_id"] = remaining[0]["id"] if remaining else None
        review["updated_at"] = now()
        self.store.put("reviews", review)
        self._learning_changed(review_id)
        self._history(review_id, "paper_removed", before, paper, paper_id=paper_id)
        return paper

    def get_paper(self, review_id, paper_id):
        return self._paper(review_id, paper_id)

    @transaction
    def add_paper(self, review_id, data):
        self._review(review_id)
        if data.get("sha256"):
            for paper in self.list_papers(review_id, include_removed=True):
                if paper.get("sha256") == data["sha256"]:
                    if paper.get("removed"):
                        before = deepcopy(paper)
                        paper.update(removed=False, search_status="stale")
                        self.store.put("papers", paper)
                        self._learning_changed(review_id)
                        for item in self.list_items(review_id):
                            if item.get("section", "study") == "study":
                                self._ensure_field(review_id, paper["id"], item["id"])
                        self._history(review_id, "paper_restored", before, paper, paper_id=paper["id"])
                        return dict(paper, duplicate=True, restored=True)
                    return dict(paper, duplicate=True)
        paper = {"id": uid(), "review_id": review_id, "title": "", "citation": "", "url": "", "filename": "", "sha256": "", "path": "",
                 "included": True, "processing_status": "pending", "processing_progress": 0, "processing_error": "", "page_count": 0,
                 "search_version": 0, "search_status": "stale", "manual_review_ack": False, "document_version": 0, "created_at": now(),
                 "pdf_metadata": None, "retrieval_engine_version": "", "removed": False}
        for key in paper.keys() - {"id", "review_id", "created_at"}:
            if key in data:
                paper[key] = deepcopy(data[key])
        paper["title"] = paper["title"] or paper["filename"] or "Untitled paper"
        self._validate_paper(paper)
        self.store.put("papers", paper)
        for item in self.list_items(review_id):
            if item.get("section", "study") == "study":
                self._ensure_field(review_id, paper["id"], item["id"])
        self._history(review_id, "paper_added", after=paper, paper_id=paper["id"])
        return paper

    def _validate_paper(self, paper):
        for key in ("title", "citation", "url", "filename", "sha256", "path", "processing_error"):
            if not isinstance(paper[key], str):
                raise ValueError(f"Paper {key} must be text")
        if not isinstance(paper["processing_status"], str) or paper["processing_status"] not in {"pending", "queued", "processing", "ready", "error"}:
            raise ValueError("Invalid processing status")
        if not isinstance(paper["search_status"], str) or paper["search_status"] not in {"stale", "queued", "refreshing", "current", "partial", "error"}:
            raise ValueError("Invalid search status")
        if not isinstance(paper["included"], bool) or not isinstance(paper["manual_review_ack"], bool):
            raise ValueError("Included and manual review acknowledgement must be boolean")
        if not isinstance(paper["processing_progress"], (int, float)) or not 0 <= paper["processing_progress"] <= 100:
            raise ValueError("Processing progress must be between 0 and 100")

    @transaction
    def update_paper(self, review_id, paper_id, data):
        before = self._paper(review_id, paper_id)
        paper = deepcopy(before)
        for key in (before.keys() | {"search_progress", "search_message"}) - {"id", "review_id", "created_at", "sha256", "path", "filename"}:
            if key in data:
                paper[key] = deepcopy(data[key])
        self._validate_paper(paper)
        self.store.put("papers", paper)
        if set(data) & {"title", "citation", "url", "included", "manual_review_ack"}:
            self._history(review_id, "paper_updated", before, paper, paper_id=paper_id)
        return paper

    def list_outcomes(self, review_id, paper_id, include_deleted=False):
        self._paper(review_id, paper_id)
        return sorted((row for row in self.store.list("outcomes", review_id=review_id, paper_id=paper_id)
                       if include_deleted or not row.get("deleted")), key=lambda row: (row["position"], row["id"]))

    def _validate_outcome(self, outcome):
        for key in ("label", "outcome", "group", "time_point", "units", "notes"):
            if not isinstance(outcome.get(key, ""), str) or len(outcome.get(key, "")) > 2000:
                raise ValueError(f"Outcome {key} must be text of at most 2000 characters")
        if not outcome["label"].strip():
            raise ValueError("Give this outcome row a label")
        if not isinstance(outcome["included"], bool):
            raise ValueError("Outcome included must be true or false")
        if isinstance(outcome["position"], bool) or not isinstance(outcome["position"], int) or outcome["position"] < 0:
            raise ValueError("Outcome position must be a nonnegative integer")

    @transaction
    def add_outcome(self, review_id, paper_id, data):
        self._paper(review_id, paper_id)
        rows = self.list_outcomes(review_id, paper_id, include_deleted=True)
        row = dict(id=uid(), review_id=review_id, paper_id=paper_id,
                   label=f"Outcome {len(rows) + 1}", outcome="", group="", time_point="", units="", notes="",
                   included=True, deleted=False, position=max((r["position"] for r in rows), default=-1) + 1,
                   created_at=now(), updated_at=now())
        for key in ("label", "outcome", "group", "time_point", "units", "notes", "included", "position"):
            if key in data:
                row[key] = deepcopy(data[key])
        self._validate_outcome(row)
        self.store.put("outcomes", row)
        for item in self.list_items(review_id):
            if item.get("section", "study") == "meta_analysis":
                self._ensure_field(review_id, paper_id, item["id"], row["id"])
        self._bump(review_id)
        self._history(review_id, "outcome_added", after=row, paper_id=paper_id, outcome_id=row["id"])
        return row

    @transaction
    def update_outcome(self, review_id, paper_id, outcome_id, data):
        before = self._outcome(review_id, paper_id, outcome_id)
        row = deepcopy(before)
        for key in ("label", "outcome", "group", "time_point", "units", "notes", "included", "position"):
            if key in data:
                row[key] = deepcopy(data[key])
        self._validate_outcome(row)
        row["updated_at"] = now()
        self.store.put("outcomes", row)
        # A changed row context can change the meaning of every approved statistic.
        if any(before.get(key) != row.get(key) for key in ("outcome", "group", "time_point", "units")):
            for field in self.store.list("outcome_fields", review_id=review_id, paper_id=paper_id, outcome_id=outcome_id):
                if field["status"] != "unreviewed":
                    prior = deepcopy(field)
                    field["needs_reconsideration"] = True
                    self.store.put("outcome_fields", field)
                    self._history(review_id, "outcome_context_reconsideration", prior, field, paper_id, field["item_id"])
        self._bump(review_id)
        self._history(review_id, "outcome_updated", before, row, paper_id=paper_id, outcome_id=outcome_id)
        return row

    @transaction
    def delete_outcome(self, review_id, paper_id, outcome_id):
        return self._set_outcome_deleted(review_id, paper_id, outcome_id, True)

    @transaction
    def restore_outcome(self, review_id, paper_id, outcome_id):
        return self._set_outcome_deleted(review_id, paper_id, outcome_id, False)

    def _set_outcome_deleted(self, review_id, paper_id, outcome_id, deleted):
        before = self._outcome(review_id, paper_id, outcome_id, include_deleted=True)
        if before.get("deleted", False) == deleted:
            return before
        row = dict(before, deleted=deleted, updated_at=now())
        self.store.put("outcomes", row)
        self._bump(review_id)
        self._history(review_id, "outcome_deleted" if deleted else "outcome_restored", before, row,
                      paper_id=paper_id, outcome_id=outcome_id)
        return row

    @transaction
    def set_pages(self, review_id, paper_id, pages):
        paper = self._paper(review_id, paper_id)
        page_numbers = [page.get("page_number") for page in pages]
        if any(isinstance(number, bool) or not isinstance(number, int) for number in page_numbers) or page_numbers != list(range(1, len(pages) + 1)):
            raise ValueError("Pages must have consecutive physical numbers starting at 1")
        old = {page["page_number"]: page for page in self.get_pages(review_id, paper_id)}
        self.store.delete_pages(paper_id)
        for page in pages:
            row = dict(page, review_id=review_id, paper_id=paper_id)
            if row.get("status") not in {"ok", "ocr_required", "error"}:
                raise ValueError("Invalid page status")
            row.setdefault("text", "")
            row.setdefault("words", [])
            row.setdefault("ocr", False)
            row.setdefault("error", "")
            self.store.put("pages", row)
        # Re-extraction can change text or geometry. Preserve prior evidence, but require explicit reconsideration.
        new = {page["page_number"]: page for page in pages}
        source_keys = ("text", "width", "height", "rotation", "words", "status", "ocr",
                       "tables", "ocr_blocks", "ocr_confidence", "engine_version")
        changed_pages = {number for number in set(old) | set(new)
                         if any(old.get(number, {}).get(key) != new.get(number, {}).get(key) for key in source_keys)}
        if old and changed_pages:
            # Keep the previous text AND coordinate map accessible after a source
            # replacement; evidence retains its original offsets and quote.
            self._history(review_id, "document_source_changed",
                          {"document_version": paper.get("document_version", 0), "pages": list(old.values())},
                          {"document_version": paper.get("document_version", 0) + 1,
                           "changed_pages": sorted(changed_pages),
                           "reading_order": {str(n): p.get("reading_order") for n, p in new.items()}}, paper_id)
            for evidence in self.store.list("evidence", review_id=review_id, paper_id=paper_id):
                if evidence["page_number"] in changed_pages:
                    previous = deepcopy(evidence)
                    evidence["source_stale"] = True
                    self.store.put("evidence", evidence)
                    self._history(review_id, "evidence_source_changed", previous, evidence, paper_id, evidence["item_id"])
            for table, field in self._field_records(review_id=review_id, paper_id=paper_id):
                if field["status"] != "unreviewed":
                    before = deepcopy(field)
                    field["needs_reconsideration"] = True
                    self.store.put(table, field)
                    self._history(review_id, "document_reprocessed_reconsideration", before, field, paper_id, field["item_id"])
        paper.update(page_count=len(pages), search_status="stale", document_version=paper.get("document_version", 0) + 1)
        self.store.put("papers", paper)
        self._learning_changed(review_id)
        return self.get_pages(review_id, paper_id)

    def get_pages(self, review_id, paper_id):
        self._paper(review_id, paper_id)
        return sorted(self.store.list("pages", review_id=review_id, paper_id=paper_id), key=lambda row: row["page_number"])

    def refresh_search(self, review_id, paper_id=None):
        self._review(review_id)
        papers = [self._paper(review_id, paper_id)] if paper_id else self.list_papers(review_id)
        results = []
        for paper in papers:
            if paper["processing_status"] != "ready":
                continue
            with self.store.transaction():
                current = self._paper(review_id, paper["id"])
                if current.get("removed"):
                    continue
                current.update(search_status="refreshing", search_progress=0, search_errors=[],
                               search_message="Preparing search")
                self.store.put("papers", current)
                review = self._review(review_id)
                document_version = current.get("document_version", 0)
                metadata = deepcopy(current.get("pdf_metadata"))
                items = self.list_items(review_id)
                pages = self.get_pages(review_id, paper["id"])
                learning = self.get_learning(review_id)
                learning_revision = learning["config"]["revision"]
                target_fingerprints = document_fingerprints(current, pages)
            collected, errors, warnings = {}, [], []
            chunks = chunk_document(pages)
            for index, item in enumerate(items):
                with self.store.transaction():
                    current = self._paper(review_id, paper["id"])
                    current["search_message"] = f'Searching {item["name"]} ({index + 1} of {len(items)})'
                    self.store.put("papers", current)
                feedback = dict(config=learning["config"], target_document_fingerprints=target_fingerprints,
                    target_paper_id=paper["id"], examples=[e for e in learning["examples"] if e["item_id"] == item["id"] and e["eligible"]])
                result = search_item(pages, item, metadata, semantic=self.semantic, chunks=chunks,
                                     feedback=feedback, reranker=self.reranker)
                collected[item["id"]] = result["matches"]
                warnings.extend(result.get("warnings", []))
                errors.extend(dict(error, item_id=item["id"]) for error in result["errors"])
                with self.store.transaction():
                    current = self._paper(review_id, paper["id"])
                    current["search_progress"] = round((index + 1) / max(1, len(items)) * 100)
                    self.store.put("papers", current)
            with self.store.transaction():
                if self._paper(review_id, paper["id"]).get("removed"):
                    continue
                existing = self.store.list("suggestions", review_id=review_id, paper_id=paper["id"])
                by_span = {(row["item_id"], row.get("source_type", "pdf_text"), row["page_number"], row["start"], row["end"], row["quote"], row.get("table_id"), row.get("table_column")): row for row in sorted(existing, key=lambda row: row.get("origin") == "user")}
                for suggestion in existing:
                    suggestion["active"] = bool(suggestion.get("origin") == "user" and suggestion.get("document_version", 0) == document_version
                        and suggestion["item_id"] in {i["id"] for i in items})
                    if suggestion["active"]:
                        suggestion["rule_version"] = review["rule_version"]
                        # A retained user span is discoverable even if automatic
                        # retrieval did not select it. Never reuse old scores.
                        for key in ("rank", "rank_score", "scores", "learning", "semantic_basis", "ranking_weights"):
                            suggestion.pop(key, None)
                    self.store.put("suggestions", suggestion)
                active_ids = {item["id"] for item in self.list_items(review_id)}
                for item_id, matches in collected.items():
                    if item_id not in active_ids:
                        continue
                    for match in matches:
                        key = (item_id, match.get("source_type", "pdf_text"), match["page_number"], match["start"], match["end"], match["quote"], match.get("table_id"), match.get("table_column"))
                        prior = by_span.get(key)
                        # Changed source wording at an old location is a new passage, not an inherited decision.
                        if prior and prior["quote"] != match["quote"]:
                            prior = None
                        suggestion = dict(match, id=prior["id"] if prior else uid(), review_id=review_id, paper_id=paper["id"], item_id=item_id,
                                          rule_version=review["rule_version"], document_version=document_version, active=True, decision=prior["decision"] if prior else "pending",
                                          created_at=prior["created_at"] if prior else now())
                        if prior:
                            for key in ("origin", "user_selected", "notes", "rejection_reason", "overlapping_candidate_ids"):
                                if key in prior: suggestion[key] = prior[key]
                        suggestion.setdefault("origin", "algorithm")
                        self.store.put("suggestions", suggestion)
                current = self._paper(review_id, paper["id"])
                fresh = (self._review(review_id)["rule_version"] == review["rule_version"]
                         and config_for(self._review(review_id))["revision"] == learning_revision
                         and current.get("pdf_metadata") == metadata
                         and current.get("document_version", 0) == document_version and current["processing_status"] == "ready")
                current.update(search_status=("partial" if errors else "current") if fresh else "stale", search_version=review["rule_version"], search_progress=100, search_errors=errors, search_message="",
                               retrieval_engine_version=ENGINE_VERSION,
                               retrieval_model_config=self.retrieval_model_config(),
                               retrieval_warnings=list(dict.fromkeys(warnings)),
                               learning_revision=learning_revision, learning_config_version=learning["config"]["version"])
                self.store.put("papers", current)
                results.append(current)
        return results

    def search_document(self, review_id, paper_id, query):
        self._paper(review_id, paper_id)
        if not isinstance(query, str) or not query.strip():
            return []
        result = search_pages(self.get_pages(review_id, paper_id), [query])
        return [dict(match, id=f'search:{paper_id}:{match["page_number"]}:{match["start"]}:{match["end"]}',
                     paper_id=paper_id, item_id=None, active=True, decision="pending", rule_version=self._review(review_id)["rule_version"]) for match in result["matches"]]

    def get_suggestions(self, review_id, paper_id, item_id, outcome_id=None):
        paper = self._paper(review_id, paper_id)
        self._field_scope(review_id, paper_id, item_id, outcome_id)
        review = self._review(review_id)
        rows = self.store.list("suggestions", review_id=review_id, paper_id=paper_id, item_id=item_id)
        if outcome_id is not None:
            decisions = {row.get("suggestion_id"): row for row in self.get_evidence(review_id, paper_id, item_id, outcome_id)
                         if row.get("suggestion_id")}
            rows = [dict(row, decision=decisions.get(row["id"], {}).get("decision", "pending"),
                         notes=decisions.get(row["id"], {}).get("notes", ""),
                         rejection_reason=decisions.get(row["id"], {}).get("rejection_reason", ""),
                         evidence_id=decisions.get(row["id"], {}).get("id"), outcome_id=outcome_id) for row in rows]
        return sorted((dict(row, stale=row["rule_version"] != review["rule_version"] or paper["search_status"] != "current"
                            or row.get("document_version", 0) != paper.get("document_version", 0)) for row in rows if row["active"] or row["decision"] != "pending" or row.get("origin") == "user"),
                      key=lambda row: (not row["active"], -row.get("rank_score", 0), row["page_number"] or 0, row["start"] or 0, row["created_at"]))

    @transaction
    def decide_suggestion(self, review_id, paper_id, suggestion_id, data, outcome_id=None):
        before = self._scoped_record("suggestions", review_id, paper_id, suggestion_id)
        self._field_scope(review_id, paper_id, before["item_id"], outcome_id)
        if outcome_id is not None:
            current = next((row for row in self.get_evidence(review_id, paper_id, before["item_id"], outcome_id)
                            if row.get("suggestion_id") == suggestion_id), {})
            before = dict(before, outcome_id=outcome_id, decision=current.get("decision", "pending"),
                          notes=current.get("notes", ""), rejection_reason=current.get("rejection_reason", ""))
        decision = data.get("decision")
        if not isinstance(decision, str) or decision not in DECISIONS:
            raise ValueError("Invalid evidence decision")
        if not isinstance(data.get("notes", ""), str):
            raise ValueError("Evidence notes must be text")
        reason = data.get("rejection_reason", before.get("rejection_reason", "") if decision == "rejected" else "")
        if not isinstance(reason, str) or reason not in REJECTION_REASONS: raise ValueError("Invalid rejection reason")
        suggestion = dict(before, decision=decision, rejection_reason=reason if decision == "rejected" else "")
        if "notes" in data: suggestion["notes"] = data["notes"]
        if suggestion.get("source_type") == "pdf_metadata":
            if decision == "accepted":
                raise ValueError("PDF metadata is a discovery aid. Select the visible byline as page evidence before approving authors.")
            if outcome_id is None:
                self.store.put("suggestions", suggestion)
                self._save_feedback(review_id, paper_id, suggestion)
                self._history(review_id, "suggestion_decided", before, suggestion, paper_id, suggestion["item_id"])
                return suggestion
        if outcome_id is None:
            self.store.put("suggestions", suggestion)
        evidence = next((row for row in self.get_evidence(review_id, paper_id, suggestion["item_id"], outcome_id) if row.get("suggestion_id") == suggestion_id), None)
        previous = deepcopy(evidence)
        if evidence is None:
            evidence = {key: deepcopy(suggestion.get(key)) for key in ("review_id", "paper_id", "item_id", "page_number", "start", "end", "quote", "boxes", "ocr")}
            evidence.update(id=uid(), suggestion_id=suggestion_id, created_at=now(), notes="", manual=False,
                            outcome_id=outcome_id, source_type=suggestion.get("source_type", "pdf_text"))
        for key in ("chunk_id", "section", "table_id", "table_title", "table_row", "table_column", "row_header", "column_header", "source_location", "ocr_block_label", "extracted_value", "scores", "proposed_value"):
            if key in suggestion: evidence[key] = deepcopy(suggestion[key])
        review = self._review(review_id)
        evidence.setdefault("document_version", suggestion.get("document_version", 0))
        evidence.setdefault("source_stale", False)
        evidence.update(decision=decision, rejection_reason=suggestion["rejection_reason"], notes=data.get("notes", evidence["notes"]), reviewer=review["reviewer"], updated_at=now(),
                        schema_version=review["schema_version"], rule_version=review["rule_version"])
        if decision == "accepted":
            document_version = self._paper(review_id, paper_id).get("document_version", 0)
            if suggestion.get("document_version", 0) != document_version:
                raise ValueError("Refresh suggestions before accepting evidence from a reprocessed document")
            for key in ("page_number", "start", "end", "quote", "boxes"):
                evidence[key] = deepcopy(suggestion[key])
            evidence.update(document_version=document_version, source_stale=False)
            self._validate_evidence_source(review_id, paper_id, evidence)
        self.store.put("evidence", evidence)
        self._save_feedback(review_id, paper_id, suggestion)
        self._reconsider_evidence_dependents(review_id, paper_id, evidence, previous)
        self._history(review_id, "suggestion_decided", before, suggestion, paper_id, suggestion["item_id"])
        self._history(review_id, "evidence_decided", previous, evidence, paper_id, suggestion["item_id"])
        return dict(suggestion, evidence_id=evidence["id"])

    def _save_feedback(self, review_id, paper_id, source, *, evidence=False, item_snapshot=None, epoch=None):
        # Row-specific relevance is not a global paper/item retrieval label.
        if source.get("outcome_id"):
            return None
        item = item_snapshot or self._item(review_id, source["item_id"])
        config = config_for(self._review(review_id))
        identity = example_id(source, evidence)
        reason = source.get("rejection_reason", "") if source["decision"] == "rejected" else ""
        state = dict(decision=source["decision"], rejection_reason=reason,
                     item_signature=item_signature(item), item_meaning_version=item.get("learning_version", 0),
                     epoch=config["epoch"] if epoch is None else epoch)
        prior = latest_events(self.store.list("feedback", review_id=review_id, item_id=item["id"])).get(identity)
        if prior and all(prior.get(k) == v for k,v in state.items()) and prior.get("candidate_text") == source["quote"] and prior.get("candidate", {}).get("document_version", 0) == source.get("document_version", 0):
            return prior
        config = self._learning_changed(review_id)
        return self.store.put("feedback", dict(id=uid(), review_id=review_id, paper_id=paper_id,
            document_id=paper_id, item_id=item["id"], item=deepcopy(item),
            suggestion_id=source.get("suggestion_id") if evidence else source["id"],
            evidence_id=source["id"] if evidence else None, example_id=identity, sequence=config["revision"],
            candidate_text=source["quote"], accepted=source["decision"] == "accepted", **state,
            rank=source.get("rank"), scores=deepcopy(source.get("scores", {})),
            candidate=deepcopy(source), engine_version=ENGINE_VERSION, created_at=now()))

    def get_evidence(self, review_id, paper_id, item_id, outcome_id=None):
        self._field_scope(review_id, paper_id, item_id, outcome_id)
        return sorted((row for row in self.store.list("evidence", review_id=review_id, paper_id=paper_id, item_id=item_id)
                       if row.get("outcome_id") == outcome_id), key=lambda row: row["created_at"])

    @transaction
    def evidence_with_locations(self, review_id, paper_id, item_id, outcome_id=None):
        records = self.get_evidence(review_id, paper_id, item_id, outcome_id)
        if not records:
            return []
        pages = {page['page_number']: page for page in self.store.export_pages(review_id, paper_id)}
        return [dict(record, verification_location=(
            'Source changed; select its current location again.' if record.get('source_stale') else
            source_location(record, pages.get(record.get('page_number'), {})))) for record in records]

    def _selection_source(self, review_id, paper_id, item_id, data):
        self._paper(review_id, paper_id)
        self._item(review_id, item_id)
        page_number = data.get("page_number")
        if isinstance(page_number, bool) or not isinstance(page_number, int):
            raise ValueError("Evidence page number must be a physical page integer")
        page = next((page for page in self.get_pages(review_id, paper_id) if page["page_number"] == page_number), None)
        if page is None:
            raise ValueError("Evidence must identify an existing physical PDF page")
        quote = data.get("quote", "")
        if not isinstance(quote, str) or not quote.strip():
            raise ValueError("Evidence needs an exact source quotation")
        start, end = data.get("start"), data.get("end")
        manual = data.get("manual", False)
        if not isinstance(manual, bool) or not isinstance(data.get("ocr", False), bool):
            raise ValueError("Manual transcription and OCR flags must be boolean")
        rationale = data.get("rationale", "")
        if not isinstance(rationale, str) or not isinstance(data.get("notes", ""), str):
            raise ValueError("Evidence rationale and notes must be text")
        source_location = data.get("source_location", "")
        if not isinstance(source_location, str) or len(source_location) > 2000:
            raise ValueError("Source location must be text of at most 2000 characters")
        if manual and page["status"] == "ok":
            if not rationale.strip() or not source_location.strip():
                raise ValueError("Image transcription needs a rationale and a precise source location, such as Figure 1, panel B")
            start = end = None
            boxes = []
        elif page["status"] == "ok":
            if start is None and end is None:
                start = page["text"].find(quote)
                if start >= 0 and page["text"].find(quote, start + 1) >= 0:
                    raise ValueError("This quotation occurs more than once; select its exact source offsets")
                end = start + len(quote)
            if isinstance(start, bool) or isinstance(end, bool) or not isinstance(start, int) or not isinstance(end, int) or start < 0 or end <= start or end > len(page["text"]) or page["text"][start:end] != quote:
                raise ValueError("Quotation must exactly match the selected page text and offsets")
            boxes = boxes_for_span(page, start, end)
        else:
            if not manual or not isinstance(rationale, str) or not rationale.strip():
                raise ValueError("Unsearchable-page transcription must be labeled manual and include a rationale")
            start = end = None
            boxes = []
        return dict(page_number=page_number, start=start, end=end, quote=quote, boxes=boxes,
                    ocr=bool(page.get("ocr")) or data.get("ocr", False), manual=manual, rationale=rationale,
                    source_location=source_location.strip())

    @transaction
    def add_candidate(self, review_id, paper_id, item_id, data, outcome_id=None):
        self._field_scope(review_id, paper_id, item_id, outcome_id)
        paper = self._paper(review_id, paper_id)
        version = data.get("document_version")
        if isinstance(version, bool) or not isinstance(version, int) or version != paper.get("document_version", 0):
            raise ValueError("The document changed; select text from the current version")
        source = self._selection_source(review_id, paper_id, item_id, data)
        if source["start"] is None or source["manual"]:
            raise ValueError("Candidates require searchable source text; use manual evidence for transcription")
        decision = data.get("decision", "pending")
        if not isinstance(decision, str) or decision not in {"pending", "accepted"}: raise ValueError("Create a pending or accepted candidate")
        existing = self.store.list("suggestions", review_id=review_id, paper_id=paper_id, item_id=item_id)
        same = next((s for s in existing if not s.get("table_id") and s.get("source_type", "pdf_text") == "pdf_text"
                     and s.get("document_version", 0) == version
                     and all(s.get(k) == source[k] for k in ("page_number", "start", "end", "quote"))), None)
        review = self._review(review_id)
        candidate = dict(same or {}, **source)
        candidate.update(id=same["id"] if same else uid(), review_id=review_id, paper_id=paper_id, item_id=item_id,
            origin="user", user_selected=True, source_type="pdf_text", document_version=version,
            rule_version=review["rule_version"], active=True, decision=same["decision"] if same else "pending",
            created_at=same["created_at"] if same else now(), notes=data.get("notes", (same or {}).get("notes", "")))
        if outcome_id is not None:
            candidate["notes"] = (same or {}).get("notes", "")
        candidate.setdefault("proposed_value", None)
        candidate.setdefault("retrieval_methods", ["user_selection"])
        candidate["overlapping_candidate_ids"] = [s["id"] for s in existing if s["id"] != candidate["id"]
            and s.get("page_number") == source["page_number"] and s.get("start") is not None
            and min(s["end"], source["end"]) > max(s["start"], source["start"])]
        self.store.put("suggestions", candidate)
        if same != candidate: self._history(review_id, "user_candidate_created", same, candidate, paper_id, item_id)
        if decision == "accepted":
            return self.decide_suggestion(review_id, paper_id, candidate["id"], {"decision": "accepted", "notes":data.get("notes", candidate["notes"])}, outcome_id)
        if not same and outcome_id is None: self._save_feedback(review_id, paper_id, candidate)
        if outcome_id is not None:
            return next(row for row in self.get_suggestions(review_id, paper_id, item_id, outcome_id) if row["id"] == candidate["id"])
        return candidate

    @transaction
    def add_evidence(self, review_id, paper_id, item_id, data, outcome_id=None):
        self._field_scope(review_id, paper_id, item_id, outcome_id)
        source = self._selection_source(review_id, paper_id, item_id, data)
        decision = data.get("decision", "accepted")
        if not isinstance(decision, str) or decision not in DECISIONS:
            raise ValueError("Invalid evidence decision")
        reason = data.get("rejection_reason", "") if decision == "rejected" else ""
        if not isinstance(reason, str) or reason not in REJECTION_REASONS: raise ValueError("Invalid rejection reason")
        review = self._review(review_id)
        evidence = {"id": uid(), "review_id": review_id, "paper_id": paper_id, "item_id": item_id, "suggestion_id": None,
                    "outcome_id": outcome_id,
                    **source, "origin": "user", "decision": decision, "rejection_reason": reason, "notes": data.get("notes", ""),
                    "reviewer": review["reviewer"], "created_at": now(), "updated_at": now(),
                    "schema_version": review["schema_version"], "rule_version": review["rule_version"],
                    "document_version": self._paper(review_id, paper_id).get("document_version", 0), "source_stale": False}
        self.store.put("evidence", evidence)
        self._save_feedback(review_id, paper_id, evidence, evidence=True)
        self._history(review_id, "evidence_added", after=evidence, paper_id=paper_id, item_id=item_id)
        return evidence

    def _reconsider_evidence_dependents(self, review_id, paper_id, evidence, before):
        if before is None or all(before.get(key) == evidence.get(key) for key in ("decision", "ocr", "manual", "rationale")):
            return
        for table, field in self._field_records(review_id=review_id, paper_id=paper_id, item_id=evidence["item_id"]):
            used = set(field["evidence_ids"])
            for observation in field["observations"]:
                used.update(observation.get("evidence_ids", []))
            if evidence["id"] in used and field["status"] != "unreviewed":
                prior = deepcopy(field)
                field["needs_reconsideration"] = True
                self.store.put(table, field)
                self._history(review_id, "evidence_changed_reconsideration", prior, field, paper_id, field["item_id"])

    @transaction
    def update_evidence(self, review_id, paper_id, evidence_id, data, outcome_id=None):
        before = self._scoped_record("evidence", review_id, paper_id, evidence_id)
        if before.get("outcome_id") != outcome_id:
            raise KeyError(evidence_id)
        self._field_scope(review_id, paper_id, before["item_id"], outcome_id)
        evidence = deepcopy(before)
        for key in ("decision", "notes", "ocr", "rejection_reason", "source_location"):
            if key in data:
                evidence[key] = data[key]
        if not isinstance(evidence["decision"], str) or evidence["decision"] not in DECISIONS:
            raise ValueError("Invalid evidence decision")
        if not isinstance(evidence["notes"], str) or not isinstance(evidence["ocr"], bool):
            raise ValueError("Evidence notes must be text and OCR flag must be boolean")
        if not isinstance(evidence.get("source_location", ""), str) or len(evidence.get("source_location", "")) > 2000:
            raise ValueError("Source location must be text of at most 2000 characters")
        reason = evidence.get("rejection_reason", "") if evidence["decision"] == "rejected" else ""
        if not isinstance(reason, str) or reason not in REJECTION_REASONS: raise ValueError("Invalid rejection reason")
        evidence["rejection_reason"] = reason
        if evidence["decision"] == "accepted":
            self._validate_evidence_source(review_id, paper_id, evidence)
        review = self._review(review_id)
        evidence.update(reviewer=review["reviewer"], updated_at=now(), schema_version=review["schema_version"], rule_version=review["rule_version"])
        self.store.put("evidence", evidence)
        if evidence.get("suggestion_id") and outcome_id is None:
            suggestion = self._scoped_record("suggestions", review_id, paper_id, evidence["suggestion_id"])
            suggestion["decision"] = evidence["decision"]
            suggestion["rejection_reason"] = reason
            if "source_location" in data:
                suggestion["source_location"] = evidence["source_location"]
            self.store.put("suggestions", suggestion)
            if set(data) & {"decision", "rejection_reason"}:
                self._save_feedback(review_id, paper_id, suggestion)
        elif set(data) & {"decision", "rejection_reason"}:
            self._save_feedback(review_id, paper_id, evidence, evidence=True)
        self._reconsider_evidence_dependents(review_id, paper_id, evidence, before)
        self._history(review_id, "evidence_updated", before, evidence, paper_id, evidence["item_id"])
        return evidence

    def _ensure_field(self, review_id, paper_id, item_id, outcome_id=None):
        table = "outcome_fields" if outcome_id is not None else "fields"
        identity = f"{paper_id}:{outcome_id}:{item_id}" if outcome_id is not None else f"{paper_id}:{item_id}"
        try:
            return self.store.get(table, identity)
        except KeyError:
            review = self._review(review_id)
            field = {"review_id": review_id, "paper_id": paper_id, "item_id": item_id, "status": "unreviewed", "value": None,
                     "value_kind": "reported", "rationale": "", "calculation_inputs": [], "notes": "", "evidence_ids": [],
                     "observations": [], "needs_reconsideration": False, "reviewer": "", "updated_at": now(),
                     "schema_version": review["schema_version"], "rule_version": review["rule_version"]}
            if outcome_id is not None:
                field["outcome_id"] = outcome_id
            return self.store.put(table, field)

    @transaction
    def get_field(self, review_id, paper_id, item_id, outcome_id=None):
        self._field_scope(review_id, paper_id, item_id, outcome_id)
        return self._ensure_field(review_id, paper_id, item_id, outcome_id)

    def _typed_value(self, value, item):
        kind = item["data_type"]
        if value is None or (isinstance(value, str) and not value.strip()):
            raise ValueError("An approved field needs a value")
        if kind in {"text", "narrative", "date"} and not isinstance(value, str):
            raise ValueError(f"{kind} values must be text")
        if kind in {"number", "integer"}:
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError("Numeric values must be finite numbers")
            if kind == "integer" and not isinstance(value, int):
                raise ValueError("An integer value is required")
        if kind == "boolean" and not isinstance(value, bool):
            raise ValueError("Boolean values must be true or false")
        if kind == "date":
            try:
                if date.fromisoformat(value).isoformat() != value:
                    raise ValueError()
            except ValueError as exc:
                raise ValueError("Dates must use YYYY-MM-DD") from exc
        allowed = item.get("allowed_values", [])
        if allowed and not any(type(value) is type(option) and value == option for option in allowed):
            raise ValueError("Value is not in the item's allowed values")
        if item.get("section", "study") == "meta_analysis" and isinstance(value, (int, float)) and not isinstance(value, bool):
            heading = re.sub(r"[^a-z]", "", (item.get("export_heading") or item.get("name", "")).lower())
            if heading == "n" and (value <= 0 or int(value) != value):
                raise ValueError("Outcome sample size must be a positive whole number.")
            if heading in {"presd", "postsd"} and value < 0:
                raise ValueError("Standard deviations cannot be negative.")
            if heading == "r" and not -1 <= value <= 1:
                raise ValueError("The pre/post correlation must be between -1 and 1.")

    def _validate_evidence_source(self, review_id, paper_id, evidence):
        if evidence.get("source_stale"):
            raise ValueError("Supporting evidence no longer matches the current text or page geometry; select its source again")
        page = next((page for page in self.get_pages(review_id, paper_id) if page["page_number"] == evidence["page_number"]), None)
        if page is None:
            raise ValueError("Supporting evidence refers to a page no longer present")
        if evidence.get("manual") and evidence.get("start") is None and page["status"] == "ok":
            if not evidence.get("rationale", "").strip() or not evidence.get("source_location", "").strip():
                raise ValueError("Image transcription needs a rationale and a precise source location")
            return
        if page["status"] != "ok":
            if not evidence.get("manual") or not evidence.get("rationale", "").strip():
                raise ValueError("Unsearchable-page evidence requires a manual transcription and rationale")
            return
        start, end = evidence.get("start"), evidence.get("end")
        if not isinstance(start, int) or not isinstance(end, int) or page["text"][start:end] != evidence["quote"]:
            raise ValueError("Supporting evidence no longer matches current extracted text; select its source again")

    @transaction
    def save_field(self, review_id, paper_id, item_id, data, outcome_id=None):
        item = self._field_scope(review_id, paper_id, item_id, outcome_id)
        before = self._ensure_field(review_id, paper_id, item_id, outcome_id)
        field = deepcopy(before)
        for key in ("status", "value", "value_kind", "rationale", "calculation_inputs", "notes", "evidence_ids", "observations"):
            if key in data:
                field[key] = deepcopy(data[key])
        if not isinstance(field["status"], str) or field["status"] not in FIELD_STATUSES:
            raise ValueError("Invalid field status")
        if not isinstance(field["value_kind"], str) or field["value_kind"] not in {"reported", "interpretation", "calculated"}:
            raise ValueError("Invalid value kind")
        if not isinstance(field["rationale"], str) or not isinstance(field["notes"], str):
            raise ValueError("Rationale and notes must be text")
        if not isinstance(field["calculation_inputs"], (list, dict)):
            raise ValueError("Calculation inputs must be a list or object")
        if not isinstance(field["evidence_ids"], list) or not isinstance(field["observations"], list):
            raise ValueError("Evidence and observations must be lists")
        if not all(isinstance(eid, str) and eid for eid in field["evidence_ids"]):
            raise ValueError("Evidence IDs must be nonempty strings")
        if isinstance(field["value"], (dict, list)) or (isinstance(field["value"], float) and not math.isfinite(field["value"])):
            raise ValueError("Final values must be finite scalar values")
        field["evidence_ids"] = list(dict.fromkeys(field["evidence_ids"]))
        evidence_by_id = {row["id"]: row for row in self.get_evidence(review_id, paper_id, item_id, outcome_id)}
        used_ids = set(field["evidence_ids"])
        observation_ids = set()
        for observation in field["observations"]:
            if not isinstance(observation, dict):
                raise ValueError("Observations must be objects")
            observation.setdefault("id", uid())
            if not isinstance(observation["id"], str) or not observation["id"]:
                raise ValueError("Observation IDs must be nonempty strings")
            if observation["id"] in observation_ids:
                raise ValueError("Observation IDs must be unique")
            observation_ids.add(observation["id"])
            for key in ("group", "population", "outcome", "time_point", "units", "statistic"):
                observation.setdefault(key, "")
                if not isinstance(observation[key], str):
                    raise ValueError("Observation labels must be text")
            if not any(observation[key].strip() for key in ("group", "population", "outcome", "time_point")):
                raise ValueError("Each observation needs a group, population, outcome, or time-point label")
            observation.setdefault("evidence_ids", [])
            if not isinstance(observation["evidence_ids"], list):
                raise ValueError("Observation evidence must be a list")
            if not all(isinstance(eid, str) and eid for eid in observation["evidence_ids"]):
                raise ValueError("Observation evidence IDs must be nonempty strings")
            used_ids.update(observation["evidence_ids"])
            value = observation.get("value")
            if isinstance(value, (dict, list)) or (isinstance(value, float) and not math.isfinite(value)):
                raise ValueError("Observation values must be finite scalar values")
        if not used_ids.issubset(evidence_by_id):
            raise ValueError("Every evidence reference must belong to this paper, extraction item, and outcome row")
        if field["status"] == "approved":
            self._typed_value(field["value"], item)
            if not field["evidence_ids"] or any(evidence_by_id[eid]["decision"] != "accepted" for eid in used_ids):
                raise ValueError("Approve relevant supporting evidence before approving a value")
            for eid in used_ids:
                self._validate_evidence_source(review_id, paper_id, evidence_by_id[eid])
            if field["value_kind"] in {"interpretation", "calculated"} and not field["rationale"].strip():
                raise ValueError("Interpretations and calculations require a rationale")
            if field["value_kind"] == "calculated" and not field["calculation_inputs"]:
                raise ValueError("Calculated values require calculation inputs")
            for observation in field["observations"]:
                if observation.get("value") is None:
                    raise ValueError("Approved observations require a value")
                if not observation["evidence_ids"]:
                    raise ValueError("Each approved observation requires its own supporting evidence")
        explained = field["rationale"].strip() or field["notes"].strip()
        if field["status"] in {"unclear", "conflicting"} and isinstance(field["value"], str):
            explained = explained or field["value"].strip()
        if field["status"] in {"not_reported", "not_applicable", "unclear", "conflicting"} and not explained:
            raise ValueError("Record a rationale or note for this review status")
        if field["status"] in {"not_reported", "not_applicable"}:
            field["value"] = None
        review = self._review(review_id)
        field.update(needs_reconsideration=bool(before["needs_reconsideration"] and "status" not in data), reviewer=review["reviewer"], updated_at=now(),
                     schema_version=review["schema_version"], rule_version=review["rule_version"])
        self.store.put("outcome_fields" if outcome_id is not None else "fields", field)
        self._history(review_id, "field_saved", before, field, paper_id, item_id)
        return field

    @transaction
    def export_snapshot(self, review_id):
        """A coherent export view; retrieval candidates/geometry are not needed."""
        papers = self.list_papers(review_id)
        active_ids = {paper['id'] for paper in papers}
        return {'review': self._review(review_id), 'items': self.list_items(review_id, True),
                'papers': papers,
                'pages': [page for page in self.store.export_pages(review_id) if page['paper_id'] in active_ids],
                **{table: [row for row in self.store.list(table, review_id=review_id) if row['paper_id'] in active_ids]
                   for table in ('evidence', 'fields', 'outcomes', 'outcome_fields')}}

    def history(self, review_id):
        self._review(review_id)
        return self.store.list('history', review_id=review_id)

    @transaction
    def snapshot(self, review_id):
        papers = self.list_papers(review_id)
        active_ids = {paper["id"] for paper in papers}
        return {"review": self._review(review_id), "items": self.list_items(review_id, True), "papers": papers,
                "learning_examples": self.get_learning(review_id)["examples"],
                "history": self.store.list("history", review_id=review_id),
                **{table: [row for row in self.store.list(table, review_id=review_id) if row["paper_id"] in active_ids]
                   for table in ("pages", "suggestions", "evidence", "fields", "feedback", "outcomes", "outcome_fields")}}
