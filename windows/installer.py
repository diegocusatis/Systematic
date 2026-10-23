"""Standard-library installer. Never imports or modifies a source installation.

The native bootstrap checks Python before invoking this module. Data preparation
is copy-on-write, and activation has a durable recovery journal. Tests can supply
isolated home/data directories without touching a user's installation.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import ctypes
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import sqlite3
import subprocess
import sys
import time
import tomllib
import urllib.parse
import urllib.request
from uuid import uuid4

TABLES = ("reviews", "items", "papers", "pages", "suggestions", "evidence",
          "fields", "outcomes", "outcome_fields", "history", "feedback")
DB = "reviews.sqlite3"
# Folder names searched for a previous Systematic copy (case-insensitive). Detection
# stays bounded: only these names are entered below each search root.
DATA_FOLDERS = {".review-data", "review-data"}
PROJECT_PREFIXES = ("systematic", "review-extractor", "review_extractor", "review extractor")
CONTAINER_FOLDERS = {"github", "source", "repos", "projects", "research"}


def say(message):
    print(message, flush=True)


def progress(percent, message):
    """Completed phase milestones, not a timer or an estimated download percent."""
    say("SYSTEMATIC_PROGRESS=" + json.dumps({"percent": percent, "message": message}))


def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".new")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, ensure_ascii=False)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def read_json(path, default=None):
    return json.loads(Path(path).read_text(encoding="utf-8-sig")) if Path(path).exists() else default


def inside(path, root):
    return Path(path).resolve().is_relative_to(Path(root).resolve())


def remove_owned(path, parent):
    path, parent = Path(path).resolve(), Path(parent).resolve()
    if path == parent or not inside(path, parent):
        raise ValueError("Refusing to remove a folder outside the installation workspace.")
    if path.exists():
        shutil.rmtree(path)


@contextmanager
def installation_lock(home):
    """Shared by setup and the desktop server, released even after a crash."""
    home = Path(home)
    home.mkdir(parents=True, exist_ok=True)
    stream = (home / "session.lock").open("a+b")
    if os.fstat(stream.fileno()).st_size == 0:
        stream.write(b"\0")
        stream.flush()
    stream.seek(0)
    try:
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        stream.close()
        raise RuntimeError("Systematic is running or another setup is open. Close it and try again.") from None
    try:
        yield
    finally:
        stream.close()


def require_closed(folder):
    """Also catches legacy launches that do not participate in session.lock."""
    path = Path(folder) / DB
    if not path.exists() or os.name != "nt":
        return
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                  ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    handle = kernel.CreateFileW(str(path), 0x80000000, 0, None, 3, 0, None)
    if handle == wintypes.HANDLE(-1).value:
        raise RuntimeError(f"Close Systematic and any database viewer before continuing:\n{folder}")
    kernel.CloseHandle(handle)


@contextmanager
def connect_readonly(path):
    connection = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)
    try:
        yield connection
    finally:
        connection.close()


@contextmanager
def connect_write(path):
    connection = sqlite3.connect(path)
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def db_check(path):
    with connect_readonly(path) as db:
        if db.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
            raise ValueError("Database integrity check failed.")
        if db.execute("PRAGMA foreign_key_check").fetchone():
            raise ValueError("A saved review has broken database links.")
        actual = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {"reviews", "papers", "items"}.issubset(actual):
            raise ValueError("This is not a supported Systematic review database.")
        if actual - set(TABLES) - {"sqlite_sequence"}:
            raise ValueError("This review database has tables from a newer or unknown version. Use its matching installer.")
        for table in actual & set(TABLES):
            for (data,) in db.execute(f'SELECT data FROM "{table}"'):
                if not isinstance(json.loads(data), dict):
                    raise ValueError("Invalid saved review record.")


def snapshot(source, destination):
    """Copy all assets, using SQLite's backup API to include committed WAL data."""
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if source == destination or inside(destination, source) or inside(source, destination):
        raise ValueError("The backup and source folders must be separate.")
    require_closed(source)
    db_check(source / DB)
    destination.mkdir(parents=True, exist_ok=False)
    for path in source.rglob("*"):
        if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
            raise ValueError("Review data contains a linked folder/file. Choose a normal local copy.")
        if not path.is_file() or path.name in {DB, DB + "-wal", DB + "-shm", DB + "-journal"}:
            continue
        target = destination / path.relative_to(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        if digest(path) != digest(target):
            raise ValueError(f"File changed while copying: {path.name}. Close Systematic and retry.")
    with connect_readonly(source / DB) as src, connect_write(destination / DB) as dst:
        src.backup(dst)
    db_check(destination / DB)
    require_closed(source)


def locate_data(selected):
    selected = Path(selected).expanduser().resolve()
    matches = [p for p in (selected, selected / ".review-data", selected / "review-data")
               if (p / DB).is_file()]
    if len(matches) != 1:
        raise ValueError("Choose the old folder containing reviews.sqlite3, or its Systematic project folder.")
    return matches[0]


def imported_key(source):
    """Identity of an imported previous folder (matches prepare_data's archive name)."""
    return hashlib.sha256(str(Path(source).resolve()).encode()).hexdigest()[:16]


def last_used(folder):
    """Most recent write to the review database, including an unflushed WAL."""
    folder = Path(folder)
    times = [(folder / DB).stat().st_mtime]
    wal = folder / (DB + "-wal")
    if wal.is_file() and wal.stat().st_size > 0:
        times.append(wal.stat().st_mtime)
    return max(times)


def carryover_summary(database):
    """Human-readable counts of everything a transfer must preserve."""
    summary = dict(reviews=0, review_names=[], pdfs=0, template_columns=0, search_terms=0,
                   accepted_evidence=0, rejected_evidence=0, reviewed_values=0,
                   approved_values=0, outcome_rows=0, history_entries=0, learning_examples=0)
    with connect_readonly(database) as db:
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}

        def rows(table):
            return db.execute(f'SELECT data FROM "{table}"') if table in tables else []

        for (data,) in rows("reviews"):
            summary["reviews"] += 1
            summary["review_names"].append(json.loads(data).get("name", ""))
        for (data,) in rows("papers"):
            summary["pdfs"] += not json.loads(data).get("removed")
        for (data,) in rows("items"):
            item = json.loads(data)
            if not item.get("deleted"):
                summary["template_columns"] += 1
                summary["search_terms"] += len(item.get("keywords") or [])
        for (data,) in rows("evidence"):
            decision = json.loads(data).get("decision")
            if decision in ("accepted", "rejected"):
                summary[decision + "_evidence"] += 1
        for table in ("fields", "outcome_fields"):
            for (data,) in rows(table):
                status = json.loads(data).get("status")
                summary["reviewed_values"] += status not in (None, "unreviewed")
                summary["approved_values"] += status == "approved"
        for table, key in (("outcomes", "outcome_rows"), ("history", "history_entries"),
                           ("feedback", "learning_examples")):
            if table in tables:
                summary[key] = db.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
    return summary


def describe(summary):
    def n(count, word):
        return f"{count:,} {word}" + ("" if count == 1 else "s")
    return " · ".join([
        n(summary["reviews"], "review"), n(summary["pdfs"], "PDF"),
        n(summary["template_columns"], "template column") + f" ({summary['search_terms']:,} search terms)",
        f"{summary['accepted_evidence']:,} accepted / {summary['rejected_evidence']:,} rejected evidence",
        n(summary["reviewed_values"], "reviewed value") + f" ({summary['approved_values']:,} approved)",
        n(summary["history_entries"], "history entry").replace("entrys", "entries"),
        n(summary["learning_examples"], "learning example")])


def discover(roots, exclude=(), installed_data=None, max_depth=4):
    """Find previous Systematic data folders below known roots, newest first.

    Only folders named like a Systematic project/data folder (or a code
    container such as GitHub) are entered, links are never followed, and
    nothing is opened for writing.
    """
    excluded = [Path(p).resolve() for p in exclude if p]
    imported = Path(installed_data) / "imported-assets" if installed_data else None
    found, seen = [], set()

    def visit(folder, depth):
        try:
            folder = folder.resolve()
            if folder in seen or any(folder == e or inside(folder, e) for e in excluded):
                return
            seen.add(folder)
            if (folder / DB).is_file():
                found.append(folder)
                return
            if depth >= max_depth:
                return
            children = list(folder.iterdir())
        except OSError:
            return
        for child in children:
            name = child.name.lower()
            if not (name in DATA_FOLDERS or name.startswith(PROJECT_PREFIXES)
                    or name in CONTAINER_FOLDERS):
                continue
            try:
                # An unreadable or linked folder is skipped without ending the search.
                if child.is_symlink() or (hasattr(child, "is_junction") and child.is_junction()):
                    continue
                if not child.is_dir():
                    continue
            except OSError:
                continue
            visit(child, depth + 1)

    for root in roots:
        if root and Path(root).is_dir():
            visit(Path(root), 0)
    result = []
    for folder in found:
        try:
            summary = carryover_summary(folder / DB)
        except (sqlite3.Error, OSError, ValueError) as error:
            result.append(dict(path=str(folder), usable=False, error=str(error), last_used=0))
            continue
        result.append(dict(
            path=str(folder), usable=summary["reviews"] > 0, last_used=last_used(folder),
            last_used_text=datetime.fromtimestamp(last_used(folder)).strftime("%d %b %Y %H:%M"),
            already_imported=bool(imported and (imported / imported_key(folder)).is_dir()),
            summary=summary, description=describe(summary)))
    result.sort(key=lambda c: (not c["usable"], -c["last_used"]))
    return result


def verify_carryover(original, staged):
    """Every original record must exist unchanged in the staged copy.

    Only a paper's current file path may differ, because PDFs move into
    permanent review storage. Templates, search terms, evidence decisions,
    verified values, history and learning examples must match exactly.
    """
    with connect_readonly(original) as src, connect_readonly(staged) as dst:
        source_tables = {r[0] for r in src.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table in TABLES:
            if table not in source_tables:
                continue
            for row in src.execute(f'SELECT * FROM "{table}" ORDER BY id'):
                copy = dst.execute(f'SELECT * FROM "{table}" WHERE id=?', (row[0],)).fetchone()
                if copy is None:
                    raise ValueError(f"A saved {table} record was not carried over. Nothing has been replaced.")
                if copy == row:
                    continue  # Identical bytes; avoids decoding large candidate tables.
                before, after = json.loads(row[-1]), json.loads(copy[-1])
                if table == "papers":
                    before.pop("path", None)
                    after.pop("path", None)
                if copy[:-1] != row[:-1] or before != after:
                    raise ValueError(f"A saved {table} record changed during transfer. Nothing has been replaced.")


def paper_source(record, source):
    stored = Path(record["path"])
    candidates = [stored if stored.is_absolute() else source / stored]
    # A copied checkout may still contain absolute paths to another computer.
    parts = record["path"].replace("\\", "/").split("/")
    if "pdfs" in parts:
        candidates.append(source.joinpath(*parts[parts.index("pdfs"):]))
    candidates.extend([source / "pdfs" / record["review_id"] / stored.name])
    expected = record.get("sha256")
    for candidate in candidates:
        if candidate.is_file() and (not expected or digest(candidate) == expected):
            return candidate.resolve()
    raise ValueError(f"Missing or changed PDF: {record.get('filename', record['id'])}. "
                     "Restore this PDF in the old review folder and retry.")


def relocate_papers(database, original, stage, final):
    """Only current paper paths change. Quoted evidence and historical records do not."""
    with connect_write(database) as db:
        for rid, data in db.execute("SELECT id,data FROM papers").fetchall():
            record = json.loads(data)
            source = paper_source(record, original)
            # Stable, collision-safe file names also support PDFs outside the old data folder.
            sha = digest(source)
            relative = Path("pdfs") / record["review_id"] / (sha + ".pdf")
            if not inside(stage / relative, stage):
                raise ValueError("Invalid review file path.")
            output = stage / relative
            output.parent.mkdir(parents=True, exist_ok=True)
            if not output.exists():
                shutil.copy2(source, output)
            if digest(output) != sha:
                raise ValueError("PDF verification failed.")
            record["path"] = str((final / relative).resolve())
            db.execute("UPDATE papers SET data=? WHERE id=?", (json.dumps(record, ensure_ascii=False), rid))


def records(database):
    with connect_readonly(database) as db:
        actual = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        return {table: db.execute(f'SELECT * FROM "{table}" ORDER BY rowid').fetchall()
                for table in TABLES if table in actual}


def record_fingerprints(database):
    """Bound memory use even for large extracted-page/candidate tables."""
    result = {}
    with connect_readonly(database) as db:
        actual = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table in TABLES:
            if table not in actual:
                continue
            checksum, count = hashlib.sha256(), 0
            for row in db.execute(f'SELECT * FROM "{table}" ORDER BY id'):
                checksum.update(json.dumps(row, ensure_ascii=False).encode("utf-8"))
                checksum.update(b"\n")
                count += 1
            result[table] = {"count": count, "sha256": checksum.hexdigest()}
    return result


def data_fingerprint(folder):
    folder = Path(folder)
    if not folder.exists():
        return None
    checksum = hashlib.sha256()
    if (folder / DB).exists():
        checksum.update(json.dumps(record_fingerprints(folder / DB), sort_keys=True).encode())
    for path in sorted(folder.rglob("*")):
        if path.is_file() and path.name not in {DB, DB + "-wal", DB + "-shm", DB + "-journal"}:
            checksum.update(path.relative_to(folder).as_posix().encode())
            checksum.update(digest(path).encode())
    return checksum.hexdigest()


def review_fingerprint(db, review_id, tables):
    checksum = hashlib.sha256()
    for table in TABLES:
        if table not in tables:
            continue
        column = "id" if table == "reviews" else "review_id"
        for (data,) in db.execute(f'SELECT data FROM "{table}" WHERE {column}=? ORDER BY id', (review_id,)):
            checksum.update(table.encode())
            checksum.update(json.dumps(json.loads(data), sort_keys=True, ensure_ascii=False).encode())
    return checksum.hexdigest()


def merge_database(target, incoming):
    """Add complete, disjoint reviews. Duplicate rows must agree; never pick a winner."""
    with connect_write(target) as dst, connect_readonly(incoming) as src:
        dst.execute("PRAGMA foreign_keys=ON")
        existing_tables = {r[0] for r in dst.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        incoming_tables = {r[0] for r in src.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        overlapping = {r[0] for r in dst.execute("SELECT id FROM reviews")} & {
            r[0] for r in src.execute("SELECT id FROM reviews")}
        for review_id in overlapping:
            if review_fingerprint(dst, review_id, existing_tables) != review_fingerprint(src, review_id, incoming_tables):
                raise ValueError("The same review has different saved work in two locations. "
                                 "Setup stopped without choosing or overwriting either copy. "
                                 "Keep both folders and reconcile them before importing.")
        # Older databases may lack later tables. Copy their actual schema, not inferred fields.
        for table in TABLES:
            if table in incoming_tables and table not in existing_tables:
                sql = src.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()[0]
                dst.execute(sql)
        for table in TABLES:
            if table not in incoming_tables:
                continue
            left = [r[1] for r in dst.execute(f'PRAGMA table_info("{table}")')]
            right = [r[1] for r in src.execute(f'PRAGMA table_info("{table}")')]
            if left != right:
                raise ValueError("Database formats differ. A version-specific migration is required.")
            for row in src.execute(f'SELECT * FROM "{table}" ORDER BY rowid'):
                old = dst.execute(f'SELECT * FROM "{table}" WHERE id=?', (row[0],)).fetchone()
                if old is not None:
                    equivalent = old[:-1] == row[:-1] and json.loads(old[-1]) == json.loads(row[-1])
                    if not equivalent:
                        raise ValueError("The same review has different saved work in two locations. "
                                         "Setup stopped without choosing or overwriting either copy. "
                                         "Keep both folders and reconcile them before importing.")
                else:
                    placeholders = ",".join("?" for _ in row)
                    dst.execute(f'INSERT INTO "{table}" VALUES ({placeholders})', row)
    db_check(target)


def validate_pdfs(database, stage, final):
    count = 0
    with connect_readonly(database) as db:
        for (data,) in db.execute("SELECT data FROM papers"):
            paper = json.loads(data)
            path = Path(paper["path"])
            if not inside(path, final):
                raise ValueError("A PDF still points outside permanent review storage.")
            copied = stage / path.relative_to(final)
            if not copied.is_file() or (paper.get("sha256") and digest(copied) != paper["sha256"]):
                raise ValueError(f"PDF validation failed: {paper.get('filename', paper['id'])}")
            count += 1
    return count


def archive_files(paths, stage):
    for name in paths:
        source = Path(name).resolve()
        if not source.is_file():
            raise ValueError(f"An additional template/export file was not found: {source}")
        target = stage / "original-workbooks" / digest(source)[:16] / source.name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        if digest(source) != digest(target):
            raise ValueError("Workbook copy verification failed.")


CACHE = "retrieval-cache"


def merge_cache(source, target):
    """Bring precomputed search embeddings/scores along; they are only a cache.

    Missing files are copied. Existing SQLite cache files gain rows they do not
    have yet, so nothing already computed in the target is replaced.
    """
    source, target = Path(source), Path(target)
    if not source.is_dir():
        return 0
    added = 0
    for path in source.rglob("*"):
        if not path.is_file() or path.name.endswith(("-wal", "-shm", "-journal")):
            continue
        destination = target / path.relative_to(source)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists():
            shutil.copy2(path, destination)
            added += 1
            continue
        if path.suffix != ".sqlite3":
            continue
        try:
            with connect_write(destination) as db:
                db.execute("ATTACH DATABASE ? AS incoming", (str(path.resolve()),))
                tables = [r[0] for r in db.execute(
                    "SELECT name FROM incoming.sqlite_master WHERE type='table'")]
                for table in tables:
                    if not db.execute("SELECT 1 FROM main.sqlite_master WHERE type='table' AND name=?",
                                      (table,)).fetchone():
                        sql = db.execute("SELECT sql FROM incoming.sqlite_master WHERE name=?", (table,)).fetchone()[0]
                        db.execute(sql)
                    db.execute(f'INSERT OR IGNORE INTO main."{table}" SELECT * FROM incoming."{table}"')
            added += 1
        except sqlite3.Error as error:
            say(f"Search cache {path.name} was not merged ({error}); it will be rebuilt when needed.")
    return added


def prepare_data(current, source, work, final, extra_files=()):
    """Produce a complete staged data folder without mutating either input."""
    current, work, final = Path(current), Path(work), Path(final).resolve()
    stage = work / "prepared-data"
    report = {"reviews": 0, "pdfs": 0, "tables": {}, "source": str(source) if source else None}
    if (current / DB).exists():
        snapshot(current, stage)
        relocate_papers(stage / DB, current, stage, final)
    else:
        if current.exists() and any(current.iterdir()):
            raise ValueError("The destination has files but no review database. Move or inspect it before setup.")
        stage.mkdir(parents=True)
    if source is not None and Path(source).resolve() != current.resolve():
        incoming = work / "source-snapshot"
        snapshot(source, incoming)
        # Keep a complete, untouched source backup, plus copy all non-database assets.
        source_key = imported_key(source)
        archived = stage / "imported-assets" / source_key
        if archived.exists():
            raise ValueError("This source was already imported. For an update, leave the previous-folder field blank.")
        # The search cache is moved into live storage below, not archived twice.
        shutil.copytree(incoming, archived, ignore=shutil.ignore_patterns(CACHE))
        (archived / DB).unlink()
        merge_cache(incoming / CACHE, stage / CACHE)
        incoming_database = work / "relocated-import.sqlite3"
        shutil.copy2(incoming / DB, incoming_database)
        relocate_papers(incoming_database, Path(source), stage, final)
        if (stage / DB).exists():
            merge_database(stage / DB, incoming_database)
        else:
            shutil.copy2(incoming_database, stage / DB)
    # Repair earlier installs that archived an imported cache instead of using it.
    for old_cache in sorted((stage / "imported-assets").glob("*/" + CACHE)):
        merge_cache(old_cache, stage / CACHE)
    archive_files(extra_files, stage)
    if (stage / DB).exists():
        db_check(stage / DB)
        # Independent record-by-record proof that nothing was lost or altered.
        if (current / DB).exists():
            verify_carryover(current / DB, stage / DB)
        if source is not None and Path(source).resolve() != current.resolve():
            verify_carryover(incoming / DB, stage / DB)
        report["carried_over"] = carryover_summary(stage / DB)
        report["pdfs"] = validate_pdfs(stage / DB, stage, final)
        report["tables"] = {table: value["count"] for table, value in record_fingerprints(stage / DB).items()}
        report["reviews"] = report["tables"]["reviews"]
    return stage, report


def recover(home):
    """Rollback interrupted activation, or finish housekeeping after a committed one."""
    home = Path(home).resolve()
    journal_path = home / "pending.json"
    journal = read_json(journal_path)
    if not journal:
        return
    target = Path(journal["data"]).resolve()
    backup = Path(journal["backup"]).resolve()
    # Journal is local installer state, but still enforce its deletion boundaries.
    if target != Path(journal["expected_data"]).resolve() or not inside(backup, home / "backups"):
        raise ValueError("Invalid setup recovery journal; keep it for manual recovery.")
    state = read_json(home / "current.json", {})
    if state.get("installation_id") == journal["installation_id"]:
        journal_path.unlink()
        return
    if backup.exists():
        if target.exists():
            failed = home / "backups" / (journal["installation_id"] + "-interrupted")
            if failed.exists():
                raise ValueError("Recovery needs attention: an interrupted-data folder already exists.")
            target.rename(failed)
        backup.rename(target)
    elif not journal["had_data"] and target.exists():
        target.rename(home / "backups" / (journal["installation_id"] + "-interrupted"))
    journal_path.unlink()
    say("Recovered the previous installation after an interrupted setup.")


def activate(home, data, stage, new_state, report=None):
    home, data, stage = Path(home).resolve(), Path(data).resolve(), Path(stage).resolve()
    require_closed(data)
    backup = home / "backups" / new_state["installation_id"] / "data"
    backup.parent.mkdir(parents=True, exist_ok=False)
    old_state = read_json(home / "current.json")
    atomic_json(backup.parent / "previous-installation.json", old_state)
    if report is not None:
        atomic_json(backup.parent / "transfer-report.json", report)
    journal = dict(data=str(data), expected_data=str(data), backup=str(backup),
                   had_data=data.exists(), installation_id=new_state["installation_id"])
    atomic_json(home / "pending.json", journal)
    try:
        if data.exists():
            data.rename(backup)
        stage.rename(data)
        atomic_json(home / "current.json", new_state)
    except BaseException:
        recover(home)
        raise
    (home / "pending.json").unlink()
    return backup.parent


def folder_size(path):
    total = 0
    for item in Path(path).rglob("*"):
        try:
            if item.is_file() and not item.is_symlink():
                total += item.stat().st_size
        except OSError:
            pass
    return total


def remove_old_versions(home, state):
    """Delete superseded app environments and setup leftovers after a successful setup.

    Review data, its update backups, and any previous project folder are never
    touched. Only folders inside this installation that the active version does
    not use are removed; a folder that cannot be removed is reported and kept.
    """
    home = Path(home).resolve()
    active = Path(state["runner"]).resolve().parent
    # Setup may be running on an older version's Python; never delete it mid-run.
    running = Path(sys.executable).resolve()
    targets, skipped = [], []
    versions = home / "versions"
    if versions.is_dir():
        for version in versions.iterdir():
            if not version.is_dir() or version.resolve() == active:
                continue
            if inside(running, version):
                skipped.append(version.name)
                continue
            targets.append(version)
    transactions = home / "transactions"
    if transactions.is_dir():
        targets += [t for t in transactions.iterdir() if t.is_dir()]
    cache = home / "package-cache"
    if cache.is_dir():
        targets += [c for c in cache.iterdir() if c.is_dir() and c.name.endswith((".partial", ".invalid"))]
    removed, freed, kept = 0, 0, []
    for target in targets:
        size = folder_size(target)
        try:
            remove_owned(target, home)
        except (OSError, ValueError) as error:
            kept.append(f"{target.name} ({error})")
            continue
        removed += 1
        freed += size
    say(f"Removed {removed} older version/setup folder(s), freeing {freed / 1024 ** 3:.1f} GB. "
        "Reviews and their backups were kept.")
    for item in kept:
        say("Could not remove " + item + "; it can be deleted after restarting Windows.")
    for name in skipped:
        say(f"Kept {name} because setup is running from it. It is removed by the next update.")
    return dict(removed=removed, freed_bytes=freed, kept=len(kept))


def clean_env():
    env = dict(os.environ, PYTHONNOUSERSITE="1", PYTHONUTF8="1", PIP_NO_INPUT="1")
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    return env


def execute(args, **kwargs):
    say("  " + " ".join(str(a) for a in args[:4]))
    subprocess.run([str(a) for a in args], check=True, env=clean_env(), **kwargs)


def run_elevated(executable, arguments, timeout=1800):
    """Run a program after one Windows UAC approval and return its exit code."""
    from ctypes import wintypes

    class Info(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("fMask", wintypes.ULONG), ("hwnd", wintypes.HWND),
                    ("lpVerb", wintypes.LPCWSTR), ("lpFile", wintypes.LPCWSTR),
                    ("lpParameters", wintypes.LPCWSTR), ("lpDirectory", wintypes.LPCWSTR),
                    ("nShow", ctypes.c_int), ("hInstApp", wintypes.HINSTANCE), ("lpIDList", ctypes.c_void_p),
                    ("lpClass", wintypes.LPCWSTR), ("hkeyClass", wintypes.HKEY), ("dwHotKey", wintypes.DWORD),
                    ("hIcon", wintypes.HANDLE), ("hProcess", wintypes.HANDLE)]
    shell = ctypes.WinDLL("shell32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    shell.ShellExecuteExW.argtypes = (ctypes.POINTER(Info),)
    kernel.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    kernel.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    # SEE_MASK_NOCLOSEPROCESS | SEE_MASK_NOASYNC; the window stays hidden.
    info = Info(cbSize=ctypes.sizeof(Info), fMask=0x140, lpVerb="runas", lpFile=str(executable),
                lpParameters=arguments, nShow=0)
    if not shell.ShellExecuteExW(ctypes.byref(info)):
        error = ctypes.get_last_error()
        if error == 1223:
            raise PermissionError("administrator permission was declined")
        raise ctypes.WinError(error)
    try:
        if kernel.WaitForSingleObject(info.hProcess, int(timeout * 1000)) != 0:
            raise TimeoutError("the installer did not finish in time")
        code = wintypes.DWORD()
        kernel.GetExitCodeProcess(info.hProcess, ctypes.byref(code))
        return code.value
    finally:
        kernel.CloseHandle(info.hProcess)


def signed_by(path, signer):
    """Windows Authenticode check, for downloads that have no pinned checksum."""
    literal = str(path).replace("'", "''")
    script = (f"Get-AuthenticodeSignature -LiteralPath '{literal}' | Where-Object Status -eq 'Valid' | "
              "ForEach-Object { $_.SignerCertificate.Subject }")
    result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                            capture_output=True, text=True, timeout=120)
    return f"O={signer}," in result.stdout + ","


def ask_admin(what, percent):
    progress(percent, f"Windows will ask for administrator permission to install {what}. "
                 "If no prompt appears, check the taskbar.")


def check_python(manifest):
    version = f"{sys.version_info.major}.{sys.version_info.minor}"
    if version not in manifest["supported_python"] or platform.architecture()[0] != manifest["architecture"]:
        raise ValueError("Install a supported 64-bit Python version: " + ", ".join(manifest["supported_python"]))


EXTRAS_LOCK = "requirements-extras.txt"


def pip_locks(project):
    """The runtime lock is installed; the optional-feature lock only pins versions."""
    locks = ["-r", project / "requirements.txt"]
    if (project / EXTRAS_LOCK).is_file():
        locks += ["-c", project / EXTRAS_LOCK]
    return locks


def package_spec(project, semantic):
    config = tomllib.loads((project / "pyproject.toml").read_text(encoding="utf-8"))
    requirements = list(config["build-system"]["requires"]) + list(config["project"]["dependencies"])
    extras = config["project"].get("optional-dependencies", {})
    # OCR's Python bridge is small and always installed; the engine is found separately.
    requirements += extras.get("ocr", [])
    if semantic:
        requirements += extras["semantic"]
    # App version/source changes can reuse dependencies. Dependency/lockfile,
    # optional-feature and interpreter changes automatically select a new cache.
    key = dict(format=1, requirements=requirements,
               lock=(project / "requirements.txt").read_text(encoding="utf-8"),
               extras_lock=(project / EXTRAS_LOCK).read_text(encoding="utf-8")
               if (project / EXTRAS_LOCK).is_file() else None,
               python=sys.version, platform=sys.platform, machine=platform.machine(),
               implementation=sys.implementation.cache_tag)
    return requirements, hashlib.sha256(json.dumps(key, sort_keys=True).encode()).hexdigest()


def verified_wheels(folder):
    try:
        manifest = read_json(folder / "wheels.json")
        if not isinstance(manifest, dict) or not manifest:
            return False
        actual = {p.name for p in folder.iterdir() if p.is_file() and p.suffix == ".whl"}
        return actual == set(manifest) and all(
            Path(name).name == name and digest(folder / name) == sha
            for name, sha in manifest.items())
    except (OSError, ValueError, TypeError):
        return False


def install_packages(python, project, home, semantic):
    """Reuse verified wheels, never a mutable environment or an old app package."""
    requirements, key = package_spec(project, semantic)
    cache_root = home / "package-cache"
    cache_root.mkdir(parents=True, exist_ok=True)
    cache = cache_root / key
    if verified_wheels(cache):
        progress(48, "Reusing verified package downloads.")
    else:
        progress(45, "Downloading application packages. This stage depends on your connection.")
        pending = cache_root / (key + "-" + uuid4().hex + ".partial")
        pending.mkdir()
        # A failed download never marks a partial cache as reusable.
        execute([python, "-m", "pip", "wheel", "--disable-pip-version-check",
                 "--only-binary=:all:", "--wheel-dir", pending, *pip_locks(project), *requirements])
        wheels = {p.name: digest(p) for p in pending.glob("*.whl")}
        if not wheels:
            raise ValueError("No application packages were downloaded.")
        atomic_json(pending / "wheels.json", wheels)
        if cache.exists():
            cache.rename(cache_root / (key + "-" + uuid4().hex + ".invalid"))
        pending.rename(cache)
    progress(65, "Installing verified packages into the new environment.")
    execute([python, "-m", "pip", "install", "--disable-pip-version-check",
             "--no-index", "--find-links", cache, "--no-compile", *pip_locks(project), *requirements])
    progress(78, "Installing this version of Systematic.")
    # Build tools are already present. Avoid a second download/build environment,
    # and install the app only once even when optional semantic search is enabled.
    execute([python, "-m", "pip", "install", "--disable-pip-version-check",
             "--no-index", "--no-deps", "--no-build-isolation", "--no-compile", project])
    execute([python, "-m", "pip", "check"])


TESSERACT_EXE = "tesseract.exe" if os.name == "nt" else "tesseract"


def tesseract_works(executable):
    """A usable OCR engine starts and has English language data."""
    try:
        result = subprocess.run([str(executable), "--list-langs"], capture_output=True,
                                text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0 and "eng" in (result.stdout + result.stderr).split()


def find_tesseract(home, hint=None):
    """Prefer the engine this installation used before, then standard install folders."""
    candidates = [hint, os.environ.get("SYSTEMATIC_TESSERACT"),
                  Path(home) / "tesseract" / TESSERACT_EXE, shutil.which("tesseract")]
    for variable, *parts in (("ProgramFiles", "Tesseract-OCR"), ("ProgramFiles(x86)", "Tesseract-OCR"),
                             ("LOCALAPPDATA", "Programs", "Tesseract-OCR")):
        if os.environ.get(variable):
            candidates.append(Path(os.environ[variable], *parts, TESSERACT_EXE))
    for candidate in candidates:
        if candidate and Path(candidate).is_file() and tesseract_works(candidate):
            return str(Path(candidate).resolve())
    return None


def download(url, destination):
    with urllib.request.urlopen(url, timeout=60) as response, open(destination, "wb") as stream:
        shutil.copyfileobj(response, stream, 1024 * 1024)


def run_tesseract_setup(installer, target):
    """Per-user install. The installer requests admin by default; RunAsInvoker declines that."""
    env = dict(os.environ, __COMPAT_LAYER="RunAsInvoker")
    # NSIS reads /D= verbatim: it must come last and stay unquoted, even with spaces.
    subprocess.run(f'"{installer}" /S /D={target}', check=True, timeout=900, env=env)


def run_tesseract_setup_elevated(installer):
    """Fallback: the standard machine-wide install in Program Files."""
    ask_admin("Tesseract OCR", 82)
    code = run_elevated(installer, "/S")
    if code:
        raise ValueError(f"the OCR installer stopped with code {code}")


def ensure_tesseract(home, spec, hint=None, windows=os.name == "nt"):
    """Find or install the pinned OCR engine. Failure disables OCR; it never stops setup."""
    found = find_tesseract(home, hint)
    if found or not spec or not windows:
        return found
    downloads = Path(home) / "downloads"
    downloads.mkdir(parents=True, exist_ok=True)
    installer = downloads / Path(urllib.parse.urlparse(spec["url"]).path).name
    partial = installer.with_name(installer.name + ".partial")
    try:
        progress(80, f"Downloading Tesseract OCR {spec['version']} for scanned PDFs (about 50 MB).")
        download(spec["url"], partial)
        if digest(partial) != spec["sha256"]:
            raise ValueError("the download did not match its pinned checksum and was not run")
        os.replace(partial, installer)
        progress(82, "Installing Tesseract OCR for this Windows user.")
        try:
            run_tesseract_setup(installer, Path(home) / "tesseract")
        except (OSError, subprocess.SubprocessError) as error:
            say(f"A per-user OCR install was not possible ({error}).")
        found = find_tesseract(home)
        if not found:
            run_tesseract_setup_elevated(installer)
            found = find_tesseract(home)
        if not found:
            raise ValueError("the installed engine did not start")
        return found
    except Exception as error:
        say(f"OCR was not installed ({error}). Systematic works without it; scanned pages stay "
            "flagged for manual review. Rerun setup to try again.")
        return None
    finally:
        for leftover in (partial, installer):
            try:
                leftover.unlink()
            except OSError:
                pass


DLL_ERRORS = ("DLL load failed", "WinError 126", "WinError 1114")


def ensure_vc_runtime(home, spec):
    """Microsoft's C++ runtime (needed by PyTorch) installs machine-wide: one UAC approval."""
    downloads = Path(home) / "downloads"
    downloads.mkdir(parents=True, exist_ok=True)
    installer = downloads / "vc_redist.x64.exe"
    try:
        progress(84, "Downloading the Microsoft Visual C++ runtime used by semantic search.")
        download(spec["url"], installer)
        if not signed_by(installer, spec["signer"]):
            raise ValueError("the runtime download is not signed by " + spec["signer"])
        ask_admin("the Microsoft Visual C++ runtime", 84)
        code = run_elevated(installer, "/install /quiet /norestart")
        # 1638: a newer runtime is already installed. 3010: restart recommended.
        if code not in (0, 1638, 3010):
            raise ValueError(f"the runtime installer stopped with code {code}")
    finally:
        installer.unlink(missing_ok=True)


def ensure_semantic_model(python, script, home, spec, previous=None, vc_spec=None, windows=os.name == "nt"):
    """Download the embedding model and prove it loads offline. Failure keeps fast retrieval."""
    if not spec:
        return None
    revision = spec.get("revision", "main")
    if revision == "main" and previous and previous.get("repo") == spec["repo"]:
        revision = previous["revision"]  # Keep the resolved commit: cached embeddings stay valid.

    def attempt():
        result = subprocess.run([str(python), str(script), str(Path(home) / "models"), spec["repo"], revision],
                                capture_output=True, text=True, env=clean_env(), timeout=1800)
        for line in result.stdout.splitlines():
            if line.startswith("SYSTEMATIC_MODEL="):
                return json.loads(line.split("=", 1)[1]), ""
        return None, (result.stdout + result.stderr).strip()

    try:
        progress(83, f"Downloading the semantic search model {spec['repo']} (about 90 MB).")
        model, output = attempt()
        if model is None and windows and vc_spec and any(e in output for e in DLL_ERRORS):
            ensure_vc_runtime(home, vc_spec)
            model, output = attempt()
        if model is None:
            raise ValueError(output.splitlines()[-1] if output else "no result")
        return model
    except Exception as error:
        say(f"Semantic search model was not installed ({error}). Systematic uses fast retrieval; "
            "rerun setup to try again.")
        return None


def report_dependencies(python, tesseract, semantic, model):
    rows = [("Python", True, f"{platform.python_version()} ({Path(python).parent.parent})"),
            ("Application packages", True, "installed and checked"),
            ("OCR for scanned PDFs", bool(tesseract), tesseract or "not available")]
    if semantic:
        rows.append(("Semantic search model", bool(model),
                     f"{model['repo']}@{model['revision'][:12]}" if model else "not available"))
    say("Dependency check:")
    for name, ok, detail in rows:
        say(f"  [{'OK' if ok else 'MISSING'}] {name}: {detail}")
    return {name: ok for name, ok, _ in rows}


def install(payload, home, data, source=None, extra_files=(), semantic=False, remove_old=False):
    payload, home, data = Path(payload).resolve(), Path(home).resolve(), Path(data).resolve()
    manifest = read_json(payload / "release.json")
    check_python(manifest)
    if inside(home, data) or inside(data, home):
        raise ValueError("Program files and review data must use separate folders.")
    with installation_lock(home):
        progress(10, "Checking the current installation and review folders.")
        recover(home)
        state = read_json(home / "current.json", {})
        if state.get("data_format", 1) > manifest["data_format"]:
            raise ValueError("These reviews need a newer installer. Downgrading their data format is not supported.")
        require_closed(data)
        original_data = data_fingerprint(data)
        source = locate_data(source) if source else None
        if source:
            if inside(home, source) or inside(source, home) or inside(data, source) or inside(source, data):
                if source != data:
                    raise ValueError("Choose a previous review folder separate from the installation and destination.")
            require_closed(source)
        original_source = data_fingerprint(source) if source else None
        # Immutable version directories keep an interrupted dependency install away from the working version.
        install_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid4().hex[:8]
        version = home / "versions" / (manifest["version"] + "-" + install_id)
        version.mkdir(parents=True)
        work = home / "transactions" / install_id
        work.mkdir(parents=True)
        progress(18, "Copying your reviews and verifying PDFs; originals remain untouched.")
        stage, report = prepare_data(data, source, work, data, extra_files)
        # Independent source backup is retained even when dependency installation fails.
        if source:
            say("The previous review folder remains available at: " + str(source))
        progress(35, "Review copy prepared. Creating a separate application environment.")
        venv = version / ".venv"
        execute([sys.executable, "-m", "venv", venv])
        python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        project = payload / "project"
        semantic = semantic or state.get("semantic", False)
        install_packages(python, project, home, semantic)
        tesseract = ensure_tesseract(home, manifest.get("tesseract"), state.get("tesseract"))
        for name in ("runner.py", "installer.py", "validate.py", "models.py"):
            shutil.copy2(payload / name, version / name)
        model = ensure_semantic_model(python, version / "models.py", home, manifest.get("semantic_model"),
                                      state.get("semantic_model"), manifest.get("vc_runtime")) if semantic else None
        dependencies = report_dependencies(python, tesseract, semantic, model)
        progress(85, "Checking saved values, extraction templates and verification exports.")
        # App initialization and exports operate only on the staged copy.
        before_validation = record_fingerprints(stage / DB) if (stage / DB).exists() else {}
        execute([python, version / "validate.py", stage, data])
        db_check(stage / DB)
        validate_pdfs(stage / DB, stage, data)
        if before_validation:
            after = record_fingerprints(stage / DB)
            for table, saved in before_validation.items():
                if after.get(table, []) != saved:
                    raise ValueError(f"Validation changed saved {table} records. A version-specific migration is required.")
        progress(93, "Confirming original reviews and PDFs have not changed during setup.")
        if data_fingerprint(data) != original_data or (source and data_fingerprint(source) != original_source):
            raise ValueError("Review data changed during setup. Close all copies of Systematic and retry. "
                             "No changed review data has been replaced.")
        if source:
            require_closed(source)
        report["verified_records"] = record_fingerprints(stage / DB)
        new_state = dict(installation_id=install_id, version=manifest["version"],
                         payload_id=manifest["payload_id"], python=str(python),
                         runner=str(version / "runner.py"), data=str(data),
                         launcher_icon=str(version / "Systematic.ico"),
                         data_format=manifest["data_format"], semantic=semantic, tesseract=tesseract,
                         semantic_model=model, dependencies=dependencies,
                         supported_python=manifest["supported_python"], installed_at=install_id)
        # Place the launcher before committing. Its code is a generic state-file reader.
        launcher = payload / "Systematic.exe"
        pending_launcher = home / "Systematic.next.exe"
        shutil.copy2(payload / "Systematic.ico", version / "Systematic.ico")
        shutil.copy2(launcher, pending_launcher)
        if digest(pending_launcher) != digest(launcher):
            raise ValueError("Launcher copy verification failed. Retry setup.")
        os.replace(pending_launcher, home / "Systematic.exe")
        progress(97, "Activating the new version and retaining the previous data as a backup.")
        backup = activate(home, data, stage, new_state, report)
        # Retain source snapshots and installation logs for recovery; never clean user backups automatically.
        say(f"Verified {report['reviews']} reviews and {report['pdfs']} PDF records.")
        if report.get("carried_over"):
            say("Carried over: " + describe(report["carried_over"]))
        say("Review storage: " + str(data))
        say("Backup: " + str(backup))
        if remove_old:
            progress(98, "Removing older Systematic versions. Reviews and backups are kept.")
            remove_old_versions(home, new_state)
        progress(99, "Reviews verified and version activated. Finishing the desktop shortcut.")
        say("SETUP_OK")
        return new_state


def discover_main(argv):
    parser = argparse.ArgumentParser()
    parser.add_argument("--discover", action="store_true")
    parser.add_argument("--roots-json", required=True)
    parser.add_argument("--home", type=Path, required=True)
    parser.add_argument("--data", type=Path, required=True)
    args = parser.parse_args(argv)
    roots = read_json(args.roots_json, [])
    candidates = discover(roots, exclude=[args.home, args.data], installed_data=args.data)
    installed = None
    if (args.data / DB).is_file():
        try:
            installed = dict(last_used=last_used(args.data),
                             description=describe(carryover_summary(args.data / DB)))
        except (sqlite3.Error, OSError, ValueError):
            installed = dict(last_used=0, description="")
    print("SYSTEMATIC_DISCOVERY=" + json.dumps(dict(candidates=candidates, installed=installed),
                                               ensure_ascii=False), flush=True)
    return 0


def main():
    if "--discover" in sys.argv[1:]:
        return discover_main(sys.argv[1:])
    parser = argparse.ArgumentParser()
    parser.add_argument("--payload", type=Path, required=True)
    parser.add_argument("--home", type=Path, required=True)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--source")
    parser.add_argument("--files-json")
    parser.add_argument("--semantic", action="store_true")
    parser.add_argument("--remove-old-versions", action="store_true")
    args = parser.parse_args()
    try:
        files = read_json(args.files_json, []) if args.files_json else []
        install(args.payload, args.home, args.data, args.source, files, args.semantic,
                args.remove_old_versions)
    except Exception as error:
        say("SETUP_ERROR: " + str(error))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
