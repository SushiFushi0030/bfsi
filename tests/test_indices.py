"""Offline regression and sparse/new-video boundaries for the index adapter."""
import copy
import json
import os
from pathlib import Path
import sys
import unittest

from bfsi.index.calculator import build_payload


class IndexTests(unittest.TestCase):
    def row(self, cid="901", stance="bullish", **updates):
        row = {"emotion":["neutral"],"action_intent":"not_applicable","creator_attitude":"not_applicable","sarcasm":"no"}
        row.update(comment_id=cid, video_oid="900001", video_bvid="BV1234567890",
                   raw_text="审计后新增测试评论" + cid, summary="独立测试摘要",
                   market_stance=stance, targets=[], relevance="financial", confidence="high",
                   depth=0, parent_comment_id=None, root_comment_id=None, observation_count=1,
                   context_used={"direct_parent": None, "root": None, "ancestor_chain": []})
        row.update(updates)
        return row

    def payload(self, rows, **metadata):
        return build_payload({"records": rows, **metadata}, {"oid": "900001", "bvid": "BV1234567890", "title": "独立新视频"}, {}, "test-run")

    def test_synthetic_formula_oracle_and_no_input_mutation(self):
        rows = [self.row(cid=str(901+i), stance=stance) for i, stance in enumerate(
            ["bullish", "bullish", "bearish", "mixed", "neutral"])]
        before = copy.deepcopy(rows)
        actual = self.payload(rows)
        self.assertEqual(actual["headline"]["bfsi"], 62.5)
        self.assertEqual(actual["headline"]["signal_coverage"], 80.0)
        self.assertEqual(actual["headline"]["disagreement"], 67)
        self.assertEqual(rows, before)

    def test_single_new_comment_and_empty_targets(self):
        actual = self.payload([self.row(observation_count=2)])
        self.assertEqual(actual["headline"]["bfsi"], 100)
        self.assertEqual(actual["headline"]["signal_coverage"], 100)
        self.assertEqual(actual["video"]["comment_count"], 1)
        self.assertEqual(actual["video"]["observation_count"], 2)
        self.assertEqual(actual["target_indices"], [])
        self.assertEqual(actual["top_financial_topics"], [])
        self.assertEqual(actual["representative_comments"]["bearish"], [])
        self.assertEqual(actual["video"]["video_bvid"], "BV1234567890")
        self.assertEqual(actual["video"]["title"], "独立新视频")

    def test_mixed_only_and_no_bull_bear_signals(self):
        actual = self.payload([self.row(stance="mixed")])
        self.assertEqual(actual["headline"]["bfsi"], 50)
        self.assertFalse(actual["headline"]["no_directional_signal"])
        self.assertTrue(actual["headline"]["no_bullish_bearish_signal"])

    def test_no_directional_signal_has_explicit_flag(self):
        actual = self.payload([self.row(stance="not_applicable")])
        self.assertEqual(actual["headline"]["bfsi"], 50)
        self.assertEqual(actual["headline"]["signal_coverage"], 0)
        self.assertTrue(actual["headline"]["no_directional_signal"])

    def test_exact_duplicate_ids_deduplicated_without_weight(self):
        a = self.row(observation_count=3)
        actual = self.payload([a, copy.deepcopy(a), self.row(cid="902", stance="bearish")])
        self.assertEqual(actual["headline"]["bfsi"], 50)
        self.assertEqual(actual["video"]["comment_count"], 2)
        self.assertEqual(actual["video"]["observation_count"], 4)
        self.assertEqual(actual["analysis"]["duplicate_records_ignored"], 1)

    def test_conflicting_duplicate_id_rejected(self):
        with self.assertRaisesRegex(ValueError, "冲突"):
            self.payload([self.row(), self.row(stance="bearish")])

    def test_cross_video_oid_rejected(self):
        with self.assertRaisesRegex(ValueError, "OID"):
            self.payload([self.row(), self.row(cid="902", video_oid="900002")])

    def test_cross_video_bvid_rejected(self):
        with self.assertRaisesRegex(ValueError, "BV"):
            self.payload([self.row(video_bvid="BV0987654321")])

    def test_empty_input_rejected(self):
        with self.assertRaisesRegex(ValueError, "没有可计算"):
            self.payload([])

    def test_target_alias_threshold_per_comment_and_multilabel(self):
        targets = [{"raw_name": "通货膨胀", "normalized_name": "通货膨胀", "target_type": "macro"},
                   {"raw_name": "通胀", "normalized_name": "通胀", "target_type": "macro"}]
        rows = [self.row(cid=str(901 + i), targets=targets, emotion=["neutral", "humorous"]) for i in range(5)]
        actual = self.payload(rows)
        self.assertEqual(len(actual["target_indices"]), 1)
        self.assertEqual(actual["target_indices"][0]["target_name"], "通胀")
        self.assertEqual(actual["target_indices"][0]["total_mentions"], 5)
        self.assertEqual(actual["distributions"]["emotion"]["humorous"], 5)
        self.assertEqual(actual["distributions"]["emotion"]["neutral"], 5)
        self.assertEqual(self.payload(rows[:4])["target_indices"], [])

    def test_dynamic_representatives_limits_and_no_repeated_ids(self):
        rows = []
        for stance in ("bullish", "bearish", "mixed", "neutral"):
            for i in range(7):
                rows.append(self.row(cid=str(901 + len(rows)), stance=stance,
                                     action_intent="ask_advice" if stance == "neutral" else "not_applicable",
                                     emotion=["humorous"] if i % 2 else ["neutral"]))
        actual = self.payload(rows)
        groups = actual["representative_comments"]
        self.assertEqual([len(groups[k]) for k in ("bullish", "bearish", "mixed", "ask_advice", "humorous")], [3, 3, 2, 2, 2])
        ids = [r["comment_id"] for group in groups.values() for r in group]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(actual, self.payload(rows))

    def test_invalid_semantic_labels_and_observation_count_rejected(self):
        for changes in ({"market_stance": "invalid"}, {"emotion": ["neutral", "neutral"]}, {"observation_count": 0}, {"depth": -1}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.payload([self.row(**changes)])

    def test_collection_metadata_does_not_change_formulas_or_input(self):
        dataset = {"records": [self.row()], "model_requested": "test-model", "source": {"sha256": "test"}}
        collection = {"scope": {"max_roots": 1}, "warnings": ["limited"]}
        before = copy.deepcopy((dataset, collection))
        actual = build_payload(dataset, {"oid": "900001", "bvid": "BV1234567890"}, collection, "bounded")
        self.assertEqual((dataset, collection), before)
        self.assertEqual(actual["analysis"]["model"], "test-model")
        self.assertEqual(actual["analysis"]["scope"], {"max_roots": 1})
        self.assertEqual(actual["headline"]["bfsi"], 100)
        self.assertEqual(actual["source"]["hash_basis"], "canonical UTF-8 JSON; sorted keys; compact separators")
        json.dumps(actual, ensure_ascii=False, allow_nan=False)


if __name__ == "__main__":
    unittest.main(verbosity=2)
