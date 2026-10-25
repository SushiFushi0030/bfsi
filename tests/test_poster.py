"""Offline poster identity/content/cache tests; all outputs use isolated folders."""
import copy
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

from PIL import Image, ImageDraw, ImageFont

from bfsi.reporting.poster import export_poster
ROOT = Path(__file__).resolve().parents[1]
REAL_RUNS = ("20261025T000000Z_00000001", "20261025T000000Z_00000002")
sha = lambda data: hashlib.sha256(data).hexdigest()


class PosterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.addCleanup(self.temp.cleanup)

    def sample(self, run_id=REAL_RUNS[0]):
        payload = json.loads((ROOT / "examples/demo/payload.json").read_text(encoding="utf-8"))
        payload["analysis"]["run_id"] = run_id
        payload["source"]["run_id"] = run_id
        payload.setdefault("metadata", {})["run_id"] = run_id
        payload["video"]["title"] += run_id[-1]
        return payload

    def persist(self, payload):
        folder = self.root / payload["analysis"]["run_id"]
        folder.mkdir()
        (folder / "payload.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return folder

    def manifest(self, result):
        return json.loads((Path(result["path"]).parent / "manifest.json").read_text(encoding="utf-8"))

    def test_two_synthetic_runs_separate_and_content_matches(self):
        outputs = []
        for run_id in REAL_RUNS:
            payload = self.sample(run_id)
            folder = self.persist(payload)
            before = (folder / "payload.json").read_bytes()
            result = export_poster(payload, folder)
            manifest = self.manifest(result)
            self.assertEqual(manifest["run_id"], run_id)
            self.assertEqual(manifest["title"], payload["video"]["title"])
            self.assertEqual(manifest["bvid"], payload["video"]["video_bvid"])
            self.assertEqual(manifest["different_comments"], payload["video"]["comment_count"])
            self.assertEqual(manifest["observations"], payload["video"]["observation_count"])
            self.assertEqual(manifest["headline"], payload["headline"])
            self.assertEqual(manifest["stance_counts"], payload["distributions"]["market_stance"])
            self.assertEqual(manifest["warnings"], payload["analysis"]["collection"]["warnings"])
            original_comments = {r["comment_id"]: r for rows in payload["representative_comments"].values() for r in rows}
            self.assertLessEqual(len(manifest["representative_comments"]), 5)
            for row in manifest["representative_comments"]:
                self.assertEqual(row["raw_text"], original_comments[row["comment_id"]]["raw_text"])
                self.assertEqual(row["summary"], original_comments[row["comment_id"]]["summary"])
            with Image.open(result["path"]) as image:
                self.assertEqual(image.size, (1080, result["height"]))
                self.assertGreater(result["height"], 2000)
                self.assertEqual(image.info["BFSI run_id"], run_id)
                self.assertEqual(image.info["BFSI payload_sha256"], result["payload_sha256"])
            self.assertEqual(sha(Path(result["path"]).read_bytes()), result["sha256"])
            self.assertEqual((folder / "payload.json").read_bytes(), before)
            outputs.append(result)
        self.assertNotEqual(outputs[0]["path"], outputs[1]["path"])
        self.assertNotEqual(outputs[0]["sha256"], outputs[1]["sha256"])

    def test_idempotent_cache_preserves_file_mtime(self):
        payload = self.sample()
        folder = self.persist(payload)
        first = export_poster(payload, folder)
        paths = [Path(first["path"]), folder / "exports/manifest.json"]
        before = [p.stat().st_mtime_ns for p in paths]
        self.assertEqual(export_poster(payload, folder), first)
        self.assertEqual([p.stat().st_mtime_ns for p in paths], before)

    def test_payload_and_run_identity_mismatch_rejected(self):
        payload = self.sample()
        folder = self.persist(payload)
        for group in ("analysis", "source", "metadata"):
            changed = copy.deepcopy(payload)
            changed.setdefault(group, {})["run_id"] = REAL_RUNS[1]
            with self.subTest(group=group), self.assertRaises(ValueError):
                export_poster(changed, folder)
        changed = copy.deepcopy(payload)
        changed["headline"]["bfsi"] += 1
        with self.assertRaisesRegex(ValueError, "payload.json"):
            export_poster(changed, folder)
        self.assertFalse((folder / "exports").exists())

    def test_changed_persisted_payload_cannot_overwrite_existing_export(self):
        payload = self.sample()
        folder = self.persist(payload)
        first = export_poster(payload, folder)
        changed = copy.deepcopy(payload)
        changed["video"]["title"] += "（变更）"
        (folder / "payload.json").write_text(json.dumps(changed, ensure_ascii=False), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "拒绝覆盖"):
            export_poster(changed, folder)
        self.assertEqual(sha(Path(first["path"]).read_bytes()), first["sha256"])

    def test_full_partial_and_zero_signals_are_not_valid_extreme_scores(self):
        payload = self.sample()
        payload["analysis"].update(mode="full", scope="partial")
        payload["analysis"]["warnings"] = ["标准化隔离了缺少父链的评论"]
        payload["analysis"]["collection"].update(complete=False, partial=True)
        n = payload["video"]["comment_count"]
        payload["headline"].update(bfsi=50, signal_coverage=0, disagreement=0,
                                   no_directional_signal=True, no_bullish_bearish_signal=True)
        payload["distributions"]["market_stance"] = {k: (n if k == "not_applicable" else 0) for k in payload["distributions"]["market_stance"]}
        payload["distribution_percentages"]["market_stance"] = {k: (100 if k == "not_applicable" else 0) for k in payload["distributions"]["market_stance"]}
        payload["representative_comments"] = {}
        result = export_poster(payload, self.persist(payload))
        manifest = self.manifest(result)
        text = "\n".join(block["text"] for block in manifest["text_blocks"])
        self.assertTrue(manifest["partial"])
        self.assertIn("全量分析", manifest["mode_label"])
        self.assertIn("部分结果", manifest["range_label"])
        self.assertIn("暂无方向信号", text)
        self.assertIn("无多空比较样本", text)
        self.assertIn("50 为约定占位值", text)
        self.assertNotIn("50.0", [block["text"] for block in manifest["text_blocks"]])
        self.assertEqual(manifest["representative_comments"], [])
        self.assertIn("标准化隔离了缺少父链的评论", manifest["warnings"])
        self.assertIn("标准化隔离了缺少父链的评论", " ".join(manifest["scope_notes"]))

    def test_complete_full_uses_accessible_scope_not_platform_total(self):
        payload = self.sample()
        payload["analysis"].update(mode="full", scope="full_accessible")
        payload["analysis"]["collection"].update(complete=True, partial=False)
        result = export_poster(payload, self.persist(payload))
        manifest = self.manifest(result)
        self.assertEqual(manifest["range_label"], "已完成可访问评论范围")
        self.assertIn("全量仅指此次接口可访问范围", " ".join(manifest["scope_notes"]))

    def test_long_text_truncates_with_markers_and_all_text_stays_in_bounds(self):
        payload = self.sample()
        payload["video"]["title"] = "很长的中文标题ABC " * 80
        row = next(r for values in payload["representative_comments"].values() for r in values)
        row["raw_text"], row["summary"] = "很长原文 ABC " * 200, "很长摘要 XYZ " * 200
        result = export_poster(payload, self.persist(payload))
        manifest = self.manifest(result)
        self.assertTrue(manifest["title_truncated"])
        chosen = next(r for r in manifest["representative_comments"] if r["comment_id"] == row["comment_id"])
        self.assertTrue(chosen["raw_truncated"])
        self.assertTrue(chosen["summary_truncated"])
        self.assertIn("〔原文截断〕", chosen["rendered_raw_text"])
        self.assertIn("〔摘要截断〕", chosen["rendered_summary"])
        draw = ImageDraw.Draw(Image.new("RGB", (1, 1)))
        for block in manifest["text_blocks"]:
            self.assertGreaterEqual(block["x"], 0)
            self.assertLessEqual(block["x"] + block["width"], result["width"])
            self.assertLessEqual(block["y"] + block["height"], result["height"])
            font = ImageFont.truetype(manifest["font_path"], block["font_size"])
            for line in block["text"].split("\n"):
                self.assertLessEqual(draw.textlength(line, font=font), block["width"] + 0.1)

    def test_symlink_export_directory_and_corrupt_cache_rejected(self):
        payload = self.sample()
        folder = self.persist(payload)
        destination = self.root / "unrelated"
        destination.mkdir()
        (folder / "exports").symlink_to(destination, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "符号链接"):
            export_poster(payload, folder)
        self.assertEqual(list(destination.iterdir()), [])
        (folder / "exports").unlink()
        result = export_poster(payload, folder)
        Path(result["path"]).write_bytes(b"corrupt")
        with self.assertRaisesRegex(ValueError, "完整性"):
            export_poster(payload, folder)


if __name__ == "__main__":
    unittest.main(verbosity=2)
