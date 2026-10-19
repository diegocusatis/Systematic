"""Editable automatic vocabulary, including upgrades of existing reviews."""
from io import BytesIO

from fastapi.testclient import TestClient
from openpyxl import Workbook

from review_extractor.app import create_app
from review_extractor.keywords import suggest_keywords
from review_extractor.service import ReviewService
from review_extractor.workbooks import preview_template


def test_related_terms_boundaries_and_unknown_heading():
    assert {"mean age", "median age", "years old"} <= set(suggest_keywords({"name": "Average participant age"}))
    assert "randomised" in suggest_keywords({"name": "Trial design"})
    assert "hertz" in suggest_keywords({"name": "Frequency"})
    assert "age" not in suggest_keywords({"name": "Dosage"})
    assert suggest_keywords({"name": "Novel measurement / regional score"}) == ["Novel measurement", "regional score"]
    assert "mean age" in suggest_keywords({"name": "Demographics", "definition": "The participants' age"})
    terms = suggest_keywords({"name": "Mean age", "export_heading": "Mean age"})
    assert len(terms) == len({term.casefold() for term in terms})


def test_api_defaults_explicit_removal_and_restart(tmp_path):
    with TestClient(create_app(tmp_path, run_background=False)) as client:
        r = client.post("/api/reviews", json={"name": "Keywords"}).json()["id"]
        path = f"/api/reviews/{r}/items"
        item = client.post(path, json={"name": "Mean age"}).json()
        assert "median age" in item["keywords"]
        empty = client.post(path, json={"name": "Mean age", "keywords": []}).json()
        assert empty["keywords"] == []
        client.patch(f"{path}/{item['id']}", json={"keywords": ["my own phrase"]})
        response = client.post("/api/keyword-suggestions", json={"name": "Mean age"})
        assert "median age" in response.json()["keywords"]
        assert client.post("/api/keyword-suggestions", json={"definition": []}).status_code == 422
    with TestClient(create_app(tmp_path, run_background=False)) as client:
        items = {item["id"]: item for item in client.get(path).json()}
        assert items[item["id"]]["keywords"] == ["my own phrase"]
        assert items[empty["id"]]["keywords"] == []
        client.patch(f"{path}/{item['id']}", json={"keywords": []})
    with TestClient(create_app(tmp_path, run_background=False)) as client:
        assert all(item["keywords"] == [] for item in client.get(path).json())


def test_legacy_initialization_is_scoped_once_and_preserves_prior_edits(tmp_path):
    service = ReviewService(tmp_path)
    r = service.create_review({"name": "Legacy"})["id"]
    other = service.create_review({"name": "Other"})["id"]
    untouched = service.add_item(r, {"name": "Mean age", "keywords": []})
    cleared = service.add_item(r, {"name": "Frequency", "keywords": ["custom"]})
    service.update_item(r, cleared["id"], {"keywords": []})
    custom = service.add_item(r, {"name": "Mean age", "keywords": ["my custom term"]})
    elsewhere = service.add_item(other, {"name": "Mean age", "keywords": []})
    for record in (untouched, cleared, custom, elsewhere):
        record = service.store.get("items", record["id"])
        record.pop("keywords_initialized")
        service.store.put("items", record)
    paper = service.add_paper(r, {"title": "Already reviewed"})
    fields = service.store.list("fields", review_id=r)
    service.initialize_keywords(r)
    items = {item["id"]: item for item in service.list_items(r)}
    assert "mean age" in items[untouched["id"]]["keywords"]
    assert items[cleared["id"]]["keywords"] == []
    assert items[custom["id"]]["keywords"] == ["my custom term"]
    assert service.list_items(other)[0]["keywords"] == []
    assert service.store.list("fields", review_id=r) == fields
    assert service.get_paper(r, paper["id"])["search_status"] == "stale"
    history = service.store.list("history", review_id=r)
    service.initialize_keywords(r)
    assert service.store.list("history", review_id=r) == history
    service.update_item(r, untouched["id"], {"keywords": []})
    service.initialize_keywords(r)
    assert service.store.get("items", untouched["id"])["keywords"] == []
    service.store.close()


def test_template_starters_ignore_example_rows_and_preserve_dictionary_choices():
    book = Workbook()
    book.active.append(["Mean age", "Frequency"])
    book.active.append(["NOT EVIDENCE", "NOT A KEYWORD"])
    stream = BytesIO()
    book.save(stream)
    preview = preview_template(stream.getvalue())
    assert "median age" in preview["items"][0]["keywords"]
    assert "hertz" in preview["items"][1]["keywords"]
    assert all("NOT" not in term for item in preview["items"] for term in item["keywords"])
    book = Workbook()
    book.active.append(["name", "keywords"])
    book.active.append(["Mean age", "my custom phrase"])
    book.active.append(["Frequency", "[]"])
    book.active.append(["Study design", None])
    stream = BytesIO()
    book.save(stream)
    items = preview_template(stream.getvalue(), mode="dictionary")["items"]
    assert items[0]["keywords"] == ["my custom phrase"]
    assert items[1]["keywords"] == []
    assert "randomised" in items[2]["keywords"]
