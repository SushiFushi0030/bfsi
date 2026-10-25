"""Public Bilibili pagination, immutable receipts and resumable checkpoints."""
from __future__ import annotations

import hashlib
import gzip
import io
import json
import os
import re
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlparse

from ..config import RUNS, DATA_DIR
from ..control import CooperativePause
from . import http_client

PROJECT = DATA_DIR
VIEW_ENDPOINT = '/x/web-interface/view'
MAIN_ENDPOINT = '/x/v2/reply/wbi/main'
REPLY_ENDPOINT = '/x/v2/reply/reply'
API = 'https://api.bilibili.com'
REQUEST_INTERVAL = 1.05
MAX_REQUESTS = 80
MAX_PAGES_PER_ROOT = 20
def _environment_number(name, default, minimum, maximum):
    try:
        value = float(os.environ.get(name, default))
        if not minimum <= value <= maximum:
            return default
        return value
    except (TypeError, ValueError):
        return default


FULL_BURST_REQUESTS = int(_environment_number('BFSI_COLLECTION_BURST_REQUESTS', 80, 1, 10000))
FULL_COOLDOWN_SECONDS = _environment_number('BFSI_COLLECTION_COOLDOWN_SECONDS', 5, 0, 300)



class CollectionError(RuntimeError):
    """A visible collection failure, optionally retaining its exact HTTP response."""
    def __init__(self, message, *, raw=None, status=None, restricted=False):
        super().__init__(message)
        self.raw, self.status, self.restricted = raw, status, restricted


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec='microseconds').replace('+00:00', 'Z')


def _legacy():
    """Compatibility hook for injected test transports; uses the packaged HTTP client."""
    return http_client


def _decode_transport(raw):
    """Decode standard gzip transport, retaining the JSON representation bytes unchanged."""
    maximum = 20 * 1024 * 1024
    if len(raw) > maximum:
        raise CollectionError('单页响应超过安全大小限制，已停止。')
    if raw.startswith(b'\x1f\x8b'):
        try:
            with gzip.GzipFile(fileobj=io.BytesIO(raw)) as handle:
                decoded = handle.read(maximum + 1)
        except (OSError, EOFError) as exc:
            raise CollectionError('B站 gzip 响应无法正常解压，已停止。', raw=raw) from exc
        if len(decoded) > maximum:
            raise CollectionError('单页 gzip 解压结果超过安全大小限制，已停止。')
        return decoded
    return raw


def _request(url):
    """Retry transient transport/server errors only; never retry platform restrictions."""
    for attempt in range(3):
        try:
            return _decode_transport(_legacy().get_bytes(url)), utc_now()
        except HTTPError as exc:
            raw = exc.read()
            if exc.code in (401, 403, 412, 429):
                raise CollectionError(
                    f'B站访问受限（HTTP {exc.code}），已停止；请稍后通过正常访问条件重试。',
                    raw=raw, status=exc.code, restricted=True) from exc
            if exc.code >= 500 and attempt < 2:
                time.sleep(1 + attempt)
                continue
            raise CollectionError(f'B站请求失败（HTTP {exc.code}）。', raw=raw, status=exc.code) from exc
        except (URLError, TimeoutError, OSError) as exc:
            if attempt < 2:
                time.sleep(1 + attempt)
                continue
            raise CollectionError('B站网络请求超时或连接失败，请检查网络后重试。') from exc


def _payload(raw):
    try:
        result = json.loads(raw)
    except (ValueError, UnicodeError) as exc:
        raise CollectionError('B站返回了非 JSON 响应，可能暂时不可访问。', raw=raw) from exc
    if not isinstance(result, dict) or result.get('code') != 0:
        code = result.get('code') if isinstance(result, dict) else None
        restricted = code in (-352, -412, -401, -403, -101, -111, -509)
        reason = '访问校验或登录限制，已停止' if restricted else '接口未成功'
        raise CollectionError(f'B站{reason}（code={code}）。', raw=raw, status=code, restricted=restricted)
    if not isinstance(result.get('data'), dict):
        raise CollectionError('B站响应缺少有效 data 对象。', raw=raw)
    return result


def _mixin_key():
    for attempt in range(3):
        try:
            return _legacy().get_mixin_key()
        except HTTPError as exc:
            raw = exc.read()
            if exc.code in (401, 403, 412, 429):
                raise CollectionError(f'B站签名信息访问受限（HTTP {exc.code}），已停止。',
                    raw=raw, status=exc.code, restricted=True) from exc
            if exc.code >= 500 and attempt < 2:
                time.sleep(1 + attempt)
                continue
            raise CollectionError(f'B站签名信息请求失败（HTTP {exc.code}）。', raw=raw, status=exc.code) from exc
        except (URLError, TimeoutError, OSError) as exc:
            if attempt < 2:
                time.sleep(1 + attempt)
                continue
            raise CollectionError('B站签名信息连接失败，请检查网络后重试。') from exc
        except (ValueError, KeyError, TypeError) as exc:
            raise CollectionError('B站签名信息格式异常，已停止。') from exc


def normalize_bvid(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError('请输入 BV 号或 bilibili.com 视频链接。')
    value = value.strip()
    if re.fullmatch(r'BV[A-Za-z0-9]{10}', value):
        return value
    parsed = urlparse(value)
    if parsed.scheme not in ('http', 'https') or parsed.hostname not in ('bilibili.com', 'www.bilibili.com', 'm.bilibili.com'):
        raise ValueError('请输入完整 BV 号，或 bilibili.com/video/BV… 视频链接。')
    match = re.fullmatch(r'/(?:zh-Hans/)?video/(BV[A-Za-z0-9]{10})/?', parsed.path)
    if not match:
        raise ValueError('视频链接中未找到有效 BV 号。')
    return match[1]


def parse_public_video_page(raw: bytes, requested_bvid: str) -> dict:
    """Read the real public page's JSON state; never execute script or fabricate API JSON."""
    try:
        html = raw.decode('utf-8')
    except UnicodeError as exc:
        raise CollectionError('公开视频页面编码无效。', raw=raw) from exc
    marker = re.search(r'window\.__INITIAL_STATE__\s*=\s*', html)
    if marker is None:
        restricted = bool(re.search(r'验证码|访问验证|安全验证|风控|captcha|risk.control', html, re.I))
        message = '公开页面要求访问验证，已停止。' if restricted else '公开视频页面没有可核验的 SSR 视频信息，已停止。'
        raise CollectionError(message, raw=raw, restricted=restricted)
    try:
        state, _ = json.JSONDecoder().raw_decode(html[marker.end():])
    except ValueError as exc:
        raise CollectionError('公开视频页面的 SSR 视频信息不是有效 JSON。', raw=raw) from exc
    if not isinstance(state, dict) or not isinstance(state.get('videoData'), dict):
        raise CollectionError('公开视频页面缺少 videoData。', raw=raw)
    data = state['videoData']
    oid = str(data.get('aid', ''))
    if not (data.get('bvid') == state.get('bvid') == requested_bvid
            and str(state.get('aid')) == oid and re.fullmatch(r'[1-9][0-9]*', oid)):
        raise CollectionError('公开页面顶层与 videoData 的 BV/OID 映射不一致，已停止。', raw=raw)
    return data


def resolve_video(value: str) -> dict:
    bvid = normalize_bvid(value)
    # This is the normal URL observed in the current public Bilibili browser page.
    # It is the primary metadata source; a rejected API is never relabelled as successful.
    url = f'https://www.bilibili.com/zh-Hans/video/{bvid}/'
    raw, received_at = _request(url)
    data = parse_public_video_page(raw, bvid)
    oid = str(data['aid'])
    return {
        'bvid': bvid, 'oid': oid, 'title': str(data.get('title') or ''),
        'description': str(data.get('desc') or ''),
        'url': f'https://www.bilibili.com/video/{bvid}/',
        'published_at_unix': data.get('pubdate'), 'duration_seconds': data.get('duration'),
        'reported_comment_count': (data.get('stat') or {}).get('reply'),
        'resolution_evidence': {'source': 'public_video_page', 'endpoint': f'/zh-Hans/video/{bvid}/',
            'url': url, 'requested_bvid': bvid,
            'response_bvid': bvid, 'response_oid': oid, 'received_at': received_at,
            'response_sha256': hashlib.sha256(raw).hexdigest()},
        # JSON-serializable; collect writes these exact UTF-8 bytes as mapping evidence.
        '_resolution_raw': raw.decode('utf-8'),
    }


def _write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')


def _run_path(run_dir):
    run_dir = Path(run_dir).resolve()
    if not run_dir.is_relative_to(RUNS.resolve()):
        raise ValueError('新采集只能写入配置的数据目录下的 runs，不能写入源码或历史任务。')
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def _raw_nodes(data, main):
    """Enumerate only the positions supported by the frozen Standard Comment contract."""
    roots = list(data.get('replies') or []) if main else []
    if main:
        upper = (data.get('top') or {}).get('upper')
        if upper:
            roots.append(upper)
        roots.extend(data.get('top_replies') or [])
    else:
        root = data.get('root')
        if root:
            roots.append(root)
    nodes = []
    for root in roots:
        if isinstance(root, dict):
            nodes.append(root)
            if main:
                nodes.extend(c for c in (root.get('replies') or []) if isinstance(c, dict))
    if not main:
        nodes.extend(c for c in (data.get('replies') or []) if isinstance(c, dict))
    return roots, nodes


def _select_closed(nodes, limit):
    """Select in encounter order, adding every materialized ancestor before its reply."""
    by_id, order = {}, []
    for node in nodes:
        cid = str(node.get('rpid', ''))
        if re.fullmatch(r'[1-9][0-9]*', cid) and cid not in by_id:
            by_id[cid] = node
            order.append(cid)
    selected, unresolved = [], set()
    chosen = set()
    for cid in order:
        chain, seen, cursor = [], set(), cid
        while cursor not in chosen:
            if cursor in seen or cursor not in by_id:
                unresolved.add(cid)
                chain = []
                break
            seen.add(cursor)
            node = by_id[cursor]
            chain.append(cursor)
            parent, root = str(node.get('parent', 0)), str(node.get('root', 0))
            if parent == '0' and root == '0':
                break
            if parent == '0' or root == '0' or root not in by_id:
                unresolved.add(cid)
                chain = []
                break
            cursor = parent
        additions = [x for x in reversed(chain) if x not in chosen]
        if limit is None or len(chosen) + len(additions) <= limit:
            for item in additions:
                if item not in chosen:
                    chosen.add(item)
                    selected.append(item)
    return selected, sorted(unresolved, key=int), len(by_id)


def _atomic_json(path, value):
    path = Path(path)
    temporary = path.with_name(path.name + '.tmp')
    content = (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode('utf-8')
    with temporary.open('wb') as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _page_state():
    return {'main': {'next_batch': 1, 'next_offset': '', 'complete': False, 'offsets': [], 'all_count_values': []},
        'roots': {}, 'root_order': [], 'files': [], 'nodes': [], 'page_count': 0,
        'warnings': [], 'selected': [], 'unresolved': [], 'raw_unique': 0}


def _apply_page(state, receipt, payload):
    """Validate a journal entry and reconstruct the exact next request without network."""
    request = receipt['request']
    data = payload['data']
    if request['kind'] == 'main':
        main = state['main']
        if main['complete'] or request['batch'] != main['next_batch'] or request['offset'] != main['next_offset']:
            raise CollectionError('一级评论检查点顺序不一致，禁止跳页或重复采集。')
        cursor = data.get('cursor') or {}
        if cursor.get('mode') != 2 or type(cursor.get('is_end')) is not bool:
            raise CollectionError('一级评论分页状态无效。')
        if request['offset'] in main['offsets']:
            raise CollectionError('一级评论分页 offset 重复，已停止。')
        roots, nodes = _raw_nodes(data, True)
        if not (data.get('replies') or []) and not cursor['is_end']:
            raise CollectionError('一级评论在分页结束前返回空页，已停止。')
        next_offset = None if cursor['is_end'] else (cursor.get('pagination_reply') or {}).get('next_offset')
        if not cursor['is_end'] and (not isinstance(next_offset, str) or not next_offset or next_offset in main['offsets'] or next_offset == request['offset']):
            raise CollectionError('一级评论下一页标识缺失或重复，已停止。')
        # Validate before mutating reconstructed state.
        main['offsets'].append(request['offset'])
        main.update(next_batch=request['batch'] + 1, next_offset=next_offset, complete=cursor['is_end'])
        if type(cursor.get('all_count')) is int and cursor['all_count'] not in main['all_count_values']:
            main['all_count_values'].append(cursor['all_count'])
        for root in roots:
            if not isinstance(root, dict):
                continue
            rid = str(root.get('rpid', ''))
            if not re.fullmatch(r'[1-9][0-9]*', rid) or type(root.get('rcount')) is not int or root['rcount'] < 0:
                raise CollectionError('一级评论或置顶评论的编号/回复数无效。')
            if rid not in state['roots']:
                state['root_order'].append(rid)
                state['roots'][rid] = {'root_comment_id': rid, 'snapshot_reply_count': root['rcount'],
                    'next_page': 1, 'pages': 0, 'complete': root['rcount'] == 0,
                    'page_states': [], 'api_counts': [], 'reply_ids': [], 'error': None}
            elif root['rcount'] != state['roots'][rid]['snapshot_reply_count']:
                warning = f'回复树 {rid} 的回复数在采集中发生变化。'
                if warning not in state['warnings']:
                    state['warnings'].append(warning)
                if root['rcount'] > 0 and state['roots'][rid]['pages'] == 0:
                    state['roots'][rid]['complete'] = False
    else:
        rid = request['root']
        tree = state['roots'].get(rid)
        if tree is None or tree['complete'] or request['page'] != tree['next_page']:
            raise CollectionError('回复树检查点顺序不一致，禁止跳页或重复采集。')
        page = data.get('page') or {}
        if any(type(page.get(k)) is not int for k in ('num', 'size', 'count')) or page['num'] != request['page'] or page['size'] <= 0 or page['count'] < 0:
            raise CollectionError('回复分页状态无效。')
        replies = data.get('replies') or []
        if not isinstance(replies, list) or any(not isinstance(x, dict) or str(x.get('root')) != rid for x in replies):
            raise CollectionError('回复页包含错误的所属回复树。')
        ids = [str(x.get('rpid', '')) for x in replies]
        if any(not re.fullmatch(r'[1-9][0-9]*', x) for x in ids) or len(set(ids)) != len(ids):
            raise CollectionError('回复页评论编号无效或重复。')
        signature = hashlib.sha256(json.dumps([page['count'], ids]).encode()).hexdigest()
        if signature in tree['page_states']:
            raise CollectionError('回复分页内容重复，已停止。')
        complete = page['num'] * page['size'] >= page['count']
        if not replies and not complete:
            raise CollectionError('回复分页在结束前出现空页，已停止。')
        if set(ids) & set(tree['reply_ids']):
            warning = f'回复树 {rid} 存在跨页重复评论，已按唯一编号统计。'
            if warning not in state['warnings']:
                state['warnings'].append(warning)
        if tree['api_counts'] and page['count'] not in tree['api_counts']:
            state['warnings'].append(f'回复树 {rid} 的 API 总数在分页中发生变化。')
        tree['page_states'].append(signature)
        tree['api_counts'].append(page['count'])
        tree['reply_ids'] = list(dict.fromkeys(tree['reply_ids'] + ids))
        tree.update(next_page=request['page'] + 1, pages=tree['pages'] + 1, complete=complete, error=None)
        if complete and len(tree['reply_ids']) != page['count']:
            state['warnings'].append(f'回复树 {rid} 已到分页终点，但唯一回复数与 API count 不一致。')
        _, nodes = _raw_nodes(data, False)
    # The selection graph needs IDs only. Rich raw comments stay in immutable page files,
    # avoiding repeatedly copying profile/UI payloads during a long full collection.
    state['nodes'].extend({key:node.get(key) for key in ('rpid','root','parent')} for node in nodes)
    state['files'].append(receipt['record'])
    state['page_count'] += 1


def collect(video: dict, run_dir: Path, limit: int | None = 100, progress=None,
            mode: str = 'quick', control=None) -> tuple[dict, dict]:
    """Quick sample or all visible API pages, with immutable page journals and resume."""
    if mode not in ('quick', 'full'):
        raise ValueError('采集模式必须为 quick 或 full。')
    if mode == 'full':
        limit = None
    elif type(limit) is not int or not 1 <= limit <= 500:
        raise ValueError('快速模式评论预算必须为 1 到 500 条。')
    run_dir = _run_path(run_dir)
    raw_dir = run_dir / 'raw'
    raw_dir.mkdir(exist_ok=True)
    pages_dir = raw_dir / 'pages'
    pages_dir.mkdir(exist_ok=True)
    checkpoint_path = run_dir / 'collection_checkpoint.json'
    bvid, oid = normalize_bvid(video['bvid']), str(video['oid'])
    if not re.fullmatch(r'[1-9][0-9]*', oid):
        raise ValueError('视频 OID 无效。')
    existing_checkpoint = checkpoint_path.exists()
    if existing_checkpoint:
        checkpoint = json.loads(checkpoint_path.read_text(encoding='utf-8'))
        if checkpoint.get('version') != 2 or any(checkpoint.get(k) != v for k, v in {'bvid':bvid, 'oid':oid, 'mode':mode, 'limit':limit}.items()):
            raise ValueError('续采配置与任务检查点不一致，请创建新任务。')
        stored_video = json.loads((run_dir / 'video.json').read_text(encoding='utf-8'))
        if video.get('resolution_evidence') != stored_video.get('resolution_evidence'):
            raise ValueError('续采须使用本任务已保存的 video.json，不能替换视频解析证据。')
        if checkpoint.get('status') == 'partial' and (run_dir / 'standard_comments.json').exists():
            raise ValueError('本任务已发布部分分析结果；获取更多评论请创建新任务。')
    else:
        if (run_dir / 'source_file_manifest.json').exists() or (run_dir / 'standard_comments.json').exists():
            raise ValueError('旧任务没有续采检查点，请创建新任务以保留既有产物。')
        view_raw = video.get('_resolution_raw')
        if not isinstance(view_raw, str):
            raise ValueError('缺少 resolve_video 返回的真实视频映射证据。')
        metadata_name = 'video_page.html' if video.get('resolution_evidence', {}).get('source') == 'public_video_page' else 'video_view.json'
        with (raw_dir / metadata_name).open('xb') as handle:
            handle.write(view_raw.encode('utf-8'))
        _write_json(run_dir / 'video.json', {k:v for k,v in video.items() if not k.startswith('_')})
        checkpoint = {'version':2, 'bvid':bvid, 'oid':oid, 'mode':mode, 'limit':limit,
            'started_at':utc_now(), 'status':'collecting', 'total_request_attempts':0,
            'failure_history':[], 'last_error':None}
        _atomic_json(checkpoint_path, checkpoint)
    state = _page_state()

    def refresh_selection():
        state['selected'], state['unresolved'], state['raw_unique'] = _select_closed(state['nodes'], limit)

    def save_checkpoint(status='collecting', **extra):
        checkpoint.update(status=status, updated_at=utc_now(), committed_pages=state['page_count'],
            files=state['files'], main=state['main'], root_order=state['root_order'],
            roots=state['roots'], selected_comments=len(state['selected']), **extra)
        _atomic_json(checkpoint_path, checkpoint)

    def check_control():
        if control:
            control()

    # Committed page directories are the durable source of truth even if a process died
    # between committing the page and refreshing collection_checkpoint.json.
    try:
        receipts = []
        for folder in pages_dir.iterdir():
            if folder.name.startswith('.') or not folder.is_dir():
                continue
            receipt_path = folder / 'receipt.json'
            if not receipt_path.is_file():
                raise CollectionError('采集页面目录缺少 receipt，检查点已损坏。')
            receipts.append(json.loads(receipt_path.read_text(encoding='utf-8')))
        receipts.sort(key=lambda x:x['sequence'])
        for expected, receipt in enumerate(receipts, 1):
            check_control()
            if receipt['sequence'] != expected:
                raise CollectionError('采集页面日志序号不连续，检查点已损坏。')
            rel = receipt['record']['raw_file']
            path = (PROJECT / rel).resolve()
            if not path.is_relative_to(pages_dir) or hashlib.sha256(path.read_bytes()).hexdigest() != receipt['record']['sha256']:
                raise CollectionError('已保存原始页面 SHA 或路径不匹配，禁止续采。')
            _apply_page(state, receipt, _payload(path.read_bytes()))
        refresh_selection()
        if checkpoint.get('status') == 'completed':
            manifest = json.loads((run_dir / 'source_file_manifest.json').read_text(encoding='utf-8'))
            summary = json.loads((run_dir / 'collection_summary.json').read_text(encoding='utf-8'))
            if manifest != {'schema_version':'0.1','files':state['files']}:
                raise CollectionError('已完成任务的来源清单与页面日志不一致。')
            return manifest, summary
        save_checkpoint(last_error=None)
        check_control()
    except CooperativePause as exc:
        save_checkpoint('paused', pause_reason=exc.reason)
        raise

    requests_this_call, last_request = 0, None
    failure = None
    quick_skipped = set()
    mixin = None

    def emit(message, **extra):
        if progress:
            progress({'message':message, 'collected':len(state['selected']), 'mode':mode,
                'raw_unique_comments':state['raw_unique'], 'raw_response_pages':state['page_count'],
                'main_pages':state['main']['next_batch']-1,
                'reply_trees_complete':sum(x['complete'] for x in state['roots'].values()),
                'reply_trees_total':len(state['roots']), **extra})

    def cool_down_if_needed():
        if mode != 'full':
            return
        deadline = checkpoint.get('cooldown_until_epoch')
        if deadline is None and checkpoint.get('requests_since_cooldown',0) < FULL_BURST_REQUESTS:
            return
        if deadline is None:
            deadline = time.time() + FULL_COOLDOWN_SECONDS
            checkpoint['cooldown_until_epoch'] = deadline
            save_checkpoint(waiting_for='rate_cooldown')
        remaining = max(0, deadline - time.time())
        emit(f'已保存采集进度，自动冷却 {remaining:.1f} 秒后继续获取剩余分页。',
            waiting=True, wait_seconds=remaining, waiting_for='rate_cooldown')
        while time.time() < deadline:
            check_control()
            time.sleep(min(0.25, max(0, deadline - time.time())))
        check_control()
        checkpoint['requests_since_cooldown'] = 0
        checkpoint['automatic_cooldowns'] = checkpoint.get('automatic_cooldowns',0) + 1
        checkpoint.pop('cooldown_until_epoch', None)
        save_checkpoint(waiting_for=None)
        emit('自动冷却结束，正在继续采集剩余分页。', waiting=False)

    def save_failure(exc):
        raw = getattr(exc, 'raw', None)
        if raw is not None:
            base = raw_dir / f'failure_{checkpoint["total_request_attempts"]:03d}.response'
            path, suffix = base, 1
            while path.exists():
                path = base.with_name(base.stem + f'_{suffix}' + base.suffix)
                suffix += 1
            with path.open('xb') as handle:
                handle.write(raw)

    def request_page(request, endpoint, params):
        nonlocal requests_this_call, last_request
        check_control()
        cool_down_if_needed()
        if mode == 'quick' and requests_this_call >= MAX_REQUESTS:
            raise CollectionError('快速模式达到请求边界；结果仅为部分样本。')
        if last_request is not None:
            time.sleep(max(0, REQUEST_INTERVAL - (time.monotonic() - last_request)))
        check_control()
        requests_this_call += 1
        checkpoint['total_request_attempts'] += 1
        checkpoint['requests_since_cooldown'] = checkpoint.get('requests_since_cooldown',0) + 1
        save_checkpoint()
        raw, received = _request(API + endpoint + '?' + urlencode(params, quote_via=quote))
        last_request = time.monotonic()
        try:
            payload = _payload(raw)
            sequence = state['page_count'] + 1
            logical = f'main_{request["batch"]:06d}' if request['kind']=='main' else f'reply_{request["root"]}_{request["page"]:06d}'
            final_dir = pages_dir / logical
            if final_dir.exists():
                raise CollectionError('目标页面已存在但检查点未消费，禁止覆盖。')
            record = {'raw_file':(final_dir / 'response.json').relative_to(PROJECT).as_posix(),
                'sha256':hashlib.sha256(raw).hexdigest(), 'source_endpoint':endpoint,
                'source_run':run_dir.name, 'run_time':checkpoint['started_at'],
                'run_time_basis':'collector', 'received_at':received}
            receipt = {'version':1,'sequence':sequence,'request':request,'record':record}
            # Validate the complete next state before publishing the raw/receipt pair.
            import copy
            candidate = copy.deepcopy(state)
            _apply_page(candidate, receipt, payload)
            pending_dir = Path(tempfile.mkdtemp(prefix='.pending-', dir=pages_dir))
            with (pending_dir / 'response.json').open('xb') as handle:
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
            _atomic_json(pending_dir / 'receipt.json', receipt)
            os.replace(pending_dir, final_dir)
            state.clear()
            state.update(candidate)
            refresh_selection()
            save_checkpoint()
            emit('正在采集全部可见分页并保存检查点。' if mode=='full' else '正在获取评论并保留父评论关系。')
            check_control()  # A pause here leaves a complete, replayable committed page.
        except CollectionError as exc:
            if exc.raw is None:
                exc.raw = raw
            raise

    try:
        while True:
            check_control()
            selected_set = set(state['selected'])
            pending = []
            for rid in state['root_order']:
                tree = state['roots'][rid]
                if tree['complete'] or rid in quick_skipped or (mode=='quick' and rid not in selected_set):
                    continue
                if mode=='quick' and tree['pages'] > 0 and (len(state['selected']) >= limit or tree['pages'] >= MAX_PAGES_PER_ROOT):
                    quick_skipped.add(rid)
                    continue
                pending.append(rid)
            # Full collection first exhausts the independently accessible main endpoint.
            # Then fetch every ordinary/pinned root's reply pages; a restricted reply
            # endpoint remains an explicit stop and is never bypassed or replaced.
            if pending and (mode == 'quick' or state['main']['complete']):
                rid = pending[0]
                tree = state['roots'][rid]
                request_page({'kind':'reply','root':rid,'page':tree['next_page']}, REPLY_ENDPOINT,
                    {'oid':oid,'type':1,'root':rid,'pn':tree['next_page'],'ps':20})
                continue
            if state['main']['complete'] or (mode=='quick' and len(state['selected']) >= limit):
                break
            if mixin is None:
                check_control()
                mixin = _mixin_key()
            main = state['main']
            params = {'oid':oid,'type':1,'mode':2,'plat':1,
                'pagination_str':json.dumps({'offset':main['next_offset']},separators=(',',':')),
                'seek_rpid':'','web_location':1315875}
            request_page({'kind':'main','batch':main['next_batch'],'offset':main['next_offset']},
                MAIN_ENDPOINT, _legacy().sign(params,mixin))
    except CooperativePause as exc:
        save_checkpoint('paused', pause_reason=exc.reason)
        raise
    except (CollectionError, HTTPError, URLError, TimeoutError, OSError) as exc:
        failure = exc
        save_failure(exc)
        checkpoint['failure_history'].append({'at':utc_now(),'message':str(exc),
            'status':getattr(exc,'status',None),'restricted':getattr(exc,'restricted',False)})
        save_checkpoint('partial', last_error=str(exc))

    refresh_selection()
    reply_status = [{**{k:v for k,v in state['roots'][rid].items() if k not in ('page_states','reply_ids')},
        'actual_unique_replies':len(state['roots'][rid]['reply_ids'])} for rid in state['root_order']]
    pending_trees = [x for x in reply_status if not x['complete']]
    warnings = list(state['warnings'])
    reasons = []
    if failure is not None:
        reasons.append(str(failure))
    if not state['main']['complete']:
        reasons.append('一级评论分页尚未实际结束。')
    if pending_trees:
        reasons.append(f'{len(pending_trees)} 棵回复树尚未完成全部分页。')
    if state['unresolved']:
        reasons.append(f'{len(state["unresolved"])} 条回复缺少可闭合父链，未纳入分析。')
    if state['raw_unique'] > len(state['selected']):
        reasons.append('原始对象多于可纳入分析的评论；存在预算截取或父链缺失。')
    if mode == 'quick' and quick_skipped:
        reasons.append('快速模式回复分页受评论预算或页数边界限制。')
    warnings.extend(reasons)
    complete = state['main']['complete'] and not pending_trees and not reasons and not state['warnings']
    truncated = not state['main']['complete'] or bool(pending_trees) or state['raw_unique'] > len(state['selected'])
    summary = {'bvid':bvid,'oid':oid,'mode':mode,'limit':limit,'analysis_comment_budget':limit,
        'selected_comment_ids':state['selected'],'selected_comments':len(state['selected']),
        'raw_unique_comments':state['raw_unique'],'raw_response_pages':state['page_count'],
        'request_attempts':checkpoint['total_request_attempts'],'requests_this_invocation':requests_this_call,
        'automatic_cooldowns':checkpoint.get('automatic_cooldowns',0),
        'main_pagination_complete':state['main']['complete'],'reply_trees':reply_status,
        'reported_comment_count':video.get('reported_comment_count'),
        'platform_all_count_values':state['main']['all_count_values'],
        'reported_comment_gap':(video['reported_comment_count']-state['raw_unique']
            if type(video.get('reported_comment_count')) is int else None),
        'sample':mode=='quick','truncated':truncated,'partial':not complete,'complete':complete,
        'collection_complete':complete,'partial_reasons':reasons,'unresolved_comment_ids':state['unresolved'],
        'warnings':list(dict.fromkeys(warnings)), 'started_at':checkpoint['started_at'],'finished_at':utc_now(),
        'failure_history':checkpoint['failure_history'], 'checkpoint_version':2,
        'raw_format_note':'原始 JSON 表示字节；标准 HTTP gzip 先解压，不重新排版或改写 JSON。',
        'budget_note':('全量模式没有评论数量上限；以一级与各回复树实际分页终点判断完成。'
            if mode=='full' else '快速模式预算限制进入分析的唯一评论数；接口原始整页可能包含更多对象。')}
    manifest = {'schema_version':'0.1','files':state['files']}
    # Pause never reaches publication: partial publication is reserved for explicit platform failures/boundaries.
    _atomic_json(run_dir / 'source_file_manifest.json', manifest)
    _atomic_json(run_dir / 'collection_summary.json', summary)
    save_checkpoint('completed' if complete or (mode=='quick' and failure is None) else 'partial',
        last_error=str(failure) if failure else None)
    if not state['selected']:
        if failure is not None:
            raise CollectionError(str(failure), status=getattr(failure,'status',None),
                restricted=getattr(failure,'restricted',False)) from failure
        raise CollectionError('没有获得可形成完整父链的评论；空包不能通过现有 Schema。')
    return manifest, summary
