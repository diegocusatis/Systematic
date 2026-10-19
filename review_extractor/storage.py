"""SQLite persistence; records remain separate and identities are foreign-key scoped."""
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import threading


TABLES = {"reviews", "items", "papers", "pages", "suggestions", "evidence", "fields", "history", "feedback", "outcomes", "outcome_fields"}


class Store:
    def __init__(self, path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.connection = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA busy_timeout=10000")
        self.connection.executescript('''
          CREATE TABLE IF NOT EXISTS reviews(id TEXT PRIMARY KEY, data TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS items(id TEXT PRIMARY KEY, review_id TEXT NOT NULL,
            data TEXT NOT NULL, UNIQUE(review_id,id), FOREIGN KEY(review_id) REFERENCES reviews(id));
          CREATE TABLE IF NOT EXISTS papers(id TEXT PRIMARY KEY, review_id TEXT NOT NULL,
            data TEXT NOT NULL, UNIQUE(review_id,id), FOREIGN KEY(review_id) REFERENCES reviews(id));
          CREATE TABLE IF NOT EXISTS pages(id TEXT PRIMARY KEY, review_id TEXT NOT NULL, paper_id TEXT NOT NULL,
            data TEXT NOT NULL, FOREIGN KEY(review_id,paper_id) REFERENCES papers(review_id,id));
          CREATE TABLE IF NOT EXISTS suggestions(id TEXT PRIMARY KEY, review_id TEXT NOT NULL,
            paper_id TEXT NOT NULL, item_id TEXT NOT NULL, data TEXT NOT NULL,
            FOREIGN KEY(review_id,paper_id) REFERENCES papers(review_id,id),
            FOREIGN KEY(review_id,item_id) REFERENCES items(review_id,id));
          CREATE TABLE IF NOT EXISTS evidence(id TEXT PRIMARY KEY, review_id TEXT NOT NULL,
            paper_id TEXT NOT NULL, item_id TEXT NOT NULL, data TEXT NOT NULL,
            FOREIGN KEY(review_id,paper_id) REFERENCES papers(review_id,id),
            FOREIGN KEY(review_id,item_id) REFERENCES items(review_id,id));
          CREATE TABLE IF NOT EXISTS fields(id TEXT PRIMARY KEY, review_id TEXT NOT NULL,
            paper_id TEXT NOT NULL, item_id TEXT NOT NULL, data TEXT NOT NULL,
            UNIQUE(paper_id,item_id),
            FOREIGN KEY(review_id,paper_id) REFERENCES papers(review_id,id),
            FOREIGN KEY(review_id,item_id) REFERENCES items(review_id,id));
          CREATE TABLE IF NOT EXISTS outcomes(id TEXT PRIMARY KEY, review_id TEXT NOT NULL,
            paper_id TEXT NOT NULL, data TEXT NOT NULL, UNIQUE(review_id,paper_id,id),
            FOREIGN KEY(review_id,paper_id) REFERENCES papers(review_id,id));
          CREATE TABLE IF NOT EXISTS outcome_fields(id TEXT PRIMARY KEY, review_id TEXT NOT NULL,
            paper_id TEXT NOT NULL, outcome_id TEXT NOT NULL, item_id TEXT NOT NULL, data TEXT NOT NULL,
            UNIQUE(outcome_id,item_id),
            FOREIGN KEY(review_id,paper_id,outcome_id) REFERENCES outcomes(review_id,paper_id,id),
            FOREIGN KEY(review_id,item_id) REFERENCES items(review_id,id));
          CREATE TABLE IF NOT EXISTS history(id TEXT PRIMARY KEY, review_id TEXT NOT NULL, data TEXT NOT NULL,
            FOREIGN KEY(review_id) REFERENCES reviews(id));
          CREATE TABLE IF NOT EXISTS feedback(id TEXT PRIMARY KEY, review_id TEXT NOT NULL,
            paper_id TEXT NOT NULL, item_id TEXT NOT NULL, data TEXT NOT NULL,
            FOREIGN KEY(review_id,paper_id) REFERENCES papers(review_id,id),
            FOREIGN KEY(review_id,item_id) REFERENCES items(review_id,id));
          CREATE INDEX IF NOT EXISTS feedback_scope ON feedback(review_id,paper_id,item_id);
          CREATE INDEX IF NOT EXISTS items_review ON items(review_id);
          CREATE INDEX IF NOT EXISTS papers_review ON papers(review_id);
          CREATE INDEX IF NOT EXISTS pages_paper ON pages(paper_id);
          CREATE INDEX IF NOT EXISTS suggestions_scope ON suggestions(review_id,paper_id,item_id);
          CREATE INDEX IF NOT EXISTS evidence_scope ON evidence(review_id,paper_id,item_id);
          CREATE INDEX IF NOT EXISTS fields_scope ON fields(review_id,paper_id,item_id);
          CREATE INDEX IF NOT EXISTS outcomes_scope ON outcomes(review_id,paper_id);
          CREATE INDEX IF NOT EXISTS outcome_fields_scope ON outcome_fields(review_id,paper_id,outcome_id,item_id);
          CREATE INDEX IF NOT EXISTS history_review ON history(review_id);
        ''')
        self._depth = 0

    @contextmanager
    def transaction(self):
        with self.lock:
            outer = self._depth == 0
            if outer:
                self.connection.execute("BEGIN IMMEDIATE")
            self._depth += 1
            try:
                yield
                if outer:
                    self.connection.execute("COMMIT")
            except BaseException:
                if outer:
                    self.connection.execute("ROLLBACK")
                raise
            finally:
                self._depth -= 1

    def put(self, table, record):
        if table not in TABLES:
            raise ValueError("Unknown table")
        record = dict(record)
        if table == "pages":
            record["id"] = f'{record["paper_id"]}:{record["page_number"]}'
        elif table == "fields":
            record["id"] = f'{record["paper_id"]}:{record["item_id"]}'
        elif table == "outcome_fields":
            record["id"] = f'{record["paper_id"]}:{record["outcome_id"]}:{record["item_id"]}'
        columns = ["id"]
        if table != "reviews":
            columns.append("review_id")
        if table in {"pages", "suggestions", "evidence", "fields", "feedback", "outcomes", "outcome_fields"}:
            columns.append("paper_id")
        if table in {"suggestions", "evidence", "fields", "feedback", "outcome_fields"}:
            columns.append("item_id")
        if table == "outcome_fields":
            columns.append("outcome_id")
        values = [record[column] for column in columns]
        columns.append("data")
        values.append(json.dumps(record, ensure_ascii=False, allow_nan=False))
        updates = ",".join(f"{column}=excluded.{column}" for column in columns[1:])
        with self.lock:
            self.connection.execute(
                f'INSERT INTO {table} ({",".join(columns)}) VALUES ({",".join("?" for _ in columns)}) '
                f'ON CONFLICT(id) DO UPDATE SET {updates}', values)
        return record

    def get(self, table, record_id):
        if table not in TABLES:
            raise ValueError("Unknown table")
        with self.lock:
            row = self.connection.execute(f"SELECT data FROM {table} WHERE id=?", (record_id,)).fetchone()
        if not row:
            raise KeyError(record_id)
        return json.loads(row[0])

    def list(self, table, **filters):
        if table not in TABLES or not set(filters).issubset({"review_id", "paper_id", "item_id", "outcome_id"}):
            raise ValueError("Unknown table or filter")
        where = " AND ".join(f"{key}=?" for key in filters)
        sql = f"SELECT data FROM {table}" + (f" WHERE {where}" if where else "")
        if table == "history":
            sql += " ORDER BY rowid"
        with self.lock:
            rows = self.connection.execute(sql, list(filters.values())).fetchall()
        return [json.loads(row[0]) for row in rows]

    def delete_pages(self, paper_id):
        with self.lock:
            self.connection.execute("DELETE FROM pages WHERE paper_id=?", (paper_id,))

    def export_pages(self, review_id, paper_id=None):
        """Read source text/status without decoding PDF geometry and search chunks."""
        with self.lock:
            sql = """
                SELECT paper_id, json_extract(data, '$.page_number'),
                       json_extract(data, '$.status'), json_extract(data, '$.text')
                FROM pages WHERE review_id=?
            """
            parameters = [review_id]
            if paper_id is not None:
                sql += ' AND paper_id=?'
                parameters.append(paper_id)
            rows = self.connection.execute(sql, parameters).fetchall()
        return [dict(review_id=review_id, paper_id=row[0], page_number=row[1],
                     status=row[2] or 'ok', text=row[3] or '') for row in rows]

    def close(self):
        with self.lock:
            self.connection.close()
