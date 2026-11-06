"""Minimal arXiv Atom API client.

Only ``requests`` is used for network access; XML parsing is handled by the
standard library so the project stays easy to audit and package.
"""

from __future__ import annotations

import re
from typing import Any
from xml.etree import ElementTree as ET

import requests

ATOM_NAMESPACE = "http://www.w3.org/2005/Atom"
ARXIV_NAMESPACE = "http://arxiv.org/schemas/atom"
_ATOM = f"{{{ATOM_NAMESPACE}}}"
_ARXIV = f"{{{ARXIV_NAMESPACE}}}"

API_URL = "http://export.arxiv.org/api/query"
USER_AGENT = (
    "paper-radar/0.1.0 "
    "(+https://github.com/xingaorpw/paper-radar; mailto:opensource@example.com)"
)

_VERSION_SUFFIX = re.compile(r"v\d+$")


class ArxivError(RuntimeError):
    """Raised when arXiv cannot be reached or returns unusable data."""


def _clean_text(value: str | None) -> str:
    """Collapse all whitespace (including newlines) into single spaces."""

    return " ".join((value or "").split())


def extract_id(raw_id: str | None) -> str:
    """Extract a stable paper id from an arXiv id/URL.

    ``http://arxiv.org/abs/2601.00001v2`` becomes ``2601.00001``.  The version
    suffix is intentionally removed so a later revision does not create a new
    database row or lose its rating.
    """

    raw = (raw_id or "").strip()
    if not raw:
        return ""
    if "/abs/" in raw:
        identifier = raw.split("/abs/", 1)[1]
    else:
        identifier = raw.rstrip("/").rsplit("/", 1)[-1]
    identifier = identifier.strip("/")
    return _VERSION_SUFFIX.sub("", identifier)


def _first_text(entry: ET.Element, tag: str) -> str:
    node = entry.find(f"{_ATOM}{tag}")
    if node is None:
        return ""
    return _clean_text(node.text)


def _parse_entry(entry: ET.Element) -> dict[str, Any] | None:
    raw_id = _first_text(entry, "id")
    paper_id = extract_id(raw_id)
    if not paper_id:
        return None

    authors = [
        _clean_text(node.text)
        for node in entry.findall(f"{_ATOM}author/{_ATOM}name")
        if _clean_text(node.text)
    ]

    categories: list[str] = []
    primary = entry.find(f"{_ARXIV}primary_category")
    if primary is not None:
        primary_term = (primary.get("term") or "").strip()
        if primary_term:
            categories.append(primary_term)
    for node in entry.findall(f"{_ATOM}category"):
        term = (node.get("term") or "").strip()
        if term and term not in categories:
            categories.append(term)

    link = ""
    for node in entry.findall(f"{_ATOM}link"):
        rel = node.get("rel") or "alternate"
        href = (node.get("href") or "").strip()
        if href and rel == "alternate":
            link = href
            break
    if not link:
        link = raw_id

    return {
        "id": paper_id,
        "title": _first_text(entry, "title"),
        "summary": _first_text(entry, "summary"),
        "published": _first_text(entry, "published"),
        "authors": authors,
        "categories": categories,
        "link": link,
    }


def parse_feed(xml_text: str) -> list[dict[str, Any]]:
    """Parse an arXiv Atom response into plain dictionaries.

    This function is deliberately pure so tests never need a network call.
    """

    if not isinstance(xml_text, str) or not xml_text.strip():
        raise ValueError("arXiv 返回了空的 XML 内容")
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise ValueError(f"无法解析 arXiv Atom XML：{exc}") from exc

    if root.tag != f"{_ATOM}feed":
        raise ValueError("返回内容不是合法的 Atom feed")

    papers: list[dict[str, Any]] = []
    for entry in root.findall(f"{_ATOM}entry"):
        paper = _parse_entry(entry)
        if paper is not None:
            papers.append(paper)
    return papers


def fetch(query: str, max_results: int = 50) -> list[dict[str, Any]]:
    """Fetch recent papers for an arXiv query.

    Network, HTTP, and parsing failures are all converted to :class:`ArxivError`
    with a message that tells the user what to check next.
    """

    query = (query or "").strip()
    if not query:
        raise ValueError("查询条件不能为空，例如 --query \"cat:cs.CL\"")
    try:
        limit = int(max_results)
    except (TypeError, ValueError) as exc:
        raise ValueError("max_results 必须是整数") from exc
    if not 1 <= limit <= 2000:
        raise ValueError("max_results 必须在 1 到 2000 之间")

    params = {
        "search_query": query,
        "sortBy": "submittedDate",
        "sortOrder": "descending",
        "max_results": limit,
    }
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/atom+xml, application/xml;q=0.9, */*;q=0.8",
    }

    try:
        response = requests.get(API_URL, params=params, headers=headers, timeout=30)
    except requests.RequestException as exc:
        raise ArxivError(
            "请求 arXiv 失败，请检查网络、代理或稍后重试。"
            f"（{type(exc).__name__}: {exc}）"
        ) from exc

    if response.status_code == 429:
        raise ArxivError("arXiv 限流了（HTTP 429），请等几分钟再试。")
    if response.status_code >= 400:
        raise ArxivError(
            f"arXiv 返回 HTTP {response.status_code}，请稍后重试或检查查询条件。"
        )

    raw = response.content
    if not raw.strip():
        raise ArxivError("arXiv 返回了空响应，请稍后重试。")

    xml_text = raw.decode("utf-8", errors="replace")
    try:
        return parse_feed(xml_text)
    except ValueError as exc:
        raise ArxivError(f"arXiv 返回内容无法解析：{exc}") from exc
