"""Offline integration and failure tests; never call a model from this suite."""
import copy
import json
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch

from bfsi.semantic import runner as semantic
from bfsi.control import CooperativePause


VIDEO = {"bvid": "BV1234567890", "oid": "98765", "title": "真实标题保留测试", "url": "https://www.bilibili.com/video/BV1234567890"}


def standard(count=2):
    rows = []
    for n in range(count):
        rows.append({"core": {"comment_id": str(100 + n), "video_bvid": VIDEO["bvid"],
                     "video_oid": VIDEO["oid"], "raw_text": f"第 {n} 条测试评论", "depth": 0 if n == 0 else 1,
                     "parent_comment_id": None if n == 0 else "100", "root_comment_id": None if n == 0 else "100",
                     "published_at": "2026-10-23T00:00:00Z"}, "observations": [{"has_picture": False}]})
    return {"schema_version": "0.1", "comments": rows}


def semantic_row(cid):
    return {"comment_id": cid, "relevance": "unclear", "targets": [], "market_stance": "unclear",
            "emotion": ["neutral"], "action_intent": "unclear", "creator_attitude": "unclear",
            "sarcasm": "unclear", "confidence": "low", "summary": "仅用于离线测试的受控模型响应"}


def fake_cli(malformed=False, tool=False, wrong_id=False):
    def run(command, *, input, stdout, stderr, env, timeout):
        assert "OPENAI_API_KEY" not in env and "CODEX_API_KEY" not in env
        assert "--ephemeral" in command and command[command.index("--sandbox") + 1] == "read-only"
        batch = json.loads(input.decode("utf-8").split("\nINPUT_ITEMS\n", 1)[1])
        rows = [semantic_row(item["current_comment"]["comment_id"]) for item in batch]
        if wrong_id:
            rows[0]["comment_id"] = "other"
        raw = "not JSON" if malformed else json.dumps(rows, ensure_ascii=False)
        Path(command[command.index("-o") + 1]).write_text(raw)
        events = [{"type": "item.completed", "item": {"type": "agent_message", "text": raw}},
                  {"type": "turn.completed", "usage": {"input_tokens": 42, "output_tokens": 13}}]
        if tool:
            events.insert(0, {"type": "item.completed", "item": {"type": "command_execution", "command": "forbidden"}})
        for event in events:
            stdout.write((json.dumps(event) + "\n").encode())
        return subprocess.CompletedProcess(command, 0)
    return run


class SemanticTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def analyze(self, fake, data=None, **kwargs):
        options = {"model": "explicit-test-model", **kwargs}
        with patch.object(semantic, "_cli", return_value="mock-codex"), patch.object(semantic.subprocess, "run", side_effect=fake) as runner:
            result = semantic.analyze(data or standard(), VIDEO, self.path, **options)
        return result, runner

    def attempts(self):
        return [p for p in (self.path / "semantic/meta").glob("*.json") if not p.name.endswith(".started.json")]

    def test_success_binds_source_context_and_raw_response(self):
        source = standard()
        original = copy.deepcopy(source)
        dataset, runner = self.analyze(fake_cli(), source)
        self.assertEqual(runner.call_count, 1)
        self.assertEqual(source, original)
        self.assertEqual(dataset["record_count"], 2)
        self.assertEqual(dataset["model_requested"], "explicit-test-model")
        reply = dataset["records"][1]
        self.assertEqual(reply["context_used"]["video"]["title"], VIDEO["title"])
        self.assertEqual(reply["context_used"]["ancestor_chain"][0]["raw_text"], source["comments"][0]["core"]["raw_text"])
        self.assertEqual(reply["source_ref"], {"file": "standard_comments.json", "json_pointer": "/comments/1"})
        folder = self.path / "semantic"
        saved = json.loads((folder / "full_semantic_dataset.json").read_text())
        self.assertEqual(saved, dataset)
        meta = json.loads((folder / "meta/B001.attempt1.json").read_text())
        self.assertTrue(meta["success"])
        self.assertEqual(meta["token_usage"]["input_tokens"], 42)
        self.assertTrue((folder / meta["raw_response_file"]).is_file())

    def test_malformed_first_attempt_retries_and_keeps_both(self):
        calls = []
        def run(*args, **kwargs):
            calls.append(1)
            return fake_cli(malformed=len(calls) == 1)(*args, **kwargs)
        dataset, runner = self.analyze(run)
        self.assertEqual(runner.call_count, 2)
        self.assertEqual(dataset["record_count"], 2)
        self.assertEqual(len(self.attempts()), 2)
        self.assertEqual((self.path / "semantic/raw/B001.attempt1.txt").read_text(), "not JSON")

    def test_failed_responses_never_publish_dataset(self):
        with self.assertRaises(semantic.SemanticError):
            self.analyze(fake_cli(malformed=True))
        self.assertFalse((self.path / "semantic/full_semantic_dataset.json").exists())
        self.assertEqual(len(self.attempts()), 2)
        manifest = json.loads((self.path / "semantic/checkpoint.json").read_text())
        self.assertEqual(manifest["status"], "failed")

    def test_tool_event_is_rejected_even_with_valid_output(self):
        with self.assertRaisesRegex(semantic.SemanticError, "非文本工具事件"):
            self.analyze(fake_cli(tool=True))
        self.assertFalse((self.path / "semantic/full_semantic_dataset.json").exists())

    def test_wrong_comment_id_is_rejected(self):
        with self.assertRaisesRegex(semantic.SemanticError, "ID"):
            self.analyze(fake_cli(wrong_id=True))

    def test_incomplete_parent_chain_fails_before_model(self):
        data = standard()
        data["comments"][1]["core"]["parent_comment_id"] = "missing"
        with patch.object(semantic.subprocess, "run") as runner:
            with self.assertRaisesRegex(semantic.SemanticError, "父链"):
                semantic.analyze(data, VIDEO, self.path)
        runner.assert_not_called()

    def test_multiple_batches_preserve_input_order_and_progress(self):
        events = []
        with patch.object(semantic, "_cli", return_value="mock-codex"), patch.object(semantic.subprocess, "run", side_effect=fake_cli()) as runner:
            dataset = semantic.analyze(standard(12), VIDEO, self.path, progress=events.append)
        self.assertEqual(runner.call_count, 2)
        self.assertEqual([r["comment_id"] for r in dataset["records"]], [str(100 + n) for n in range(12)])
        self.assertEqual(events[-1]["analyzed"], 12)

    def test_completed_run_is_idempotent_and_never_calls_model_again(self):
        first, _ = self.analyze(fake_cli())
        protected = [p for p in (self.path / "semantic").rglob("*") if p.is_file()
                     and p.name not in ["checkpoint.json", ".analysis.lock"] and "sessions" not in p.parts]
        before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in protected}
        second, runner = self.analyze(fake_cli())
        runner.assert_not_called()
        self.assertEqual(first, second)
        self.assertEqual(before, {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in protected})

    def test_strict_parser_rejects_duplicate_and_non_json_numbers(self):
        for raw in ['{"x":1,"x":2}', '{"x":NaN}', '{"x":Infinity}']:
            with self.assertRaises(ValueError):
                semantic._strict_json(raw)

    def test_pause_stops_new_batches_and_restart_reuses_completed_work(self):
        paused = threading.Event()
        def control():
            if paused.is_set():
                raise CooperativePause()
        def progress(value):
            if value["batches_done"] >= 1:
                paused.set()
        with patch.object(semantic, "_cli", return_value="mock-codex"), patch.object(semantic.subprocess, "run", side_effect=fake_cli()) as runner:
            with self.assertRaises(CooperativePause):
                semantic.analyze(standard(51), VIDEO, self.path, model="explicit-test-model", control=control, progress=progress)
        self.assertGreaterEqual(runner.call_count, 1)
        self.assertLessEqual(runner.call_count, 2)
        checkpoint = json.loads((self.path / "semantic/checkpoint.json").read_text())
        self.assertEqual(checkpoint["status"], "paused")
        self.assertIn(checkpoint["analyzed"], [10, 20])
        self.assertFalse((self.path / "semantic/full_semantic_dataset.json").exists())
        done_before = checkpoint["batches_done"]
        resumed_progress = []
        dataset, resumed = self.analyze(fake_cli(), standard(51), progress=resumed_progress.append)
        self.assertEqual(resumed.call_count, 6 - done_before)
        self.assertEqual(resumed_progress[0]["reused_batches"], done_before)
        self.assertEqual(dataset["record_count"], 51)

    def test_pause_before_start_sends_no_model_request(self):
        def stop():
            raise CooperativePause("用户暂停", reason="user")
        with patch.object(semantic.subprocess, "run") as runner:
            with self.assertRaises(CooperativePause):
                semantic.analyze(standard(), VIDEO, self.path, control=stop)
        runner.assert_not_called()
        checkpoint = json.loads((self.path / "semantic/checkpoint.json").read_text())
        self.assertEqual(checkpoint["status"], "paused")
        self.assertEqual(checkpoint["analyzed"], 0)
        self.assertFalse((self.path / "semantic/run_manifest.json").exists())

    def test_control_is_checked_before_retry(self):
        paused = threading.Event()
        def control():
            if paused.is_set():
                raise CooperativePause()
        def first_fails(*args, **kwargs):
            result = fake_cli(malformed=True)(*args, **kwargs)
            paused.set()
            return result
        with self.assertRaises(CooperativePause):
            self.analyze(first_fails, control=control)
        self.assertEqual(len(self.attempts()), 1)
        self.assertEqual(json.loads((self.path / "semantic/checkpoint.json").read_text())["status"], "paused")

    def test_failed_attempts_are_retained_and_new_invocation_can_try_again(self):
        with self.assertRaises(semantic.SemanticError):
            self.analyze(fake_cli(malformed=True))
        protected = {p: p.read_bytes() for p in self.attempts()}
        dataset, runner = self.analyze(fake_cli())
        self.assertEqual(runner.call_count, 1)
        self.assertEqual(len(self.attempts()), 3)
        self.assertTrue((self.path / "semantic/meta/B001.attempt3.json").is_file())
        self.assertEqual(protected, {p: p.read_bytes() for p in protected})
        self.assertEqual(dataset["record_count"], 2)

    def test_quota_error_pauses_instead_of_retrying_or_publishing(self):
        calls = []
        def quota(command, *, input, stdout, stderr, env, timeout):
            calls.append(command)
            stderr.write(b"HTTP 429 rate_limit_exceeded: usage limit reached")
            stdout.write((json.dumps({"type": "turn.failed", "error": {"code": "rate_limit_exceeded"}}) + "\n").encode())
            return subprocess.CompletedProcess(command, 1)
        with self.assertRaises(CooperativePause) as caught:
            self.analyze(quota)
        self.assertEqual(caught.exception.reason, "resource_limit")
        self.assertEqual(len(calls), 1)
        checkpoint = json.loads((self.path / "semantic/checkpoint.json").read_text())
        self.assertEqual(checkpoint["status"], "paused")
        self.assertEqual(checkpoint["pause_reason"], "resource_limit")
        self.assertFalse((self.path / "semantic/full_semantic_dataset.json").exists())
        dataset, runner = self.analyze(fake_cli())
        self.assertEqual(runner.call_count, 1)
        self.assertEqual(dataset["record_count"], 2)
        self.assertTrue((self.path / "semantic/meta/B001.attempt2.json").is_file())

    def test_changed_model_or_standard_is_rejected_without_spending_again(self):
        self.analyze(fake_cli())
        changed = standard()
        changed["comments"][0]["core"]["raw_text"] += "changed"
        for options in [{"model": "a-different-model"}, {"data": changed}]:
            with patch.object(semantic.subprocess, "run") as runner:
                with self.assertRaisesRegex(semantic.SemanticError, "合同"):
                    self.analyze(fake_cli(), **options)
            runner.assert_not_called()

    def test_changed_source_file_bytes_are_rejected_even_for_same_json(self):
        data = standard()
        source = self.path / "standard_comments.json"
        source.write_text(json.dumps(data, ensure_ascii=False))
        self.analyze(fake_cli(), data)
        source.write_text(json.dumps(data, ensure_ascii=False, indent=2))
        with self.assertRaisesRegex(semantic.SemanticError, "合同"):
            self.analyze(fake_cli(), data)

    def test_changed_prompt_or_schema_cannot_reuse_old_batches(self):
        original = semantic.RESOURCES
        resources = self.path / "resources"
        resources.mkdir()
        for name in [semantic.PROMPT_FILE, semantic.SCHEMA_FILE]:
            (resources / name).write_bytes((original / name).read_bytes())
        with patch.object(semantic, "RESOURCES", resources):
            self.analyze(fake_cli())
            prompt = resources / semantic.PROMPT_FILE
            prompt.write_text(prompt.read_text() + "\nchanged")
            with self.assertRaisesRegex(semantic.SemanticError, "合同"):
                self.analyze(fake_cli())
            prompt.write_bytes((original / semantic.PROMPT_FILE).read_bytes())
            schema_file = resources / semantic.SCHEMA_FILE
            schema_file.write_text(schema_file.read_text() + "\n")
            with self.assertRaisesRegex(semantic.SemanticError, "合同"):
                self.analyze(fake_cli())

    def test_tampered_raw_or_events_are_rejected_without_reinference(self):
        self.analyze(fake_cli())
        raw = self.path / "semantic/raw/B001.attempt1.txt"
        original_raw = raw.read_bytes()
        raw.write_bytes(original_raw + b" ")
        with self.assertRaisesRegex(semantic.SemanticError, "哈希"):
            self.analyze(fake_cli())
        raw.write_bytes(original_raw)
        events = self.path / "semantic/events/B001.attempt1.jsonl"
        events.write_bytes(events.read_bytes() + b"\n")
        with self.assertRaisesRegex(semantic.SemanticError, "哈希"):
            self.analyze(fake_cli())

    def test_over_200_comments_are_all_batched_without_a_cap(self):
        progress = []
        dataset, runner = self.analyze(fake_cli(), standard(231), progress=progress.append)
        self.assertEqual(runner.call_count, 24)
        self.assertEqual(dataset["record_count"], 231)
        self.assertEqual(len({r["comment_id"] for r in dataset["records"]}), 231)
        self.assertEqual(progress[-1]["batches_done"], 24)
        self.assertEqual(progress[-1]["batches_total"], 24)
        self.assertEqual(progress[-1]["analyzed"], 231)
        self.assertEqual(progress[-1]["total"], 231)

    def test_legacy_complete_run_can_be_verified_without_overwrite_or_model(self):
        first, _ = self.analyze(fake_cli())
        folder = self.path / "semantic"
        (folder / "contract.json").unlink()
        (folder / "checkpoint.json").unlink()
        meta_path = folder / "meta/B001.attempt1.json"
        meta = json.loads(meta_path.read_text())
        meta.pop("events_file")
        meta.pop("events_sha256")
        meta_path.write_text(json.dumps(meta))
        original = {p: p.read_bytes() for p in [meta_path, folder / "run_manifest.json", folder / "full_semantic_dataset.json"]}
        second, runner = self.analyze(fake_cli())
        runner.assert_not_called()
        self.assertEqual(first, second)
        self.assertEqual(original, {p: p.read_bytes() for p in original})

    def test_non_directional_neutral_output_is_a_valid_success(self):
        def neutral(*args, **kwargs):
            result = fake_cli()(*args, **kwargs)
            command = args[0]
            raw = Path(command[command.index("-o") + 1])
            rows = json.loads(raw.read_text())
            for row in rows:
                row["market_stance"] = "neutral"
            raw_text = json.dumps(rows)
            raw.write_text(raw_text)
            kwargs["stdout"].seek(0)
            kwargs["stdout"].truncate()
            for event in [{"type": "item.completed", "item": {"type": "agent_message", "text": raw_text}}, {"type": "turn.completed"}]:
                kwargs["stdout"].write((json.dumps(event) + "\n").encode())
            return result
        dataset, _ = self.analyze(neutral)
        self.assertTrue(all(r["market_stance"] == "neutral" for r in dataset["records"]))


if __name__ == "__main__":
    unittest.main()
