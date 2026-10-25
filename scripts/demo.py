#!/usr/bin/env python3
"""Deterministic, invented examples. No network requests or model invocations."""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path

from bfsi.index.calculator import build_payload

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "examples/demo"
RUN_ID = "20261025T000000Z_00000001"
STAMP = "2026-10-25T00:00:00Z"


def synthetic_dataset():
    stances = ["bullish"] * 10 + ["bearish"] * 8 + ["mixed"] * 4 + ["neutral"] * 4 + ["unclear"] * 2 + ["not_applicable"] * 2
    texts = {
        "bullish": "【合成评论】我对后续市场持乐观态度，仍需观察风险。",
        "bearish": "【合成评论】我担心短期市场回调，准备谨慎观察。",
        "mixed": "【合成评论】长期有机会，但短期走势仍不确定。",
        "neutral": "【合成评论】这里讨论了通胀和黄金，并未表达价格方向。",
        "unclear": "【合成评论】还需要更多信息才能判断。",
        "not_applicable": "【合成评论】这个图表的颜色挺清楚。",
    }
    records = []
    for i, stance in enumerate(stances):
        records.append({
            "comment_id": str(1001 + i), "video_oid": "900001", "video_bvid": "BV0000000000",
            "raw_text": texts[stance] + f"（演示编号 {i + 1}）", "summary": "合成人工标签：" + stance,
            "relevance": "financial" if i < 26 else "unclear" if i < 28 else "other",
            "market_stance": stance, "emotion": ["optimistic"] if stance == "bullish" else ["anxious"] if stance == "bearish" else ["neutral"],
            "action_intent": "hold" if i < 5 else "ask_advice" if i in (23, 24) else "not_applicable",
            "creator_attitude": "not_applicable", "sarcasm": "no", "confidence": "high",
            "targets": [{"raw_name": "黄金" if i % 2 else "通货膨胀", "normalized_name": "黄金" if i % 2 else "通货膨胀", "target_type": "asset" if i % 2 else "macro"}] if i < 26 else [],
            "depth": 0, "parent_comment_id": None, "root_comment_id": None,
            "observation_count": 2 if i < 5 else 1,
            "context_used": {"direct_parent": None, "root": None, "ancestor_chain": []},
        })
    return {"dataset_version": "synthetic_demo_v1", "model_requested": "synthetic-human-labels-no-model", "records": records, "synthetic": True}


def artifacts():
    dataset = synthetic_dataset()
    video = {"oid": "900001", "bvid": "BV0000000000", "title": "合成演示｜财经评论区的不同市场观点"}
    scope = {"mode": "quick", "scope": "synthetic", "complete": False, "partial": True,
             "selected_comments": 30, "raw_unique_comments": 30, "raw_response_pages": 0,
             "warnings": ["全部数据为人工构造，未采集任何真实视频或用户评论。"]}
    payload = build_payload(dataset, video, scope, RUN_ID)
    payload["analysis"].update(synthetic=True, mode="quick", scope="synthetic", requested_limit=30,
        analyzed_comments=30, semantic_complete=True, created_at=STAMP, completed_at=STAMP,
        warnings=scope["warnings"])
    payload["metadata"] = {"run_id": RUN_ID, "generated_at": STAMP, "synthetic": True}
    return {"semantic.json": dataset, "payload.json": payload}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write-examples", action="store_true", help="regenerate reviewed synthetic examples")
    parser.add_argument("--check", action="store_true", help="verify committed examples are reproducible")
    parser.add_argument("--install", action="store_true", help="install a local demo run and make it the latest result")
    args = parser.parse_args()
    values = artifacts()
    if args.write_examples:
        DEMO.mkdir(parents=True, exist_ok=True)
        for name, value in values.items():
            (DEMO / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.check:
        for name, expected in values.items():
            if json.loads((DEMO / name).read_text(encoding="utf-8")) != expected:
                raise SystemExit(f"Synthetic example drift: {name}; regenerate deliberately with --write-examples")
        print("Synthetic examples reproducible; no network or LLM used.")
    if args.install:
        from bfsi.config import DATA_DIR, RUNS
        from bfsi.server.jobs import save_json
        folder = RUNS / RUN_ID
        if folder.exists():
            existing = json.loads((folder / "payload.json").read_text(encoding="utf-8"))
            if existing != values["payload.json"]:
                raise SystemExit("Existing demo differs; choose a separate BFSI_DATA_DIR. No files replaced.")
        else:
            folder.mkdir(parents=True)
            save_json(folder / "payload.json", copy.deepcopy(values["payload.json"]))
            save_json(folder / "status.json", dict(run_id=RUN_ID, state="completed", stage="completed",
                stage_label="合成演示", bvid="BV0000000000", input="synthetic", mode="quick", limit=30,
                created_at=STAMP, updated_at=STAMP, collected=30, analyzed=30, synthetic=True,
                video={"bvid":"BV0000000000", "title":values["payload.json"]["video"]["title"]},
                message="人工构造演示：未访问平台、未调用模型", error=None))
        save_json(DATA_DIR / "latest.json", {"run_id": RUN_ID})
        print("Local synthetic demo installed; start the server to view and export it.")
    if not any(vars(args).values()):
        parser.print_help()


if __name__ == "__main__":
    main()
