"""Build the existing BFSI v0.1 presentation contract for one isolated run.

The formula module, semantic enums and generic alias file are versioned source assets.
This module performs no network requests and never writes an output file.
"""
from __future__ import annotations

import copy
import hashlib
import json
from collections import Counter, defaultdict
from fractions import Fraction
from functools import lru_cache
from pathlib import Path
from ..config import ROOT, SCHEMAS, RESOURCES
from . import math as index_math


FIELDS = ("relevance", "market_stance", "emotion", "action_intent",
          "creator_attitude", "sarcasm", "confidence")
REPRESENTATIVE_LIMITS = {"bullish": 3, "bearish": 3, "mixed": 2,
                         "ask_advice": 2, "humorous": 2}
COPY_FIELDS = ("comment_id", "raw_text", "summary", "market_stance", "targets",
               "action_intent", "confidence", "emotion", "sarcasm", "depth",
               "parent_comment_id", "root_comment_id", "context_used")


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


@lru_cache(maxsize=4)
def _assets(root: str = ""):
    formula_path = Path(index_math.__file__).resolve()
    schema_path = SCHEMAS / "simplified_semantic_schema_v0.1.json"
    alias_path = RESOURCES / "target_aliases_v0.1.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    alias_config = json.loads(alias_path.read_text(encoding="utf-8"))
    aliases = {entry["from"]: entry["to"] for entry in alias_config["aliases"]}
    if len(aliases) != len(alias_config["aliases"]) or set(aliases.values()) & set(aliases):
        raise ValueError("对象别名规则重复或形成链式映射")
    provenance = {
        "formula_path": "bfsi/index/math.py", "formula_sha256": _sha(formula_path.read_bytes()),
        "semantic_schema_path": "bfsi/resources/" + schema_path.name, "semantic_schema_sha256": _sha(schema_path.read_bytes()),
        "alias_path": "bfsi/resources/" + alias_path.name, "alias_sha256": _sha(alias_path.read_bytes()),
    }
    return index_math, schema, aliases, provenance


def _validated_records(dataset: dict, video: dict, schema: dict) -> tuple[list[dict], int]:
    rows = dataset.get("records")
    if not isinstance(rows, list) or not rows:
        raise ValueError("没有可计算的有效评论；不会生成占位指数")
    oid = str(video.get("oid", video.get("video_oid", "")))
    if not oid.isdigit() or int(oid) <= 0:
        raise ValueError("视频缺少有效 OID")
    bvid = video.get("bvid", video.get("video_bvid"))
    if bvid is not None and not isinstance(bvid, str):
        raise ValueError("视频 BV 号必须为字符串")
    if video.get("title") is not None and not isinstance(video["title"], str):
        raise ValueError("视频标题必须为字符串")
    unique, encoded, duplicate_count = {}, {}, 0
    target_schema = schema["properties"]["targets"]["items"]["properties"]
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("语义记录必须为对象")
        cid = row.get("comment_id")
        if not isinstance(cid, str) or not cid:
            raise ValueError("评论缺少字符串 Comment ID")
        if str(row.get("video_oid", "")) != oid:
            raise ValueError(f"评论 {cid} 的 OID 与目标视频不一致")
        row_bvid = row.get("video_bvid")
        if row_bvid is not None and not isinstance(row_bvid, str):
            raise ValueError(f"评论 {cid} 的 BV 号必须为字符串")
        if bvid and row_bvid and row_bvid != bvid:
            raise ValueError(f"评论 {cid} 的 BV 号与目标视频不一致")
        for field in FIELDS:
            value = row.get(field)
            if field == "emotion":
                opts = schema["properties"][field]["items"]["enum"]
                if not isinstance(value, list) or not value or any(not isinstance(v, str) or v not in opts for v in value):
                    raise ValueError(f"评论 {cid} 的 emotion 标签不合法")
                if len(set(value)) != len(value):
                    raise ValueError(f"评论 {cid} 的 emotion 标签重复")
            elif not isinstance(value, str) or value not in schema["properties"][field]["enum"]:
                raise ValueError(f"评论 {cid} 的 {field} 标签不合法")
        if any(not isinstance(row.get(field), str) for field in ("raw_text", "summary")):
            raise ValueError(f"评论 {cid} 缺少原文或语义摘要")
        for field in ("depth", "observation_count"):
            value = row.get(field, 0 if field == "depth" else 1)
            if type(value) is not int or value < (0 if field == "depth" else 1):
                raise ValueError(f"评论 {cid} 的 {field} 不合法")
        targets = row.get("targets")
        if not isinstance(targets, list):
            raise ValueError(f"评论 {cid} 缺少 targets 数组")
        for target in targets:
            if not isinstance(target, dict) or not isinstance(target.get("raw_name"), str) or not target["raw_name"]:
                raise ValueError(f"评论 {cid} 的对象原名称不合法")
            if target.get("normalized_name") is not None and not isinstance(target["normalized_name"], str):
                raise ValueError(f"评论 {cid} 的对象标准名称不合法")
            if target.get("target_type") not in target_schema["target_type"]["enum"]:
                raise ValueError(f"评论 {cid} 的对象类型不合法")
        signature = _canonical(row)
        if cid in unique:
            if signature != encoded[cid]:
                raise ValueError(f"Comment ID {cid} 存在冲突的重复语义记录")
            duplicate_count += 1
            continue
        unique[cid], encoded[cid] = row, signature
    # A missing requested BV is acceptable for historical regression, but rows
    # from two different known BVs must never be silently combined.
    known_bvids = {r.get("video_bvid") for r in unique.values() if r.get("video_bvid")}
    if len(known_bvids) > 1:
        raise ValueError("语义记录跨越多个 BV 视频")
    return list(unique.values()), duplicate_count


def _methodology() -> dict:
    return {
        "index_name": "BFSI Market Direction Index", "version": "v0.1",
        "unit_of_analysis": "unique Comment ID", "unweighted": True,
        "relevance_filter_for_main_index": False,
        "directional_score": "(bullish - bearish) / (bullish + bearish + mixed)",
        "bfsi": "50 + 50 * directional_score; denominator 0 => 50 and no_directional_signal=true",
        "signal_coverage": "(bullish + bearish + mixed) / all_unique_comments; displayed as percent",
        "disagreement": "1 - abs(bullish - bearish) / (bullish + bearish); denominator 0 => 0; displayed × 100",
        "excluded_stances": ["neutral", "unclear", "not_applicable"],
        "mixed_contribution": "0 in numerator, 1 in BFSI denominator",
        "active_trading_intent_labels": ["buy", "add", "reduce", "sell", "hold"],
        "display_rounding": {"bfsi_decimals": 1, "percent_decimals": 1, "disagreement_decimals": 0, "mode": "ROUND_HALF_UP"},
        "commentary_only_bands": {"near_neutral_bfsi_inclusive": [45, 55], "low_coverage_below_percent": 10, "high_disagreement_at_least": 80},
        "commentary_bands_note": "Fixed display wording only; never affect counts, index values or weighting; no parameter optimization.",
        "target_method": "V0.1 co-occurrence approximation", "target_threshold_unique_comments": 5,
        "target_units": {"total_mentions": "unique comments mentioning canonical target", "target_BFSI": "index_0_100", "target_signal_coverage": "percent_0_100"},
        "target_grouping": "canonical_name only; source target_types retained; per-comment deduplication after alias merge",
        "emotion_distribution": "multi-label; each label counted once per comment; category counts need not sum to total",
        "auxiliary_rate_denominator": "all_unique_comments for every auxiliary rate",
    }


def build_payload(dataset: dict, video: dict, collection: dict, run_id: str) -> dict:
    """Return a Task 07-compatible payload, without mutating any input.

    Exact duplicate rows count once, including their observation_count. Conflicting
    duplicates and mixed-video rows fail rather than silently choosing a label.
    ``collection`` is recorded as evidence and never affects formula weights.
    """
    if not isinstance(dataset, dict) or not isinstance(video, dict):
        raise ValueError("语义数据和视频元信息必须为对象")
    if not isinstance(collection, dict):
        raise ValueError("采集范围必须为对象")
    if not isinstance(run_id, str) or not run_id.strip():
        raise ValueError("缺少分析 run_id")
    math, schema, aliases, legacy = _assets()
    records, duplicates = _validated_records(dataset, video, schema)
    n = len(records)
    byid = {r["comment_id"]: r for r in records}
    distributions = {}
    for field in FIELDS:
        opts = schema["properties"][field]["items"]["enum"] if field == "emotion" else schema["properties"][field]["enum"]
        counts = Counter(tag for r in records for tag in set(r[field])) if field == "emotion" else Counter(r[field] for r in records)
        distributions[field] = {key: counts[key] for key in opts}
    stance = distributions["market_stance"]
    b, s, m = (stance[key] for key in ("bullish", "bearish", "mixed"))
    directional, disagreement = math.direction(b, s, m), math.disagreement(b, s)
    coverage = math.rate(directional["directional_comments"], n, "all_unique_comments")
    headline = {
        "bfsi": math.shown(directional["bfsi"]), "signal_coverage": coverage["display_percent"],
        "disagreement": int(math.shown(disagreement["ratio"] * 100, 0)),
        "bfsi_unrounded": float(directional["bfsi"]), "signal_coverage_unrounded": coverage["percent"],
        "signal_coverage_ratio": coverage["ratio"], "disagreement_unrounded": float(disagreement["ratio"] * 100),
        "disagreement_ratio": float(disagreement["ratio"]), "directional_score": float(directional["score"]),
        "directional_comments": directional["directional_comments"], "bullish_bearish_comments": disagreement["count"],
        "no_directional_signal": directional["no_directional_signal"],
        "no_bullish_bearish_signal": disagreement["no_bullish_bearish_signal"],
        "units": {"bfsi": "index_0_100", "signal_coverage": "percent_0_100", "disagreement": "score_0_100"},
    }
    denominators = {
        "financial_relevance_rate": distributions["relevance"]["financial"],
        "advice_seeking_rate": distributions["action_intent"]["ask_advice"],
        "active_trading_intent_rate": sum(distributions["action_intent"][k] for k in ("buy", "add", "reduce", "sell", "hold")),
        "anxiety_rate": distributions["emotion"]["anxious"], "confusion_rate": distributions["emotion"]["confused"],
        "humor_rate": distributions["emotion"]["humorous"], "creator_positive_rate": distributions["creator_attitude"]["positive"],
    }
    aux = {key: math.rate(count, n, "all_unique_comments") for key, count in denominators.items()}
    aux["active_trading_intent_rate"]["note"] = "Exploratory expression metric: completed trades, conditional plans and attribution ambiguity may coexist; not future orders."
    target_ids, target_types, names = defaultdict(set), defaultdict(set), defaultdict(set)
    for record in records:
        for target in record["targets"]:
            name = target.get("normalized_name") or target["raw_name"]
            canonical = aliases.get(name, name)
            target_ids[canonical].add(record["comment_id"])
            target_types[canonical].add(target["target_type"])
            names[canonical].add(name)
    target_indices, top_targets = [], []
    for name, ids in sorted(target_ids.items(), key=lambda item: (-len(item[1]), item[0])):
        if len(ids) < 5:
            continue
        counts = Counter(byid[cid]["market_stance"] for cid in ids)
        local = math.direction(counts["bullish"], counts["bearish"], counts["mixed"])
        rate = math.rate(local["directional_comments"], len(ids), "unique_comments_mentioning_this_target")
        row = {
            "target_name": name, "target_types": sorted(target_types[name]), "aliases_present": sorted(names[name]),
            "is_asset_or_market": bool(target_types[name] & {"asset", "market"}), "total_mentions": len(ids),
            **{f"{key}_comments": counts[key] for key in stance},
            "directional_comments": local["directional_comments"], "target_BFSI": float(local["bfsi"]),
            "target_BFSI_display": math.shown(local["bfsi"]), "target_signal_coverage": rate["percent"],
            "target_signal_coverage_ratio": rate["ratio"], "target_signal_coverage_display_pct": rate["display_percent"],
            "no_directional_signal": local["no_directional_signal"], "method": "V0.1 co-occurrence approximation",
            "target_stance_is_directly_annotated": False,
        }
        target_indices.append(row)
        share = math.rate(len(ids), n, "all_unique_comments")
        top_targets.append({"target_name": name, "target_types": row["target_types"], "total_mentions": len(ids),
                            "share_of_all_comments_percent": share["percent"], "share_of_all_comments_display_pct": share["display_percent"],
                            "aliases_present": row["aliases_present"], "is_asset_or_market": row["is_asset_or_market"]})
    topics = [row for row in top_targets if set(row["target_types"]) & {"asset", "market", "macro", "policy"}][:5]
    representatives, selected_ids, selected_text = {}, set(), set()
    candidates = sorted(records, key=lambda r: (0 if r["confidence"] == "high" else 1, r["comment_id"]))
    for category, limit in REPRESENTATIVE_LIMITS.items():
        representatives[category] = []
        for record in candidates:
            if len(representatives[category]) == limit:
                break
            if record["confidence"] not in ("high", "medium") or record["comment_id"] in selected_ids or record["raw_text"] in selected_text:
                continue
            matches = (record["market_stance"] == category if category in ("bullish", "bearish", "mixed") else
                       record["action_intent"] == "ask_advice" if category == "ask_advice" else
                       "humorous" in record["emotion"] or record["sarcasm"] == "yes")
            if matches:
                example = {key: copy.deepcopy(record.get(key)) for key in COPY_FIELDS}
                example["depth"] = record.get("depth", 0)
                representatives[category].append(example)
                selected_ids.add(record["comment_id"])
                selected_text.add(record["raw_text"])
    if directional["no_directional_signal"]:
        direction_text = "没有有效方向样本，50 是约定的占位值，不代表已观察到多空平衡"
    elif directional["bfsi"] == 50:
        direction_text = "方向分数位于多空平衡中点"
    elif 45 <= directional["bfsi"] <= 55:
        direction_text = "方向接近中性并略偏" + ("多" if b > s else "空")
    else:
        direction_text = "方向分数偏" + ("多" if b > s else "空")
    coverage_text = "方向信号覆盖率较低" if coverage["percent"] < 10 else "解读方向分数时需同时参考覆盖率"
    if disagreement["no_bullish_bearish_signal"]:
        disagreement_text = "没有 bullish/bearish 样本，分歧值 0 是约定值，不代表一边倒"
    elif disagreement["ratio"] >= Fraction(4, 5):
        disagreement_text = "多空数量接近，分歧较高"
    elif disagreement["ratio"] == 0:
        disagreement_text = "已观察到的 bullish/bearish 样本仅有一侧"
    else:
        disagreement_text = "多空数量存在差异"
    topic_text = "财经对象提及主要集中在" + "、".join(row["target_name"] for row in topics) + "等主题。" if topics else "达到至少5条评论展示阈值的财经对象为空。"
    commentary = (f"本视频评论区 BFSI 为 {headline['bfsi']:.1f}/100，{direction_text}。"
                  f"{directional['directional_comments']}/{n} 条评论（{headline['signal_coverage']:.1f}%）表达了明确市场方向，{coverage_text}。"
                  f"在 bullish/bearish 的 {disagreement['count']} 条评论中，看多 {b} 条、看空 {s} 条，Disagreement 为 {headline['disagreement']}/100，{disagreement_text}；mixed {m} 条参与 BFSI 分母，不参与分歧计算。" + topic_text)
    observations = sum(r.get("observation_count", 1) for r in records)
    video_data = {
        "video_oid": str(video.get("oid", video.get("video_oid"))),
        "video_bvid": video.get("bvid", video.get("video_bvid")) or next((r.get("video_bvid") for r in records if r.get("video_bvid")), None),
        "title": video.get("title"), "comment_count": n, "observation_count": observations,
        "title_status": "known" if video.get("title") else "unknown",
    }
    source = {
        "run_id": run_id, "semantic_version": dataset.get("dataset_version", dataset.get("schema_version", "simplified_semantic_v0.1")),
        "sha256": _sha(_canonical(dataset)), "hash_basis": "canonical UTF-8 JSON; sorted keys; compact separators",
        "upstream_source": copy.deepcopy(dataset.get("source", {})), **legacy,
    }
    payload = {
        "payload_version": "v0.1", "video": video_data, "headline": headline, "auxiliary_metrics": aux,
        "counts": {"total_comments": n, **stance},
        "exact_calculation": {"directional_score_fraction": str(directional["score"]), "bfsi_fraction": str(directional["bfsi"]),
                              "signal_coverage_fraction": str(Fraction(directional["directional_comments"], n)), "disagreement_fraction": str(disagreement["ratio"])},
        "distributions": distributions,
        "distribution_percentages": {field: {key: math.rate(count, n, "all_unique_comments")["display_percent"] for key, count in counts.items()} for field, counts in distributions.items()},
        "distribution_denominator": n, "top_targets": top_targets, "top_financial_topics": topics,
        "target_indices": target_indices, "asset_market_target_indices": [row for row in target_indices if row["is_asset_or_market"]],
        "representative_comments": representatives, "commentary": commentary, "methodology": _methodology(), "source": source,
        "analysis": {"run_id": run_id, "model": dataset.get("model_requested", dataset.get("model", "unknown")),
                     "scope": copy.deepcopy(collection.get("scope", "以本次采集范围为准")), "collection": copy.deepcopy(collection),
                     "unique_comments": n, "observations": observations, "input_records": len(dataset["records"]),
                     "duplicate_records_ignored": duplicates, "representative_selection": "deterministic label selection; category limits 3/3/2/2/2; missing groups empty"},
        "limitations": ["V0.1 exploratory index for the collected video sample, not market prediction.",
                        "Interpret BFSI jointly with coverage; no_directional_signal must be displayed when true.",
                        "Targets use comment-level stance co-occurrence, not target-level stance.",
                        "Action labels can include past trades, conditional plans and attribution ambiguity.",
                        "Representative comments are model outputs, not human-approved facts or investment advice."],
    }
    # Enforce a JSON-safe result before publication, including unknown metadata.
    _canonical(payload)
    return payload
