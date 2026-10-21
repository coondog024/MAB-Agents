"""`mab kb fetch`: download the documents listed in knowledge/sources.toml.

sources.toml:
    [[source]]
    name = "Toyota position statement - pre and post repair scanning"
    url = "https://..."
    category = "oem/toyota"      # becomes a sub-folder
    note = "optional"

PDFs are saved as-is; HTML pages are reduced to text (.txt). Then `mab kb sync`
indexes them. Only fetch documents the shop may use; this is a convenience, not
a licence.
"""
from __future__ import annotations

import html
import re
import tomllib
import urllib.error
import urllib.request
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

USER_AGENT = "mab-agents/0.1 (+https://github.com/coondog024/MAB-Agents)"


class _Text(HTMLParser):
    SKIP = {"script", "style", "nav", "footer", "header", "noscript", "svg"}

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self._skip += 1
        elif tag in {"p", "br", "div", "li", "h1", "h2", "h3", "h4", "tr", "section", "article"}:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def html_to_text(raw: str) -> str:
    p = _Text()
    p.feed(raw)
    text = html.unescape("".join(p.parts))
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n\s*\n+", "\n\n", text).strip()


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:90] or "doc"


def load_sources(path: Path) -> list[dict[str, Any]]:
    with open(path, "rb") as fh:
        return tomllib.load(fh).get("source", [])


def fetch_one(src: dict[str, Any], out_dir: Path, timeout: int = 60) -> dict[str, Any]:
    url = src["url"]
    target_dir = out_dir / src.get("category", "misc")
    target_dir.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/pdf,text/html;q=0.9,*/*;q=0.8"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            ctype = resp.headers.get("Content-Type", "")
            data = resp.read()
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as exc:
        return {"name": src["name"], "url": url, "ok": False, "error": str(exc)}
    is_pdf = "pdf" in ctype.lower() or data[:5] == b"%PDF-" or url.lower().endswith(".pdf")
    base = slug(src["name"])
    if is_pdf:
        path = target_dir / f"{base}.pdf"
        path.write_bytes(data)
    else:
        text = html_to_text(data.decode("utf-8", errors="replace"))
        header = f"# {src['name']}\n\nSource: {url}\n" + (f"Note: {src['note']}\n" if src.get("note") else "") + "\n"
        path = target_dir / f"{base}.txt"
        path.write_text(header + text, encoding="utf-8")
    return {"name": src["name"], "url": url, "ok": True, "saved": str(path), "bytes": len(data), "pdf": is_pdf}


def fetch_all(sources_path: Path, out_dir: Path, *, only_missing: bool = True) -> list[dict[str, Any]]:
    results = []
    for src in load_sources(sources_path):
        base = slug(src["name"])
        folder = out_dir / src.get("category", "misc")
        if only_missing and (folder / f"{base}.pdf").exists() or (folder / f"{base}.txt").exists():
            results.append({"name": src["name"], "url": src["url"], "ok": True, "skipped": "already downloaded"})
            continue
        results.append(fetch_one(src, out_dir))
    return results
