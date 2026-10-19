"""Default launches must reopen established checkout data from any directory."""
from review_extractor import app


def test_existing_checkout_database_is_reused_from_another_directory(tmp_path, monkeypatch):
    checkout = tmp_path / 'project'
    data = checkout / '.review-data'
    data.mkdir(parents=True)
    (checkout / 'pyproject.toml').write_text('[project]\nname="review-extractor"\n')
    (data / 'reviews.sqlite3').touch()
    elsewhere = tmp_path / 'elsewhere'
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    monkeypatch.setattr(app, 'CHECKOUT_ROOT', checkout)
    monkeypatch.delenv('REVIEW_EXTRACTOR_DATA_DIR', raising=False)
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path / 'appdata'))
    assert app.default_data_dir() == data
    monkeypatch.setenv('REVIEW_EXTRACTOR_DATA_DIR', str(tmp_path / 'explicit'))
    assert app.default_data_dir() == tmp_path / 'explicit'


def test_new_install_uses_appdata_without_creating_checkout_data(tmp_path, monkeypatch):
    monkeypatch.setattr(app, 'CHECKOUT_ROOT', tmp_path / 'installed')
    monkeypatch.delenv('REVIEW_EXTRACTOR_DATA_DIR', raising=False)
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path / 'appdata'))
    assert app.default_data_dir() == tmp_path / 'appdata' / 'ReviewExtractor'
    assert not (tmp_path / 'installed' / '.review-data').exists()
