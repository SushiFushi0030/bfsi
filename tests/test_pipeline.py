"""One offline pipeline from synthetic HTTP pages to verified semantic records and PNG.

Only transport/model process boundaries are replaced. Cleaner, schemas, persistence,
resume validation, index math and image rendering all run normally.
"""
from contextlib import ExitStack
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

from bfsi.collection import collector
from bfsi.transform import cleaner
from bfsi.semantic import runner as semantic
from bfsi.index.calculator import build_payload
from bfsi.reporting.poster import export_poster
import test_ingest as fixtures


class PipelineTests(unittest.TestCase):
    def test_collect_to_poster_with_frozen_input_and_model_resume(self):
        with tempfile.TemporaryDirectory() as temporary, ExitStack() as stack:
            data_dir = Path(temporary).resolve()
            runs = data_dir / "runs"
            run_id = "20261025T000000Z_01020304"
            folder = runs / run_id
            self.calls = []
            for module in (collector, cleaner):
                stack.enter_context(patch.object(module, "PROJECT", data_dir))
                stack.enter_context(patch.object(module, "RUNS", runs))
            stack.enter_context(patch.object(collector, "_legacy", return_value=fixtures.FakeLegacy()))
            stack.enter_context(patch.object(collector.time, "sleep"))
            stack.enter_context(patch.object(collector, "_request", side_effect=lambda url: fixtures.ProductionIngestTests.request(self, url)))

            video = fixtures.video()
            manifest, collection = collector.collect(video, folder, limit=None, mode="full")
            standard = cleaner.clean(video, manifest, folder)
            self.assertTrue(collection["complete"])
            self.assertEqual(len(standard["comments"]), 5)
            self.assertEqual(len(self.calls), 4)
            expected_ids = {"900101", "900102", "900103", "900201", "900202"}
            self.assertEqual({row["core"]["comment_id"] for row in standard["comments"]}, expected_ids)
            observed = sum(len(row["observations"]) for row in standard["comments"])
            self.assertGreater(observed, len(expected_ids))
            self.assertTrue(json.loads((folder / "cleaning_summary.json").read_text())["full_data_integrity"])

            def model_process(command, *, input, stdout, stderr, env, timeout):
                # This executable is never launched; the fixture supplies only the
                # external process's text output and normal completion event stream.
                self.assertEqual(command[0], sys.executable)
                self.assertNotIn("OPENAI_API_KEY", env)
                inputs = json.loads(input.decode("utf-8").split("\nINPUT_ITEMS\n", 1)[1])
                rows = []
                for item in inputs:
                    cid = item["current_comment"]["comment_id"]
                    stance = "bearish" if cid == "900103" else "mixed" if cid == "900202" else "bullish"
                    rows.append({"comment_id": cid, "relevance": "financial", "targets": [],
                        "market_stance": stance, "emotion": ["neutral"], "action_intent": "wait",
                        "creator_attitude": "not_applicable", "sarcasm": "no", "confidence": "high",
                        "summary": "合成离线测试标签：" + stance})
                response = json.dumps(rows, ensure_ascii=False)
                Path(command[command.index("-o") + 1]).write_text(response, encoding="utf-8")
                events = [{"type": "item.completed", "item": {"type": "agent_message", "text": response}},
                          {"type": "turn.completed", "usage": {"input_tokens": 1, "output_tokens": 1}}]
                for event in events:
                    stdout.write((json.dumps(event) + "\n").encode("utf-8"))
                return subprocess.CompletedProcess(command, 0)

            stack.enter_context(patch.dict("os.environ", {"BFSI_CODEX_CLI": sys.executable}))
            process = stack.enter_context(patch.object(semantic.subprocess, "run", side_effect=model_process))
            dataset = semantic.analyze(standard, video, folder, model="offline-synthetic-process")
            self.assertEqual(process.call_count, 1)
            self.assertEqual({row["comment_id"] for row in dataset["records"]}, expected_ids)
            self.assertEqual(dataset["record_count"], 5)
            parent_reply = next(row for row in dataset["records"] if row["comment_id"] == "900103")
            self.assertEqual(parent_reply["parent_comment_id"], "900102")
            self.assertEqual(parent_reply["depth"], 2)
            self.assertEqual(semantic.analyze(standard, video, folder, model="offline-synthetic-process"), dataset)
            self.assertEqual(process.call_count, 1, "completed semantic batches must be reused")

            payload = build_payload(dataset, video, collection, run_id)
            payload["analysis"].update(mode="full", scope="full_accessible", completed_at=fixtures.STAMP, semantic_complete=True)
            self.assertEqual(payload["headline"]["bfsi"], 70)
            self.assertEqual(payload["headline"]["signal_coverage"], 100)
            self.assertEqual(payload["headline"]["disagreement"], 50)
            self.assertEqual(payload["video"]["comment_count"], 5)
            self.assertEqual(payload["video"]["observation_count"], observed)
            (folder / "payload.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            poster = export_poster(payload, folder)
            with Image.open(poster["path"]) as image:
                self.assertEqual(image.width, 1080)
                self.assertGreater(image.height, 2000)
                self.assertEqual(image.info["BFSI run_id"], run_id)
            export_manifest = json.loads((folder / "exports/manifest.json").read_text())
            self.assertEqual(export_manifest["different_comments"], 5)
            self.assertEqual(export_manifest["headline"]["bfsi"], 70)
            self.assertEqual(export_manifest["observations"], observed)


if __name__ == "__main__":
    unittest.main()
