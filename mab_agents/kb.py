"""Shop knowledge base: your own documents, searchable by every agent.

Documents live in a folder (default ./knowledge). `sync` extracts text (pypdf
for PDFs, plain read for .md/.txt/.csv), chunks it and indexes it in a SQLite
FTS5 table ranked by BM25. `search` returns the best chunks with file and page
so agents can cite them. Nothing leaves the machine until an agent puts a chunk
into a prompt.

Put in it: the MOTOR Guide to Estimating procedure pages (P-pages) your
estimating system ships, DRP agreements, OEM repair procedures and position
statements you have downloaded, the employee handbook, SOPs, warranty wording,
vendor price lists. Only documents the shop is licensed to use.
"""
from __future__ import annotations

import hashlib
import logging
import re
import sqlite3
import threading
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

TEXT_SUFFIXES = {".md", ".txt", ".csv", ".markdown", ".rst"}
PDF_SUFFIXES = {".pdf"}
STOPWORDS = set("the a an and or of to in for on with is are was be by at as it this that from do does how what when where which who why can should i we you our your my me".split())

SCHEMA = """
CREATE TABLE IF NOT EXISTS docs (
    path TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    sha TEXT NOT NULL,
    pages INTEGER NOT NULL,
    chunks INTEGER NOT NULL,
    synced_at TEXT NOT NULL
);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks USING fts5(
    text, title, path UNINDEXED, page UNINDEXED, idx UNINDEXED,
    tokenize='porter unicode61'
);
"""


@dataclass
class Hit:
    path: str
    title: str
    page: int
    score: float
    snippet: str
    text: str


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:16]


def extract_pages(path: Path) -> list[str]:
    """Return one string per page (text files count as one page)."""
    suffix = path.suffix.lower()
    if suffix in PDF_SUFFIXES:
        from pypdf import PdfReader

        logging.getLogger("pypdf").setLevel(logging.ERROR)   # font warnings are noise for search

        reader = PdfReader(str(path))
        return [(page.extract_text() or "") for page in reader.pages]
    if suffix in TEXT_SUFFIXES:
        return [path.read_text(encoding="utf-8", errors="replace")]
    raise ValueError(f"Unsupported file type: {path.suffix} ({path.name}). Use PDF, .md, .txt or .csv.")


def chunk_text(text: str, size: int = 2200, overlap: int = 250) -> list[str]:
    """Split on paragraph boundaries into ~size-char chunks with a little overlap."""
    size = max(int(size), 200)
    overlap = max(0, min(int(overlap), size // 2))
    text = re.sub(r"[ \t]+", " ", text).strip()
    if not text:
        return []
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks: list[str] = []
    cur = ""
    for p in paras:
        while len(p) > size:                     # a single giant paragraph: hard split
            if cur:
                chunks.append(cur)
                cur = ""
            chunks.append(p[:size])
            p = p[size - overlap:]
        if len(cur) + len(p) + 2 > size and cur:
            chunks.append(cur)
            cur = cur[-overlap:] + "\n\n" + p if overlap else p
        else:
            cur = f"{cur}\n\n{p}" if cur else p
    if cur:
        chunks.append(cur)
    return chunks


def _fts_query(query: str, mode: str) -> str:
    terms = [t for t in re.findall(r"[a-z0-9][a-z0-9'&/-]*", query.lower()) if t not in STOPWORDS]
    terms = [t.replace('"', "") for t in terms] or [query.strip().replace('"', "")]
    quoted = [f'"{t}"' for t in terms if t]
    return (" AND " if mode == "and" else " OR ").join(quoted)


class KnowledgeBase:
    def __init__(self, db_path: str | Path, docs_dir: str | Path, *, chunk_chars: int = 2200, overlap_chars: int = 250):
        self.db_path = Path(db_path)
        self.docs_dir = Path(docs_dir)
        self.chunk_chars = chunk_chars
        self.overlap_chars = overlap_chars
        self._local = threading.local()
        self._shared: sqlite3.Connection | None = None
        if str(self.db_path) == ":memory:":
            self._shared = self._connect(":memory:")
        self.conn.executescript(SCHEMA)

    @staticmethod
    def _connect(path: str | Path) -> sqlite3.Connection:
        c = sqlite3.connect(path, check_same_thread=False, timeout=10)
        c.row_factory = sqlite3.Row
        return c

    @property
    def conn(self) -> sqlite3.Connection:
        """Per-thread connection to the file (shared connection for :memory:)."""
        if self._shared is not None:
            return self._shared
        c = getattr(self._local, "conn", None)
        if c is None:
            c = self._local.conn = self._connect(self.db_path)
        return c

    # ---- indexing ----------------------------------------------------------
    def _index(self, rel: str, title: str, pages: Iterable[str], sha: str) -> int:
        pages = list(pages)
        self.conn.execute("DELETE FROM chunks WHERE path = ?", (rel,))
        n = 0
        for page_no, page_text in enumerate(pages, start=1):
            for i, chunk in enumerate(chunk_text(page_text, self.chunk_chars, self.overlap_chars)):
                self.conn.execute("INSERT INTO chunks (text, title, path, page, idx) VALUES (?, ?, ?, ?, ?)", (chunk, title, rel, page_no, i))
                n += 1
        self.conn.execute(
            "INSERT INTO docs (path, title, sha, pages, chunks, synced_at) VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(path) DO UPDATE SET title=excluded.title, sha=excluded.sha, pages=excluded.pages, chunks=excluded.chunks, synced_at=excluded.synced_at",
            (rel, title, sha, len(pages), n, datetime.now().isoformat(timespec="seconds")),
        )
        return n

    def sync(self) -> dict[str, Any]:
        """Index new/changed files under docs_dir; drop index entries for deleted files."""
        stats = {"added": [], "updated": [], "removed": [], "unchanged": 0, "skipped": [], "chunks": 0}
        known = {r["path"]: r["sha"] for r in self.conn.execute("SELECT path, sha FROM docs")}
        seen: set[str] = set()
        if self.docs_dir.exists():
            for path in sorted(p for p in self.docs_dir.rglob("*") if p.is_file()):
                rel = str(path.relative_to(self.docs_dir))
                if path.suffix.lower() not in TEXT_SUFFIXES | PDF_SUFFIXES or path.name.startswith("."):
                    if path.suffix.lower() not in {".toml", ".json", ".db"}:
                        stats["skipped"].append(rel)
                    continue
                seen.add(rel)
                sha = _sha(path.read_bytes())
                if known.get(rel) == sha:
                    stats["unchanged"] += 1
                    continue
                try:
                    pages = extract_pages(path)
                except Exception as exc:  # noqa: BLE001 - one bad file must not stop the sync
                    stats["skipped"].append(f"{rel}: {exc}")
                    continue
                n = self._index(rel, path.stem.replace("_", " ").replace("-", " "), pages, sha)
                stats["chunks"] += n
                (stats["updated"] if rel in known else stats["added"]).append(rel)
        for rel in set(known) - seen:
            self.conn.execute("DELETE FROM chunks WHERE path = ?", (rel,))
            self.conn.execute("DELETE FROM docs WHERE path = ?", (rel,))
            stats["removed"].append(rel)
        self.conn.commit()
        return stats

    def add_text(self, title: str, text: str, path: str | None = None) -> int:
        """Save an ad-hoc note (e.g. a pasted carrier email) as a file under docs_dir/notes/ and index it,
        so the next sync() keeps it like any other document."""
        slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-") or "note"
        rel = path or f"notes/{slug}.txt"
        target = self.docs_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        body = f"# {title}\n\n{text.strip()}\n"
        target.write_text(body, encoding="utf-8")
        n = self._index(rel, title, [body], _sha(body.encode()))
        self.conn.commit()
        return n

    # ---- querying ------------------------------------------------------------
    def search(self, query: str, k: int = 5) -> list[Hit]:
        if not query.strip():
            return []
        for mode in ("and", "or"):
            q = _fts_query(query, mode)
            try:
                rows = self.conn.execute(
                    "SELECT path, title, page, idx, text, bm25(chunks) AS score, "
                    "snippet(chunks, 0, '[', ']', ' … ', 28) AS snip "
                    "FROM chunks WHERE chunks MATCH ? ORDER BY score LIMIT ?",
                    (q, k),
                ).fetchall()
            except sqlite3.OperationalError:
                rows = []
            if rows:
                return [Hit(path=r["path"], title=r["title"], page=int(r["page"]), score=round(-float(r["score"]), 3), snippet=r["snip"], text=r["text"]) for r in rows]
        return []

    def search_dicts(self, query: str, k: int = 5) -> list[dict[str, Any]]:
        return [asdict(h) for h in self.search(query, k)]

    def documents(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self.conn.execute("SELECT path, title, pages, chunks, synced_at FROM docs ORDER BY path")]

    def close(self) -> None:
        self.conn.close()
