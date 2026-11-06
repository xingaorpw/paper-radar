"""Command line interface for paper-radar."""

from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

from . import arxiv, store
from .profile import build_profile, paper_words, score_paper

_VERSION_SUFFIX = re.compile(r"v\d+$")


def _configure_stdout() -> None:
    """Force UTF-8 output so Chinese text survives Windows code pages."""

    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if not callable(reconfigure):
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            pass


def _positive_int(value: str) -> int:
    try:
        number = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("必须是正整数") from exc
    if number < 1:
        raise argparse.ArgumentTypeError("必须是正整数")
    return number


def _normalise_paper_id(raw: str) -> str:
    raw = (raw or "").strip()
    if not raw:
        return ""
    if raw.startswith("http://") or raw.startswith("https://") or "/abs/" in raw:
        return arxiv.extract_id(raw)
    return _VERSION_SUFFIX.sub("", raw)


def _parse_iso(value: Any) -> datetime | None:
    if not value:
        return None
    raw = str(value).strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _human_age(value: Any, now: datetime) -> str | None:
    rated_at = _parse_iso(value)
    if rated_at is None:
        return None
    days = max(0, int((now - rated_at).total_seconds() // 86400))
    if days == 0:
        return "今天"
    return f"{days} 天前"


def _short_title(paper: Mapping[str, Any]) -> str:
    title = " ".join(str(paper.get("title") or "").split())
    if len(title) > 20:
        return title[:20] + "…"
    return title


def _build_reason(
    paper: Mapping[str, Any],
    profile: Mapping[str, float],
    ratings: Mapping[str, Mapping[str, str]],
    papers_by_id: Mapping[str, Mapping[str, Any]],
    now: datetime,
) -> str:
    """One human sentence explaining why this paper is in the digest."""

    matches = [
        (word, profile.get(word, 0.0))
        for word in paper_words(paper)
        if profile.get(word, 0.0) > 0
    ]
    matches.sort(key=lambda item: (-item[1], item[0]))
    if matches:
        keywords = " / ".join(word for word, _ in matches[:2])
        return f"匹配关键词 {keywords}"

    paper_id = str(paper.get("id") or "")
    paper_categories = set(paper.get("categories") or [])
    liked: list[tuple[str, Mapping[str, str]]] = [
        (pid, rating)
        for pid, rating in ratings.items()
        if rating.get("action") == "like" and pid != paper_id
    ]
    liked.sort(
        key=lambda item: _parse_iso(item[1].get("created_at"))
        or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    for liked_id, rating in liked:
        liked_paper = papers_by_id.get(liked_id)
        if liked_paper is None:
            continue
        common = paper_categories.intersection(liked_paper.get("categories") or [])
        if not common:
            continue
        category = next(
            (cat for cat in paper.get("categories") or [] if cat in common),
            sorted(common)[0],
        )
        age = _human_age(rating.get("created_at"), now)
        prefix = f"和你{age}点赞的" if age else "和你之前点赞的"
        return f"{prefix} {_short_title(liked_paper)} 同属 {category}"

    if paper_id in ratings:
        action = ratings[paper_id].get("action", "评分")
        return f"你之前标过 {action}，这条仍按画像参与排序"
    if not ratings:
        return "你还没评分过，先看看"
    return "画像里还没有强相关词，先按新鲜度看看"


def _cmd_fetch(args: argparse.Namespace) -> int:
    papers = arxiv.fetch(args.query, args.max)
    added = store.upsert_papers(papers)
    print(f"已抓取 {len(papers)} 篇，新增 {added} 篇。")
    if added == 0 and papers:
        print("没有新论文；已更新库中论文的元数据。")
    print(f"数据库：{store.resolve_db_path()}")
    return 0


def _cmd_rate(args: argparse.Namespace) -> int:
    paper_id = _normalise_paper_id(args.paper_id)
    if not paper_id:
        raise ValueError("paper_id 不能为空")
    store.init_db()
    paper = store.get_paper(paper_id)
    if paper is None:
        raise ValueError(f"找不到论文 {paper_id}，请先运行 fetch 入库。")

    papers = store.all_papers()
    before = build_profile(papers, store.ratings())
    store.set_rating(paper_id, args.action)
    after = build_profile(papers, store.ratings())

    print(f"已记录：{paper_id} → {args.action}")
    print(f"标题：{paper.get('title') or '(无标题)'}")

    deltas: list[tuple[str, float]] = []
    for word in set(before) | set(after):
        delta = after.get(word, 0.0) - before.get(word, 0.0)
        if abs(delta) > 1e-12:
            deltas.append((word, delta))
    deltas.sort(key=lambda item: (-abs(item[1]), item[0]))

    if not deltas:
        print("画像权重没有变化（这篇论文没有可用关键词）。")
        return 0
    print("画像里权重变化最大的 3 个词：")
    for word, delta in deltas[:3]:
        print(f"  {word:<18} {delta:+.3f}")
    return 0


def _cmd_digest(args: argparse.Namespace) -> int:
    store.init_db()
    papers = store.all_papers()
    if not papers:
        print("还没有论文，先运行：python -m paper_radar fetch --query \"cat:cs.CL\"")
        return 0

    ratings = store.ratings()
    profile = build_profile(papers, ratings)
    now = datetime.now(timezone.utc)

    if ratings:
        ranked = sorted(
            papers,
            key=lambda paper: (
                score_paper(profile, paper),
                str(paper.get("published") or ""),
            ),
            reverse=True,
        )
    else:
        # Cold start: no ratings yet, so freshness is the only honest signal.
        ranked = sorted(
            papers,
            key=lambda paper: str(paper.get("published") or ""),
            reverse=True,
        )

    papers_by_id = {str(paper.get("id")): paper for paper in papers}
    shown = ranked[: args.top]
    today = datetime.now().strftime("%Y-%m-%d")
    print(f"paper-radar · {today} · 共 {len(papers)} 篇，展示 {len(shown)} 篇")
    print("=" * 68)

    for index, paper in enumerate(shown, start=1):
        score = score_paper(profile, paper)
        published = str(paper.get("published") or "")[:10] or "未知日期"
        categories = ", ".join(paper.get("categories") or []) or "未分类"
        reason = _build_reason(paper, profile, ratings, papers_by_id, now)
        print(f"[{index}] 分数 {score:.2f} | {published} | {categories}")
        print(f"    {paper.get('title') or '(无标题)'}")
        print(f"    理由：{reason}")
        link = paper.get("link") or f"https://arxiv.org/abs/{paper.get('id')}"
        print(f"    {link}")
        print()
    return 0


def _cmd_stats(args: argparse.Namespace) -> int:
    store.init_db()
    papers = store.all_papers()
    ratings = store.ratings()
    profile = build_profile(papers, ratings)
    likes = sum(1 for rating in ratings.values() if rating.get("action") == "like")
    skips = sum(1 for rating in ratings.values() if rating.get("action") == "skip")

    print(f"论文总数：{len(papers)}")
    print(f"已评分：{len(ratings)} 篇（like {likes}，skip {skips}）")
    print("画像关键词 Top 10：")
    top = sorted(profile.items(), key=lambda item: (-item[1], item[0]))[:10]
    if not top:
        print("  （还没有评分，先运行 rate）")
        return 0
    for index, (word, weight) in enumerate(top, start=1):
        print(f"  {index:>2}. {word:<20} {weight:+.3f}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="paper-radar",
        description="arXiv 每日个性化雷达：从你的 like / skip 里学习关键词画像。",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    fetch_parser = subparsers.add_parser("fetch", help="抓取 arXiv 论文并入库")
    fetch_parser.add_argument(
        "--query",
        default="cat:cs.CL",
        help="arXiv 查询表达式（默认：cat:cs.CL）",
    )
    fetch_parser.add_argument(
        "--max", type=_positive_int, default=50, help="最多抓取多少篇（默认 50）"
    )
    fetch_parser.set_defaults(func=_cmd_fetch)

    rate_parser = subparsers.add_parser("rate", help="给论文点赞或跳过")
    rate_parser.add_argument("paper_id", help="论文 id，例如 2601.00001")
    rate_parser.add_argument("action", choices=("like", "skip"), help="评分动作")
    rate_parser.set_defaults(func=_cmd_rate)

    digest_parser = subparsers.add_parser("digest", help="按画像生成今日推荐")
    digest_parser.add_argument(
        "--top", type=_positive_int, default=10, help="展示多少篇（默认 10）"
    )
    digest_parser.set_defaults(func=_cmd_digest)

    stats_parser = subparsers.add_parser("stats", help="查看论文与画像统计")
    stats_parser.set_defaults(func=_cmd_stats)

    return parser


def main(argv: list[str] | None = None) -> int:
    _configure_stdout()
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except (arxiv.ArxivError, ValueError, OSError, sqlite3.Error) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\n已取消。", file=sys.stderr)
        return 130
