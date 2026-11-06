"""Offline unit tests for paper-radar."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from paper_radar.arxiv import parse_feed  # noqa: E402
from paper_radar.profile import build_profile, score_paper  # noqa: E402
from paper_radar.store import (  # noqa: E402
    all_papers,
    get_paper,
    init_db,
    ratings,
    set_rating,
    upsert_papers,
)

FIXTURE = Path(__file__).parent / "fixtures" / "sample_feed.xml"


def make_paper(
    paper_id: str,
    title: str,
    summary: str,
    categories: list[str] | None = None,
    published: str = "2026-01-01T00:00:00Z",
) -> dict:
    return {
        "id": paper_id,
        "title": title,
        "summary": summary,
        "published": published,
        "authors": ["Test Author"],
        "categories": categories or ["cs.CL"],
        "link": f"http://arxiv.org/abs/{paper_id}",
    }


class ParseFeedTests(unittest.TestCase):
    def setUp(self) -> None:
        self.papers = parse_feed(FIXTURE.read_text(encoding="utf-8"))

    def test_parses_all_entries(self) -> None:
        self.assertEqual(len(self.papers), 3)

    def test_fields_and_id_extraction(self) -> None:
        first = self.papers[0]
        self.assertEqual(first["id"], "2601.00001")
        self.assertEqual(first["title"], "Retrieval Augmented Language Models")
        self.assertEqual(
            first["summary"],
            "We study retrieval augmented language models. "
            "The benchmark covers question answering.",
        )
        self.assertEqual(first["published"], "2026-01-02T10:00:00Z")
        self.assertEqual(first["authors"], ["Ada Lovelace", "Alan Turing"])
        self.assertEqual(first["categories"], ["cs.CL", "cs.AI"])
        self.assertEqual(first["link"], "http://arxiv.org/abs/2601.00001v2")

    def test_whitespace_is_normalised(self) -> None:
        for paper in self.papers:
            self.assertNotIn("\n", paper["title"])
            self.assertNotIn("\n", paper["summary"])
            self.assertNotIn("  ", paper["title"])
            self.assertNotIn("  ", paper["summary"])

    def test_malformed_xml_has_clear_error(self) -> None:
        with self.assertRaises(ValueError):
            parse_feed("<feed><entry>")

    def test_empty_feed_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            parse_feed("   ")


class ProfileTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime.now(timezone.utc)
        self.recent = self.now.isoformat()
        self.papers = [
            make_paper(
                "p1",
                "Retrieval Augmented Language Models for Question Answering",
                "We study retrieval and benchmark methods for question answering.",
            ),
            make_paper(
                "p2",
                "Dense Retrieval Benchmark for Evaluation",
                "A benchmark for dense retrieval systems.",
            ),
            make_paper(
                "p3",
                "Graph Neural Networks for Molecular Property Prediction",
                "We predict molecular properties with graph neural networks.",
                categories=["q-bio.BM"],
            ),
        ]

    def test_liked_direction_beats_unrelated_paper(self) -> None:
        profile = build_profile(
            self.papers,
            {
                "p1": {"action": "like", "created_at": self.recent},
                "p2": {"action": "like", "created_at": self.recent},
            },
        )
        self.assertGreater(
            score_paper(profile, self.papers[0]), score_paper(profile, self.papers[2])
        )
        self.assertGreater(
            score_paper(profile, self.papers[1]), score_paper(profile, self.papers[2])
        )

    def test_skip_lowers_score(self) -> None:
        liked_only = build_profile(
            self.papers,
            {"p1": {"action": "like", "created_at": self.recent}},
        )
        with_skip = build_profile(
            self.papers,
            {
                "p1": {"action": "like", "created_at": self.recent},
                "p3": {"action": "skip", "created_at": self.recent},
            },
        )
        self.assertLess(
            score_paper(with_skip, self.papers[2]),
            score_paper(liked_only, self.papers[2]),
        )

    def test_cold_start_profile_is_empty(self) -> None:
        profile = build_profile(self.papers, {})
        self.assertEqual(profile, {})
        self.assertEqual(score_paper(profile, self.papers[0]), 0.0)

    def test_old_like_has_less_weight(self) -> None:
        old = (self.now - timedelta(days=120)).isoformat()
        recent_profile = build_profile(
            self.papers, {"p1": {"action": "like", "created_at": self.recent}}
        )
        old_profile = build_profile(
            self.papers, {"p1": {"action": "like", "created_at": old}}
        )
        self.assertGreater(recent_profile["retrieval"], old_profile["retrieval"])
        self.assertGreater(recent_profile["retrieval"], 0.9)
        self.assertLess(old_profile["retrieval"], 0.3)


class StoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.db = Path(self.tempdir.name) / "radar.db"
        init_db(self.db)
        self.papers = [
            make_paper(
                "p1",
                "Retrieval Benchmark",
                "A retrieval benchmark.",
                ["cs.IR", "cs.LG"],
                "2026-01-02T00:00:00Z",
            ),
            make_paper(
                "p2",
                "Graph Networks",
                "Graph neural networks.",
                ["q-bio.BM"],
                "2026-01-01T00:00:00Z",
            ),
        ]

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_upsert_is_idempotent_and_refreshes_metadata(self) -> None:
        self.assertEqual(upsert_papers(self.papers, self.db), 2)
        self.assertEqual(upsert_papers(self.papers, self.db), 0)
        self.assertEqual(len(all_papers(self.db)), 2)

        stored = get_paper("p1", self.db)
        assert stored is not None
        first_seen = stored["first_seen"]

        updated = dict(self.papers[0], title="Retrieval Benchmark v2")
        self.assertEqual(upsert_papers([updated], self.db), 0)
        stored_again = get_paper("p1", self.db)
        assert stored_again is not None
        self.assertEqual(stored_again["title"], "Retrieval Benchmark v2")
        self.assertEqual(stored_again["first_seen"], first_seen)
        self.assertEqual(stored_again["categories"], ["cs.IR", "cs.LG"])

    def test_rating_is_read_and_overwritten(self) -> None:
        upsert_papers(self.papers, self.db)
        set_rating("p1", "like", self.db)
        current = ratings(self.db)
        self.assertEqual(current["p1"]["action"], "like")
        self.assertTrue(current["p1"]["created_at"])

        set_rating("p1", "skip", self.db)
        current = ratings(self.db)
        self.assertEqual(current["p1"]["action"], "skip")

    def test_invalid_rating_and_missing_paper_are_rejected(self) -> None:
        upsert_papers(self.papers, self.db)
        with self.assertRaises(ValueError):
            set_rating("p1", "maybe", self.db)
        with self.assertRaises(ValueError):
            set_rating("missing", "like", self.db)


class CliSmokeTests(unittest.TestCase):
    def test_digest_on_empty_database_does_not_crash(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            env = os.environ.copy()
            env["PAPER_RADAR_DB"] = str(Path(tempdir) / "radar.db")
            env["PYTHONIOENCODING"] = "utf-8"
            result = subprocess.run(
                [sys.executable, "-m", "paper_radar", "digest"],
                cwd=ROOT,
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=60,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("还没有论文", result.stdout)
        self.assertNotIn("Traceback", result.stderr)


if __name__ == "__main__":
    unittest.main()
