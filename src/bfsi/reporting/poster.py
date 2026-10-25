"""Deterministic server-side BFSI share poster; never reads global/latest state."""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import re
import threading
import uuid
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from PIL import Image, ImageDraw, ImageFont, PngImagePlugin


WIDTH = 1080
MARGIN = 64
CONTENT = WIDTH - 2 * MARGIN
VERSION = "bfsi-poster-v1.1"
COLORS = {"bg": "#101315", "panel": "#1a1e21", "line": "#2c3236", "text": "#edece7",
          "muted": "#a0aaaf", "dim": "#7b878f", "green": "#a6d2be", "blue": "#9abbd5",
          "bear": "#d8a390", "mixed": "#c8b68d"}
STANCES = [("bullish", "看多", "#a6d2be"), ("bearish", "看空", "#d8a390"),
           ("mixed", "多向 / 混合", "#c8b68d"), ("neutral", "方向中性", "#9aa6a8"),
           ("unclear", "方向不明", "#839cac"), ("not_applicable", "未表达方向", "#46525a")]
GROUPS = [("bullish", "看多"), ("bearish", "看空"), ("mixed", "多向 / 混合"),
          ("ask_advice", "寻求建议"), ("humorous", "幽默 / 反讽")]
_LOCK = threading.Lock()


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _font_file():
    candidates = [os.environ.get("BFSI_POSTER_FONT"), "/System/Library/Fonts/PingFang.ttc",
                  "/System/Library/Fonts/STHeiti Medium.ttc", "/System/Library/Fonts/Hiragino Sans GB.ttc",
                  "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
                  "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return str(Path(candidate).resolve())
    raise RuntimeError("没有找到中文字体；请通过 BFSI_POSTER_FONT 配置可用的中文字体文件。")


def _number(value, name, minimum=0, maximum=None, integer=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"海报字段 {name} 必须是有限数字")
    if value < minimum or (maximum is not None and value > maximum) or (integer and int(value) != value):
        raise ValueError(f"海报字段 {name} 超出范围")
    return value


def _display_time(value):
    if not value:
        return "时间未记录"
    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if moment.tzinfo is None:
            return str(value) + "（时区未记录）"
        zone = os.environ.get("BFSI_TIMEZONE", "UTC")
        return moment.astimezone(ZoneInfo(zone)).strftime("%Y-%m-%d %H:%M") + "  " + zone
    except (ValueError, ZoneInfoNotFoundError):
        return str(value)


def _scope(analysis):
    collection = analysis.get("collection") or {}
    if not isinstance(collection, dict):
        raise ValueError("analysis.collection 必须为对象")
    mode = analysis.get("mode")
    scope = analysis.get("scope")
    partial = (scope == "partial" or collection.get("partial") is True or
               (mode == "full" and (collection.get("complete") is False or collection.get("truncated") is True)))
    if mode == "full":
        mode_text = "全量分析"
    elif mode == "quick" or scope == "sample":
        mode_text = "快速样本分析" + ("（历史记录）" if mode is None else "")
    else:
        mode_text = "分析模式未记录"
    if partial:
        range_text = "部分结果 · 采集范围受限"
    elif mode == "full" and scope == "full_accessible" and collection.get("complete") is True:
        range_text = "已完成可访问评论范围"
    elif scope == "sample" or mode == "quick":
        range_text = "样本范围 · 不代表全部评论"
    else:
        range_text = "请以本次采集记录为准"
    return collection, mode_text, range_text, partial


class _Canvas:
    def __init__(self, font_path):
        self.image = Image.new("RGB", (WIDTH, 10000), COLORS["bg"])
        self.draw = ImageDraw.Draw(self.image)
        self.font_path = font_path
        self.fonts = {}
        self.y = 60
        self.blocks = []

    def font(self, size):
        if size not in self.fonts:
            self.fonts[size] = ImageFont.truetype(self.font_path, size=size)
        return self.fonts[size]

    def wrap(self, text, size, width, max_lines=None, marker="…〔截断〕"):
        text = str(text or "").replace("\r\n", "\n").replace("\r", "\n").replace("\t", "  ")
        text = "".join(ch for ch in text if ord(ch) >= 32 or ch == "\n")
        lines, line, truncated = [], "", False
        font = self.font(size)
        for position, char in enumerate(text):
            if char == "\n" or (line and self.draw.textlength(line + char, font=font) > width):
                lines.append(line)
                line = "" if char == "\n" else char
                if max_lines is not None and len(lines) >= max_lines:
                    truncated = bool(line or position < len(text) - 1)
                    break
            else:
                line += char
        else:
            if line or not lines:
                lines.append(line)
        if truncated:
            tail = lines[-1]
            while tail and self.draw.textlength(tail + marker, font=font) > width:
                tail = tail[:-1]
            lines[-1] = tail + marker
        return lines, truncated

    def text(self, text, x, y, size=26, color="text", width=CONTENT, max_lines=None, marker="…〔截断〕"):
        lines, truncated = self.wrap(text, size, width, max_lines, marker)
        step = round(size * 1.48)
        for i, line in enumerate(lines):
            self.draw.text((x, y + i * step), line, font=self.font(size), fill=COLORS.get(color, color), anchor="lt")
        self.blocks.append({"text": "\n".join(lines), "x": x, "y": y, "width": width,
                            "height": len(lines) * step, "font_size": size, "truncated": truncated})
        return len(lines) * step, truncated

    def panel(self, y, height):
        self.draw.rounded_rectangle((MARGIN, y, WIDTH - MARGIN, y + height), radius=22, fill=COLORS["panel"], outline=COLORS["line"], width=2)

    def section(self, number, title):
        self.y += 42
        self.text(number, MARGIN, self.y + 6, 20, "green", width=50)
        self.text(title, MARGIN + 54, self.y, 32, width=CONTENT - 54)
        self.y += 62


def _selected_comments(payload):
    groups = payload.get("representative_comments") or {}
    if not isinstance(groups, dict):
        raise ValueError("代表评论必须为分组对象")
    selected, seen = [], set()
    # One example per available category first; fill any remaining slots from
    # the same existing representative set. No labels or summaries are created.
    for offset in range(5):
        for key, label in GROUPS:
            rows = groups.get(key) or []
            if not isinstance(rows, list):
                raise ValueError("代表评论分组必须为数组")
            if offset >= len(rows):
                continue
            row = rows[offset]
            if not isinstance(row, dict) or not isinstance(row.get("comment_id"), str):
                raise ValueError("代表评论缺少 Comment ID")
            if row["comment_id"] in seen:
                continue
            if not isinstance(row.get("raw_text"), str) or not isinstance(row.get("summary"), str):
                raise ValueError("代表评论缺少原文或摘要")
            selected.append({"category": key, "category_label": label, "record": row})
            seen.add(row["comment_id"])
            if len(selected) == 5:
                return selected
    return selected


def _render(payload, run_id, payload_hash, font_path):
    canvas = _Canvas(font_path)
    video, headline, analysis = payload["video"], payload["headline"], payload.get("analysis", {})
    collection, mode_text, range_text, partial = _scope(analysis)
    n = int(_number(video.get("comment_count"), "comment_count", minimum=1, integer=True))
    observations = int(_number(video.get("observation_count", analysis.get("observations", n)), "observations", minimum=n, integer=True))
    counts = payload.get("distributions", {}).get("market_stance", {})
    for key, _, _ in STANCES:
        _number(counts.get(key), "market_stance." + key, integer=True)
    if sum(counts[key] for key, _, _ in STANCES) != n:
        raise ValueError("六类方向评论数之和与不同评论总数不一致")
    for key in ("bfsi", "signal_coverage", "disagreement"):
        _number(headline.get(key), key, maximum=100)
    relevance = payload.get("auxiliary_metrics", {}).get("financial_relevance_rate", {})
    financial_count = int(_number(relevance.get("count"), "financial_count", maximum=n, integer=True))
    financial_rate = _number(relevance.get("display_percent"), "financial_relevance_rate", maximum=100)
    bvid = video.get("video_bvid") or analysis.get("bvid") or "BV 未记录"
    title = video.get("title") or "视频标题未记录"
    completed = analysis.get("completed_at") or analysis.get("finished_at")
    canvas.text("BFSI", MARGIN, canvas.y, 54, "green", width=200)
    canvas.text("B站财经评论区情绪观察", MARGIN + 196, canvas.y + 20, 25, "muted", width=CONTENT - 196)
    canvas.y += 102
    if analysis.get("synthetic") is True:
        height, _ = canvas.text("合成演示 · 人工构造数据 · 未采集真实评论", MARGIN, canvas.y, 26, "mixed", max_lines=2)
        canvas.y += height + 24
    height, title_truncated = canvas.text(title, MARGIN, canvas.y, 40, max_lines=3, marker="…〔标题截断〕")
    canvas.y += height + 26
    canvas.text(bvid, MARGIN, canvas.y, 26, "blue", max_lines=1)
    canvas.y += 48
    height, _ = canvas.text("分析完成  " + _display_time(completed), MARGIN, canvas.y, 22, "muted", max_lines=2)
    canvas.y += height + 27
    height, _ = canvas.text(mode_text + "  /  " + range_text, MARGIN, canvas.y, 25, "mixed" if partial else "green", max_lines=2)
    canvas.y += height + 22
    canvas.text(f"{n:,} 条不同评论   ·   {observations:,} 次来源观察", MARGIN, canvas.y, 26, "text", max_lines=1)
    canvas.y += 64
    canvas.panel(canvas.y, 282)
    widths, x = [330, 310, 312], MARGIN
    cards = [("BFSI 市场方向指数", "—" if headline.get("no_directional_signal") else f"{headline['bfsi']:.1f}", "暂无方向信号" if headline.get("no_directional_signal") else "/ 100", "green"),
             ("方向信号覆盖率", f"{headline['signal_coverage']:.1f}", "%", "blue"),
             ("多空分歧", "—" if headline.get("no_bullish_bearish_signal") else f"{headline['disagreement']:.0f}", "无多空比较样本" if headline.get("no_bullish_bearish_signal") else "/ 100", "mixed")]
    for index, (label, number, unit, color) in enumerate(cards):
        canvas.text(label, x + 26, canvas.y + 32, 23, "muted", width=widths[index] - 40)
        canvas.text(number, x + 24, canvas.y + 96, 80, color, width=widths[index] - 38)
        canvas.text(unit, x + 28, canvas.y + 218, 23, "dim", width=widths[index] - 38)
        if index:
            canvas.draw.line((x, canvas.y + 32, x, canvas.y + 248), fill=COLORS["line"], width=2)
        x += widths[index]
    canvas.y += 308
    flags = []
    if headline.get("no_directional_signal"):
        flags.append("无有效方向样本：BFSI 50 为约定占位值。")
    if headline.get("no_bullish_bearish_signal"):
        flags.append("无看多/看空比较样本：分歧 0 为约定值。")
    flags.append("BFSI 请与覆盖率一起阅读；小样本的极端分数不代表市场预测。")
    height, _ = canvas.text(" ".join(flags), MARGIN, canvas.y, 23, "muted", max_lines=3)
    canvas.y += height
    canvas.section("01", "方向分布与财经相关性")
    canvas.panel(canvas.y, 410)
    px, py, inner = MARGIN + 28, canvas.y + 30, CONTENT - 56
    canvas.text("全部不同评论的方向表达", px, py, 25, "muted", width=inner)
    bar_y, left = py + 54, px
    for key, _, color in STANCES:
        right = left + inner * counts[key] / n
        if right > left:
            canvas.draw.rectangle((left, bar_y, right, bar_y + 24), fill=color)
        left = right
    percentages = payload.get("distribution_percentages", {}).get("market_stance", {})
    for index, (key, label, color) in enumerate(STANCES):
        column, row = index % 2, index // 2
        lx, ly = px + column * 458, bar_y + 54 + row * 44
        canvas.draw.ellipse((lx, ly + 5, lx + 11, ly + 16), fill=color)
        percentage = percentages.get(key, counts[key] / n * 100)
        _number(percentage, key + " percent", maximum=100)
        canvas.text(label, lx + 22, ly, 23, "text", width=216)
        canvas.text(f"{counts[key]:,}  /  {percentage:.1f}%", lx + 226, ly, 23, "muted", width=208)
    canvas.draw.line((px, py + 258, px + inner, py + 258), fill=COLORS["line"], width=2)
    canvas.text("财经相关", px, py + 286, 28, width=220)
    canvas.text(f"{financial_rate:.1f}%", px + 240, py + 281, 39, "blue", width=240)
    canvas.text(f"{financial_count:,} / {n:,} 条不同评论", px + 520, py + 291, 23, "muted", width=inner - 520)
    canvas.y += 410
    canvas.section("02", "话题与资产观察")
    topics = payload.get("top_financial_topics") or []
    assets = payload.get("asset_market_target_indices") or []
    if not isinstance(topics, list) or not isinstance(assets, list):
        raise ValueError("话题与资产必须为数组")
    shown_topics, shown_assets = topics[:3], assets[:3]
    panel_height = 114 + max(1, len(shown_topics)) * 45 + (74 + len(shown_assets) * 72 if shown_assets else 0)
    canvas.panel(canvas.y, panel_height)
    y = canvas.y + 28
    canvas.text("主要财经话题  ·  涉及不同评论数", MARGIN + 28, y, 24, "muted", width=CONTENT - 56)
    y += 58
    if not shown_topics:
        canvas.text("样本内暂无达到展示阈值的财经对象", MARGIN + 28, y, 25, "dim", width=CONTENT - 56)
        y += 45
    for topic in shown_topics:
        canvas.text(str(topic["target_name"]), MARGIN + 28, y, 26, width=680, max_lines=1)
        canvas.text(f"{topic['total_mentions']} 条", WIDTH - MARGIN - 178, y, 25, "blue", width=150)
        y += 45
    if shown_assets:
        y += 18
        canvas.text("资产方向为评论级共现近似；并非对象专属立场", MARGIN + 28, y, 22, "dim", width=CONTENT - 56, max_lines=1)
        y += 52
        for asset in shown_assets:
            value = asset.get("target_BFSI_display", asset.get("target_BFSI"))
            coverage = asset.get("target_signal_coverage_display_pct", asset.get("target_signal_coverage", 0))
            _number(value, "target_BFSI", maximum=100)
            _number(coverage, "target_coverage", maximum=100)
            canvas.text(str(asset["target_name"]), MARGIN + 28, y, 26, width=340, max_lines=1)
            description = "无方向信号" if asset.get("no_directional_signal") else f"BFSI {value:.1f}"
            canvas.text(description, MARGIN + 380, y, 25, "green", width=220)
            canvas.text(f"{asset['total_mentions']} 条 / 覆盖 {coverage:.1f}%", MARGIN + 618, y, 23, "muted", width=CONTENT - 646)
            y += 72
    canvas.y += panel_height
    selected = _selected_comments(payload)
    canvas.section("03", f"代表评论  ·  {len(selected)} 条现有分析样例")
    comments_manifest = []
    if not selected:
        height, _ = canvas.text("本次结果没有可展示的代表评论；不补造评论或标签。", MARGIN, canvas.y, 25, "dim", max_lines=2)
        canvas.y += height
    for item in selected:
        record = item["record"]
        raw_lines, raw_cut = canvas.wrap(record["raw_text"], 28, CONTENT - 64, 4, "…〔原文截断〕")
        summary_lines, summary_cut = canvas.wrap(record["summary"], 24, CONTENT - 96, 3, "…〔摘要截断〕")
        height = 174 + len(raw_lines) * 41 + len(summary_lines) * 36
        canvas.panel(canvas.y, height)
        y = canvas.y + 26
        canvas.text(item["category_label"], MARGIN + 28, y, 24, "green", width=330)
        canvas.text("Comment " + record["comment_id"], MARGIN + 370, y + 2, 20, "dim", width=CONTENT - 400, max_lines=1)
        y += 52
        canvas.text("\n".join(raw_lines), MARGIN + 32, y, 28, width=CONTENT - 64)
        y += len(raw_lines) * 41 + 18
        canvas.text("分析摘要", MARGIN + 48, y, 19, "blue", width=CONTENT - 96)
        y += 32
        canvas.draw.line((MARGIN + 32, y, MARGIN + 32, y + len(summary_lines) * 36), fill=COLORS["blue"], width=3)
        canvas.text("\n".join(summary_lines), MARGIN + 48, y, 24, "muted", width=CONTENT - 96)
        comments_manifest.append({"comment_id": record["comment_id"], "category": item["category"],
                                  "category_label": item["category_label"], "market_stance": record.get("market_stance"),
                                  "raw_text": record["raw_text"], "summary": record["summary"],
                                  "rendered_raw_text": "\n".join(raw_lines), "raw_truncated": raw_cut,
                                  "rendered_summary": "\n".join(summary_lines), "summary_truncated": summary_cut})
        canvas.y += height + 18
    canvas.section("04", "来源与观察范围")
    collection_warnings = collection.get("warnings") or []
    analysis_warnings = analysis.get("warnings") or []
    if not isinstance(collection_warnings, list) or not isinstance(analysis_warnings, list):
        raise ValueError("采集与分析 warnings 必须为数组")
    warnings = []
    for warning in collection_warnings + analysis_warnings:
        if warning not in warnings:
            warnings.append(warning)
    scope_lines = [mode_text + "；" + range_text + "。",
                   "数据来源：B站可访问评论接口；由 BFSI 自动分析生成。",
                   "指数仅基于实际完成语义分析的不同评论；重复 Observation 不增加权重。"]
    if collection.get("raw_response_pages") is not None:
        scope_lines.append(f"保存原始响应 {collection['raw_response_pages']} 页；分析 {n} 条不同评论。")
    if collection.get("raw_unique_comments") is not None:
        scope_lines.append(f"原始采集 {collection['raw_unique_comments']} 条；通过标准化并分析 {n} 条。")
    limit = collection.get("analysis_comment_budget", analysis.get("requested_limit", collection.get("limit")))
    if limit is not None and (analysis.get("mode") != "full"):
        scope_lines.append(f"本次样本预算 {limit} 条；样本不代表全部评论。")
    if analysis.get("scope") == "full_accessible":
        scope_lines.append("全量仅指此次接口可访问范围，不保证等于平台展示评论总数。")
    for warning in warnings[:3]:
        scope_lines.append("范围提示：" + str(warning))
    if len(warnings) > 3:
        scope_lines.append(f"另有 {len(warnings) - 3} 项范围提示，完整内容见原结果。")
    for text in scope_lines:
        height, _ = canvas.text(text, MARGIN, canvas.y, 23, "mixed" if text.startswith("范围提示") else "muted", max_lines=3)
        canvas.y += height + 10
    canvas.y += 20
    canvas.draw.line((MARGIN, canvas.y, WIDTH - MARGIN, canvas.y), fill=COLORS["line"], width=2)
    canvas.y += 28
    canvas.text("V0.1 实验性观察指标  ·  不构成投资建议", MARGIN, canvas.y, 24, "green", max_lines=1)
    canvas.y += 48
    canvas.text("模型：" + str(analysis.get("model", "未记录")) + "；标签及摘要未经过人工金标准验收。", MARGIN, canvas.y, 21, "dim", max_lines=2)
    canvas.y += 70
    canvas.text("Run  " + run_id, MARGIN, canvas.y, 19, "dim", max_lines=1)
    canvas.y += 34
    canvas.text("Payload SHA-256  " + payload_hash[:24] + "…", MARGIN, canvas.y, 18, "dim", max_lines=1)
    canvas.y += 74
    if canvas.y > canvas.image.height:
        raise ValueError("海报内容超出安全高度")
    manifest = {"run_id": run_id, "bvid": bvid, "oid": str(video["video_oid"]), "title": title,
                "title_truncated": title_truncated, "completed_at": completed, "display_completed_at": _display_time(completed),
                "mode": analysis.get("mode"), "scope": analysis.get("scope"), "mode_label": mode_text,
                "range_label": range_text, "partial": partial, "collection_complete": collection.get("complete"),
                "different_comments": n, "raw_unique_comments": collection.get("raw_unique_comments"), "observations": observations, "headline": dict(headline),
                "stance_counts": dict(counts), "financial_relevance": dict(relevance),
                "topics": shown_topics, "assets": shown_assets, "representative_comments": comments_manifest,
                "scope_notes": scope_lines, "warnings": warnings, "text_blocks": canvas.blocks,
                "font_path": font_path, "font_sha256": _sha(Path(font_path).read_bytes())}
    return canvas.image.crop((0, 0, WIDTH, canvas.y)), manifest


def export_poster(payload: dict, run_dir: Path) -> dict:
    """Export only this persisted run's payload; same content is returned from cache.

    Completion/authorization belongs to the API caller. This function enforces
    run identity, immutable payload binding and immutable cached exports itself.
    """
    if not isinstance(payload, dict) or not isinstance(payload.get("analysis"), dict):
        raise ValueError("海报需要包含 analysis 的分析结果")
    run_id = payload["analysis"].get("run_id")
    if not isinstance(run_id, str) or not re.fullmatch(r"\d{8}T\d{6}Z_[a-f0-9]{8}", run_id):
        raise ValueError("海报 run_id 不合法")
    directory = Path(run_dir).absolute()
    if directory.is_symlink() or directory.resolve() != directory or directory.name != run_id:
        raise ValueError("海报运行目录与 run_id 不一致，或包含符号链接")
    for group in ("source", "metadata"):
        other = payload.get(group, {})
        if isinstance(other, dict) and other.get("run_id") not in (None, run_id):
            raise ValueError(f"海报 {group}.run_id 与运行目录不一致")
    payload_bytes = _json(payload)
    payload_hash = _sha(payload_bytes)
    persisted_path = directory / "payload.json"
    if persisted_path.is_symlink() or not persisted_path.is_file():
        raise ValueError("运行目录缺少独立保存的 payload.json")
    persisted_bytes = persisted_path.read_bytes()
    if _sha(_json(json.loads(persisted_bytes))) != payload_hash:
        raise ValueError("传入结果与当前运行目录的 payload.json 不一致")
    with _LOCK:
        exports = directory / "exports"
        if exports.is_symlink():
            raise ValueError("海报 exports 目录不能为符号链接")
        exports.mkdir(exist_ok=True)
        png_path, manifest_path = exports / "result_poster.png", exports / "manifest.json"
        if png_path.is_symlink() or manifest_path.is_symlink():
            raise ValueError("海报文件不能为符号链接")
        if manifest_path.exists():
            saved = json.loads(manifest_path.read_text(encoding="utf-8"))
            if saved.get("run_id") != run_id or saved.get("payload_sha256") != payload_hash:
                raise ValueError("已有海报属于不同运行或不同结果；拒绝覆盖")
            if saved.get("path") != str(png_path):
                raise ValueError("已有海报路径与当前运行目录不一致")
            if not png_path.is_file() or _sha(png_path.read_bytes()) != saved.get("sha256"):
                raise ValueError("已有海报完整性校验失败；拒绝静默覆盖")
            return {key: saved[key] for key in ("path", "filename", "width", "height", "sha256", "payload_sha256", "run_id", "renderer_version")}
        if png_path.exists():
            raise ValueError("存在未带来源清单的海报，拒绝覆盖")
        image, content = _render(payload, run_id, payload_hash, _font_file())
        temporary = exports / (".poster-" + uuid.uuid4().hex + ".tmp")
        temporary_manifest = exports / (".manifest-" + uuid.uuid4().hex + ".tmp")
        try:
            info = PngImagePlugin.PngInfo()
            info.add_text("BFSI run_id", run_id)
            info.add_text("BFSI payload_sha256", payload_hash)
            image.save(temporary, format="PNG", pnginfo=info, optimize=True)
            safe_bvid = str(content["bvid"]) if re.fullmatch(r"BV[A-Za-z0-9]{10}", str(content["bvid"])) else None
            filename = "BFSI_" + (safe_bvid + "_" if safe_bvid else "") + run_id + ".png"
            result = {"path": str(png_path), "filename": filename,
                      "width": image.width, "height": image.height, "sha256": _sha(temporary.read_bytes()),
                      "payload_sha256": payload_hash, "run_id": run_id, "renderer_version": VERSION}
            manifest = {**content, **result, "payload_file_sha256": _sha(persisted_bytes)}
            temporary_manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
            if _sha(persisted_path.read_bytes()) != _sha(persisted_bytes):
                raise ValueError("生成海报期间 payload.json 发生变化，拒绝发布")
            os.replace(temporary, png_path)
            os.replace(temporary_manifest, manifest_path)
            return result
        finally:
            temporary.unlink(missing_ok=True)
            temporary_manifest.unlink(missing_ok=True)
