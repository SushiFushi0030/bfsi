"""Real, bounded Codex text inference with frozen Bulldozer semantic fields.

Every run owns its artifacts. No historical Task output is read or overwritten.
"""
from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import threading
import time

from jsonschema import Draft202012Validator

from ..control import CooperativePause
from ..config import RESOURCES


SCHEMA_FILE = "simplified_semantic_schema_v0.1.json"
PROMPT_FILE = "bulldozer_prompt_v0.1.md"
DEFAULT_MODEL = "gpt-6-astra"
BATCH_SIZE = 10
MAX_ATTEMPTS = 2
TIMEOUT_SECONDS = 900


class SemanticError(RuntimeError):
    """A real model response or its provenance failed validation."""


def _now():
    return datetime.now(timezone.utc).isoformat()


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _save(path, value):
    """Exclusive writes prevent replacing another run or a previous attempt."""
    with path.open("x", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write("\n")


def _strict_json(text):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("Duplicate JSON key: " + key)
            result[key] = value
        return result

    def constant(value):
        raise ValueError("Non-JSON constant: " + value)

    return json.loads(text, object_pairs_hook=pairs, parse_constant=constant)


def _cli():
    configured = os.environ.get("BFSI_CODEX_CLI")
    candidates = [configured] if configured else [
        shutil.which("codex"),
        "/Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    raise SemanticError("未找到可执行的 Codex CLI；请配置 BFSI_CODEX_CLI。")


def _inputs(standard, video):
    comments = standard.get("comments")
    if not isinstance(comments, list) or not comments:
        raise SemanticError("没有可供语义分析的标准评论。")
    byid = {}
    for comment in comments:
        core = comment["core"]
        cid = core["comment_id"]
        if not isinstance(cid, str) or not cid or cid in byid:
            raise SemanticError("标准评论 ID 为空或重复。")
        if str(core["video_oid"]) != str(video["oid"]):
            raise SemanticError("评论与目标视频 OID 不一致。")
        if core.get("video_bvid") not in [None, video["bvid"]]:
            raise SemanticError("评论与目标视频 BV 号不一致。")
        byid[cid] = comment

    def view(comment):
        core = comment["core"]
        return {"comment_id": core["comment_id"], "raw_text": core["raw_text"],
                "parent_comment_id": core["parent_comment_id"],
                "root_comment_id": core["root_comment_id"], "depth": core["depth"],
                "has_picture": any(o.get("has_picture") for o in comment["observations"]),
                "picture_content": None}

    items = []
    for comment in comments:
        core = comment["core"]
        chain, seen = [], {core["comment_id"]}
        parent = core["parent_comment_id"]
        while parent is not None:
            if parent not in byid or parent in seen:
                raise SemanticError("评论父链缺失或存在循环：" + core["comment_id"])
            seen.add(parent)
            ancestor = byid[parent]
            chain.insert(0, view(ancestor))
            parent = ancestor["core"]["parent_comment_id"]
        if len(chain) != core["depth"] or (chain and chain[0]["comment_id"] != core["root_comment_id"]):
            raise SemanticError("评论 depth/root 与实际父链不一致：" + core["comment_id"])
        items.append({"current_comment": view(comment), "context": {
            "direct_parent": chain[-1] if chain else None,
            "root": chain[0] if chain else None, "ancestor_chain": chain,
            "video": {"video_oid": str(video["oid"]), "video_bvid": video["bvid"],
                      "title": video.get("title"), "topic": video.get("description"),
                      "creator_name": video.get("creator_name"), "transcript": None,
                      "url": video.get("url")}}})
    return items


def _check(raw, ids, validator):
    output = _strict_json(raw)
    if not isinstance(output, list):
        raise SemanticError("模型输出必须是 JSON 数组。")
    if len(output) != len(ids):
        raise SemanticError("模型输出数量与该批评论数量不一致。")
    errors = []
    for position, row in enumerate(output):
        errors.extend(f"row {position} {error.json_path}: {error.message}"
                      for error in validator.iter_errors(row))
    if errors:
        raise SemanticError("模型输出 Schema 校验失败：" + "; ".join(errors[:10]))
    if [row["comment_id"] for row in output] != ids:
        raise SemanticError("模型输出评论 ID 或顺序不匹配。")
    return output


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False).encode("utf-8")


def _read(path):
    return _strict_json(path.read_text(encoding="utf-8"))


def _atomic(path, value):
    """Only the mutable checkpoint is replaced; attempt evidence is append-only."""
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def _publish(path, value, staging):
    """Publish a complete JSON file atomically without replacing an existing one."""
    _save(staging, value)
    try:
        os.link(staging, path)
    finally:
        staging.unlink()


def _file(folder, relative):
    path = (folder / relative).resolve()
    if not path.is_relative_to(folder.resolve()):
        raise SemanticError("语义产物路径超出本次运行目录。")
    return path


@contextmanager
def _run_lock(folder):
    with (folder / ".analysis.lock").open("a+") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise SemanticError("同一运行的语义分析正在执行，请等待或暂停后再继续。") from exc
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _event_evidence(events):
    rows, notices, types, finals, usage, completed = [], [], [], [], None, False
    for line in events.read_text(encoding="utf-8").splitlines():
        try:
            row = _strict_json(line)
            if not isinstance(row, dict):
                raise ValueError("Event is not an object")
            rows.append(row)
        except ValueError:
            notices.append("Non-JSON CLI event")
    for event in rows:
        if event.get("type") == "turn.completed":
            completed, usage = True, event.get("usage")
        if event.get("type") in ["turn.failed", "error"]:
            notices.append(event)
        if "item" in event:
            item = event["item"]
            types.append(item.get("type"))
            if item.get("type") == "agent_message" and event.get("type") == "item.completed":
                finals.append(item.get("text", ""))
            if item.get("type") == "error":
                notices.append(item)
    unexpected = sorted({t for t in types if t not in ["agent_message", "reasoning", "error"]}, key=str)
    return {"completed": completed, "usage": usage, "notices": notices,
            "types": sorted(set(types), key=str), "finals": finals, "unexpected": unexpected}


def _verify_success(batch, folder, meta, model, validator):
    for key in ["batch_id", "comment_ids", "request_sha256"]:
        if meta.get(key) != batch[key]:
            raise SemanticError("已保存成功批次的输入绑定不一致：" + batch["batch_id"])
    if meta.get("model_requested") != model or meta.get("returncode") != 0:
        raise SemanticError("已保存成功批次的模型或退出状态不一致。")
    raw = _file(folder, meta["raw_response_file"])
    if _sha(raw.read_bytes()) != meta.get("raw_response_sha256"):
        raise SemanticError("已保存模型响应的哈希不匹配：" + batch["batch_id"])
    tag = f"{batch['batch_id']}.attempt{meta['attempt']}"
    events = _file(folder, meta.get("events_file", f"events/{tag}.jsonl"))
    if meta.get("events_sha256") and _sha(events.read_bytes()) != meta["events_sha256"]:
        raise SemanticError("已保存推理事件的哈希不匹配：" + batch["batch_id"])
    evidence = _event_evidence(events)
    raw_text = raw.read_text(encoding="utf-8")
    if not evidence["completed"] or evidence["unexpected"] or not evidence["finals"]:
        raise SemanticError("已保存批次没有完整的纯文本推理证据。")
    if evidence["finals"][-1].strip() != raw_text.strip():
        raise SemanticError("已保存推理事件与原始响应不一致。")
    return _check(raw_text, batch["comment_ids"], validator), meta


def _reuse(batch, folder, model, validator):
    successes = []
    for path in sorted((folder / "meta").glob(f"{batch['batch_id']}.attempt*.json")):
        if path.name.endswith(".started.json"):
            continue
        meta = _read(path)
        if meta.get("success"):
            successes.append(_verify_success(batch, folder, meta, model, validator))
    if len(successes) > 1:
        raise SemanticError("同一批次存在多个成功结果，需检查原始运行证据。")
    return successes[0] if successes else None


def _next_attempt(folder, bid):
    numbers = []
    pattern = re.compile(re.escape(bid) + r"\.attempt(\d+)(?:\.|$)")
    for directory in ["meta", "raw", "events"]:
        for path in (folder / directory).glob(f"{bid}.attempt*"):
            match = pattern.match(path.name)
            if match:
                numbers.append(int(match.group(1)))
    return max(numbers, default=0) + 1


def _resource_limited(error, notices, stderr):
    evidence = " ".join([str(error), json.dumps(notices, ensure_ascii=False), stderr])
    return bool(re.search(r"\b429\b|rate[_ -]?limit|insufficient[_ -]quota|quota[_ -]exceeded|usage[_ -]limit", evidence, re.I))


def _run_batch(batch, folder, executable, model, validator, control=None):
    """Two attempts per explicit invocation; attempt numbers never reset on resume."""
    bid = batch["batch_id"]
    request = _file(folder, batch["request_file"]).read_bytes()
    if _sha(request) != batch["request_sha256"]:
        raise SemanticError("请求哈希不匹配：" + bid)
    start_attempt, last_error = _next_attempt(folder, bid), None
    for attempt in range(start_attempt, start_attempt + MAX_ATTEMPTS):
        if control:
            control()
        tag = f"{bid}.attempt{attempt}"
        raw, events = folder / f"raw/{tag}.txt", folder / f"events/{tag}.jsonl"
        stderr, meta_path = folder / f"events/{tag}.stderr.txt", folder / f"meta/{tag}.json"
        command = [executable, "exec", "--ignore-user-config", "--ephemeral", "--skip-git-repo-check",
                   "--sandbox", "read-only", "-C", str(folder / "empty"), "-m", model,
                   "-c", 'model_reasoning_effort="low"', "-c", 'web_search="disabled"',
                   "-c", "features.shell_tool=false", "-c", "project_doc_max_bytes=0",
                   "--color", "never", "--json", "-o", str(raw), "-"]
        env = os.environ.copy()
        for key in ["OPENAI_API_KEY", "CODEX_API_KEY"]:
            env.pop(key, None)
        started, clock = _now(), time.monotonic()
        _save(folder / f"meta/{tag}.started.json", {**batch, "attempt": attempt,
              "model_requested": model, "started_at_utc": started, "command": command})
        rc, error, output = None, None, None
        evidence = {"usage": None, "notices": [], "types": []}
        stderr_text = ""
        try:
            with events.open("xb") as stdout_handle, stderr.open("xb") as stderr_handle:
                rc = subprocess.run(command, input=request, stdout=stdout_handle, stderr=stderr_handle,
                                    env=env, timeout=TIMEOUT_SECONDS).returncode
            evidence = _event_evidence(events)
            stderr_text = stderr.read_text(encoding="utf-8", errors="replace")
            if rc != 0:
                raise SemanticError(f"Codex CLI 返回 {rc}：{stderr_text[-1500:]}")
            if not evidence["completed"] or evidence["unexpected"] or not raw.is_file():
                raise SemanticError(f"推理未完整完成或出现非文本工具事件：{evidence['unexpected']}")
            raw_text = raw.read_text(encoding="utf-8")
            if not evidence["finals"] or evidence["finals"][-1].strip() != raw_text.strip():
                raise SemanticError("CLI 最终响应与原始响应文件不一致。")
            output = _check(raw_text, batch["comment_ids"], validator)
        except subprocess.TimeoutExpired:
            error = f"Codex 推理超过 {TIMEOUT_SECONDS} 秒，未确认成功。"
            if events.is_file():
                evidence = _event_evidence(events)
            stderr_text = stderr.read_text(encoding="utf-8", errors="replace") if stderr.is_file() else ""
        except (OSError, ValueError, SemanticError) as exc:
            error = str(exc)
        result = {**batch, "attempt": attempt, "started_at_utc": started, "finished_at_utc": _now(),
                  "process_wall_seconds": round(time.monotonic() - clock, 3), "returncode": rc,
                  "model_requested": model, "model_snapshot_version": "unknown", "command": command,
                  "auth_method": "existing ChatGPT login; API key env removed", "token_usage": evidence["usage"],
                  "cost": "unknown", "item_types_observed": evidence["types"], "transport_notices": evidence["notices"],
                  "error": error, "success": output is not None and error is None,
                  "raw_response_file": str(raw.relative_to(folder)) if raw.is_file() else None,
                  "raw_response_sha256": _sha(raw.read_bytes()) if raw.is_file() else None,
                  "events_file": str(events.relative_to(folder)) if events.is_file() else None,
                  "events_sha256": _sha(events.read_bytes()) if events.is_file() else None}
        _save(meta_path, result)
        if result["success"]:
            return output, result
        if _resource_limited(error, evidence["notices"], stderr_text):
            raise CooperativePause("模型服务返回配额或速率限制；已保存检查点，可在限制恢复后继续。", reason="resource_limit")
        last_error = error
    raise SemanticError(f"{bid} 本次两次真实推理尝试均未通过：{last_error}")


def _prepare(standard, video, folder, model):
    items = _inputs(standard, video)
    schema_bytes, prompt_bytes = (RESOURCES / SCHEMA_FILE).read_bytes(), (RESOURCES / PROMPT_FILE).read_bytes()
    schema = _strict_json(schema_bytes.decode("utf-8"))
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    source_path = folder.parent / "standard_comments.json"
    source_digest = None
    if source_path.is_file():
        source_bytes = source_path.read_bytes()
        if _strict_json(source_bytes.decode("utf-8")) != standard:
            raise SemanticError("传入标准数据与本次运行保存的源文件不一致。")
        source_digest = _sha(source_bytes)
    contract = {"source_canonical_sha256": _sha(_canonical(standard)), "source_sha256": source_digest,
                "video_sha256": _sha(_canonical(video)), "inputs_sha256": _sha(_canonical(items)),
                "schema_sha256": _sha(schema_bytes), "prompt_sha256": _sha(prompt_bytes),
                "model_requested": model, "batch_size": BATCH_SIZE,
                "comment_ids": [i["current_comment"]["comment_id"] for i in items]}
    if (folder / "contract.json").is_file() and _read(folder / "contract.json") != contract:
        raise SemanticError("续跑合同不一致：源数据、视频、Prompt、Schema、模型或批次设置已变化。")
    dataset_path = folder / "full_semantic_dataset.json"
    if dataset_path.is_file():
        previous = _read(dataset_path)
        if previous.get("source", {}).get("canonical_sha256") != contract["source_canonical_sha256"]:
            raise SemanticError("已发布语义结果的源数据哈希不匹配。")
        if previous.get("model_requested") != model:
            raise SemanticError("已发布语义结果的模型与续跑请求不一致。")
        if "sha256" in previous.get("source", {}) and previous["source"]["sha256"] != source_digest:
            raise SemanticError("已发布语义结果的源文件字节哈希不匹配。")
    expected = {SCHEMA_FILE: schema_bytes, PROMPT_FILE: prompt_bytes}
    inputs = {"video": video, "items": items}
    if (folder / "inference_inputs.json").is_file() and _read(folder / "inference_inputs.json") != inputs:
        raise SemanticError("续跑输入上下文与已冻结输入不一致。")
    index = []
    for start in range(0, len(items), BATCH_SIZE):
        bid, batch = f"B{start // BATCH_SIZE + 1:03d}", items[start:start + BATCH_SIZE]
        request = (prompt_bytes.decode("utf-8") + "\n\nOUTPUT_ITEM_JSON_SCHEMA\n" + schema_bytes.decode("utf-8")
                   + "\nINPUT_ITEMS\n" + json.dumps(batch, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        name = f"requests/{bid}.txt"
        expected[name] = request
        index.append({"batch_id": bid, "comment_ids": [i["current_comment"]["comment_id"] for i in batch],
                      "request_file": name, "request_sha256": _sha(request)})
    if (folder / "requests/index.json").is_file() and _read(folder / "requests/index.json") != index:
        raise SemanticError("续跑批次索引与已冻结请求不一致。")
    # Validate all old evidence before filling in any missing preparation files.
    for name, contents in expected.items():
        path = _file(folder, name)
        if path.exists() and path.read_bytes() != contents:
            raise SemanticError("已保存请求或分析资源被修改：" + name)
    for directory in ["requests", "raw", "events", "meta", "empty", "sessions"]:
        (folder / directory).mkdir(exist_ok=True)
    for name, contents in expected.items():
        path = folder / name
        if not path.exists():
            with path.open("xb") as handle:
                handle.write(contents)
    for name, value in [("inference_inputs.json", inputs), ("requests/index.json", index), ("contract.json", contract)]:
        if not (folder / name).exists():
            _save(folder / name, value)
    return items, schema, validator, index, contract


def _assemble(standard, items, results, model, contract):
    byid = {}
    for bid, (rows, meta) in results.items():
        for position, row in enumerate(rows):
            if row["comment_id"] in byid:
                raise SemanticError("语义结果包含重复评论 ID。")
            byid[row["comment_id"]] = (row, bid, meta, position)
    if set(byid) != set(contract["comment_ids"]):
        raise SemanticError("语义结果没有完整覆盖全部标准评论，禁止发布。")
    records = []
    for position, (comment, item) in enumerate(zip(standard["comments"], items)):
        core = comment["core"]
        row, bid, meta, response_index = byid[core["comment_id"]]
        records.append({**row, "raw_text": core["raw_text"], "depth": core["depth"],
                        "parent_comment_id": core["parent_comment_id"], "root_comment_id": core["root_comment_id"],
                        "video_oid": core["video_oid"], "video_bvid": core["video_bvid"], "published_at": core["published_at"],
                        "observation_count": len(comment["observations"]), "context_used": item["context"],
                        "source_ref": {"file": "standard_comments.json", "json_pointer": f"/comments/{position}"},
                        "inference_ref": {"batch_id": bid, "raw_response_file": meta["raw_response_file"], "response_index": response_index}})
    return {"dataset_version": "production_v0.1", "schema_version": "simplified_semantic_v0.1",
            "prompt_version": "bulldozer_prompt_v0.1", "model_requested": model, "record_count": len(records),
            "source": {"file": "standard_comments.json", "sha256": contract["source_sha256"],
                       "canonical_sha256": contract["source_canonical_sha256"], "unique_comments": len(records),
                       "observations": sum(len(c["observations"]) for c in standard["comments"])}, "records": records}


def _analyze_locked(standard, video, folder, progress, model, control):
    items, schema, validator, index, contract = _prepare(standard, video, folder, model)
    started, results = _now(), {}
    for batch in index:
        reused = _reuse(batch, folder, model, validator)
        if reused:
            results[batch["batch_id"]] = reused
    reused_count = len(results)
    old_checkpoint = _read(folder / "checkpoint.json") if (folder / "checkpoint.json").is_file() else {}
    if old_checkpoint.get("contract_sha256") not in [None, _sha(_canonical(contract))]:
        raise SemanticError("语义检查点合同哈希不匹配。")
    session_number = len(list((folder / "sessions").glob("*.started.json"))) + 1
    session = f"session{session_number:04d}"
    _save(folder / f"sessions/{session}.started.json", {"started_at_utc": started,
          "contract_sha256": _sha(_canonical(contract)), "reused_batches": reused_count})

    def checkpoint(status, error=None, reason=None):
        value = {"version": 1, "status": status, "contract_sha256": _sha(_canonical(contract)),
                 "updated_at_utc": _now(), "model_requested": model,
                 "analyzed": sum(len(rows) for rows, _ in results.values()), "total": len(items),
                 "batches_done": len(results), "batches_total": len(index),
                 "successful_batches": [b["batch_id"] for b in index if b["batch_id"] in results],
                 "pending_batches": [b["batch_id"] for b in index if b["batch_id"] not in results],
                 "session": session, "error": error, "pause_reason": reason}
        _atomic(folder / "checkpoint.json", value)
        return value

    def report():
        if progress:
            analyzed = sum(len(rows) for rows, _ in results.values())
            progress({"message": f"已分析 {analyzed}/{len(items)} 条评论 · {len(results)}/{len(index)} 批",
                      "analyzed": analyzed, "total": len(items), "batches_done": len(results),
                      "batches_total": len(index), "reused_batches": reused_count})

    pause_event, pause_lock, pauses = threading.Event(), threading.Lock(), []

    def register_pause(exc):
        with pause_lock:
            if not pauses:
                pauses.append(exc)
            pause_event.set()

    def guard():
        if pause_event.is_set():
            raise pauses[0]
        try:
            if control:
                control()
        except CooperativePause as exc:
            register_pause(exc)
            raise

    def run(batch, executable):
        try:
            return _run_batch(batch, folder, executable, model, validator, control=guard)
        except CooperativePause as exc:
            register_pause(exc)
            raise

    failure = None
    try:
        checkpoint("running")
        report()
        remaining = [b for b in index if b["batch_id"] not in results]
        if remaining:
            guard()
            executable = _cli()
            with ThreadPoolExecutor(max_workers=2) as pool:
                iterator, pending, exhausted = iter(remaining), {}, False
                while pending or not exhausted:
                    while not exhausted and failure is None and not pause_event.is_set() and len(pending) < 2:
                        try:
                            guard()
                            batch = next(iterator, None)
                            if batch is None:
                                exhausted = True
                                break
                            pending[pool.submit(run, batch, executable)] = batch
                        except CooperativePause as exc:
                            register_pause(exc)
                            exhausted = True
                        except Exception as exc:
                            failure, exhausted = exc, True
                    if not pending:
                        break
                    finished, _ = wait(pending, return_when=FIRST_COMPLETED)
                    for future in finished:
                        batch = pending.pop(future)
                        try:
                            results[batch["batch_id"]] = future.result()
                            checkpoint("pausing" if pause_event.is_set() else "running")
                            report()
                        except CooperativePause as exc:
                            register_pause(exc)
                        except Exception as exc:
                            failure = exc
                    if failure is not None or pause_event.is_set():
                        exhausted = True
        if pause_event.is_set():
            raise pauses[0]
        if failure is not None:
            raise failure
        guard()
        dataset = _assemble(standard, items, results, model, contract)
        output = folder / "full_semantic_dataset.json"
        if output.is_file():
            existing = _read(output)
            if (existing.get("records") != dataset["records"] or existing.get("record_count") != len(items)
                    or existing.get("schema_version") != dataset["schema_version"]):
                raise SemanticError("已有语义数据集与已验证批次不一致，拒绝覆盖。")
            dataset = existing
        else:
            guard()
            _publish(output, dataset, folder / f"sessions/{session}.dataset.pending")
        manifest = {"status": "complete", "started_at_utc": started, "finished_at_utc": _now(),
                    "model_requested": model, "model_snapshot_version": "unknown", "batch_count": len(index),
                    "record_count": len(items), "prompt_sha256": contract["prompt_sha256"],
                    "schema_sha256": contract["schema_sha256"], "source_canonical_sha256": contract["source_canonical_sha256"],
                    "attempts": [_read(p) for p in sorted((folder / "meta").glob("*.json")) if not p.name.endswith(".started.json")],
                    "semantic_accuracy": "not evaluated; structural and identity validation only"}
        # Earlier completed/failed manifests are immutable evidence, including legacy runs.
        if not (folder / "run_manifest.json").exists():
            _publish(folder / "run_manifest.json", manifest, folder / f"sessions/{session}.manifest.pending")
        final = checkpoint("complete")
        _save(folder / f"sessions/{session}.json", {**final, "started_at_utc": started, "finished_at_utc": _now()})
        return dataset
    except CooperativePause as exc:
        final = checkpoint("paused", error=str(exc), reason=exc.reason)
        _save(folder / f"sessions/{session}.json", {**final, "started_at_utc": started, "finished_at_utc": _now()})
        raise
    except Exception as exc:
        final = checkpoint("failed", error=str(exc))
        _save(folder / f"sessions/{session}.json", {**final, "started_at_utc": started, "finished_at_utc": _now()})
        raise


def analyze(standard: dict, video: dict, run_dir: Path, progress=None, model: str | None = None, control=None) -> dict:
    """Resume identical frozen inputs; never repeat a verified successful batch.

    ``control()`` may raise CooperativePause. In-flight requests finish and save
    their evidence; no new batch/retry is started after a pause is observed.
    """
    model = model or os.environ.get("BFSI_MODEL") or DEFAULT_MODEL
    _inputs(standard, video)  # Fail before creating artifacts for malformed trees.
    folder = Path(run_dir).resolve() / "semantic"
    folder.mkdir(parents=True, exist_ok=True)
    with _run_lock(folder):
        return _analyze_locked(standard, video, folder, progress, model, control)
