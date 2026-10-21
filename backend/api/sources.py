"""GET /api/sources?file=&line=&page=&context= : the exact source lines behind a number.

Reads the bank's original file (from data/generated/) and returns the cited
line with a few lines either side. Only files in that folder can be read.
"""
from __future__ import annotations

from functools import lru_cache

from flask import Blueprint, jsonify, request

from api.errors import BadRequest, NotFound
from config import GENERATED_DIR
from pipeline.parse_pdf import read_pages

bp = Blueprint("sources", __name__)

ALLOWED_SUFFIXES = {".csv", ".txt", ".pdf"}
MAX_CONTEXT = 10


@lru_cache(maxsize=16)
def _lines(name: str, page: int | None) -> tuple[str, ...]:
    path = GENERATED_DIR / name
    if path.suffix == ".pdf":
        pages = read_pages(path)
        if page is None or not 1 <= page <= len(pages):
            raise BadRequest(f"{name} has pages 1-{len(pages)}; pass page=")
        return tuple(pages[page - 1])
    return tuple(path.read_text().splitlines())


@bp.get("/sources")
def get_source():
    name = request.args.get("file", "")
    allowed = {p.name for p in GENERATED_DIR.iterdir() if p.suffix in ALLOWED_SUFFIXES}
    if name not in allowed:  # also blocks any path like ../../.env
        raise NotFound(f"no source file {name!r}")
    try:
        line = int(request.args["line"])
        page = int(request.args["page"]) if request.args.get("page") else None
        context = min(int(request.args.get("context", 3)), MAX_CONTEXT)
    except (KeyError, ValueError):
        raise BadRequest("line (and page for PDFs) must be integers") from None

    lines = _lines(name, page)
    if not 1 <= line <= len(lines):
        raise BadRequest(f"line must be 1-{len(lines)}")
    start, end = max(1, line - context), min(len(lines), line + context)
    return jsonify({
        "file": name, "page": page, "line": line,
        "lines": [{"n": n, "text": lines[n - 1], "cited": n == line} for n in range(start, end + 1)],
    })