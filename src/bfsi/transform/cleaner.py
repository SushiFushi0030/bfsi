"""Verified metadata adapter around the deterministic Transformer and v0.1 contract."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from ..config import RUNS, DATA_DIR, SCHEMAS
from ..collection import collector as page_reader
from . import transformer, validation as validator

PROJECT = DATA_DIR


def _read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def _publish(path, value):
    content = (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode('utf-8')
    if path.exists():
        if path.read_bytes() != content:
            raise ValueError('已有标准化产物不同，请新建分析任务，禁止覆盖。')
    else:
        with path.open('xb') as handle:
            handle.write(content)


def clean(video: dict, manifest: dict, run_dir: Path) -> dict:
    from jsonschema import Draft202012Validator, FormatChecker, ValidationError

    run_dir = Path(run_dir).resolve()
    if not run_dir.is_relative_to(RUNS.resolve()):
        raise ValueError('标准化只能写入配置的数据目录下的 runs，不能修改源码或历史任务。')
    bvid, oid = video.get('bvid'), str(video.get('oid', ''))
    if not isinstance(bvid, str) or not re.fullmatch(r'BV[A-Za-z0-9]{10}', bvid) or not re.fullmatch(r'[1-9][0-9]*', oid):
        raise ValueError('缺少有效 BV/OID。')
    evidence = video.get('resolution_evidence') or {}
    public_page = evidence.get('source') == 'public_video_page'
    view_path = run_dir / ('raw/video_page.html' if public_page else 'raw/video_view.json')
    if not view_path.is_file():
        raise ValueError('缺少视频解析原始映射证据。')
    view_bytes = view_path.read_bytes()
    if public_page:
        try:
            verified = page_reader.parse_public_video_page(view_bytes, bvid)
        except RuntimeError as exc:
            raise ValueError('公开页面 BV/OID 映射证据无效。') from exc
        source_valid = evidence.get('url') == f'https://www.bilibili.com/zh-Hans/video/{bvid}/'
    else:
        view = json.loads(view_bytes)
        verified = view.get('data') or {}
        source_valid = view.get('code') == 0 and evidence.get('endpoint') == '/x/web-interface/view'
    if not (source_valid and verified.get('bvid') == bvid and str(verified.get('aid')) == oid
            and evidence.get('requested_bvid') == evidence.get('response_bvid') == bvid
            and str(evidence.get('response_oid')) == oid
            and evidence.get('response_sha256') == hashlib.sha256(view_bytes).hexdigest()):
        raise ValueError('BV/OID 映射证据不一致，禁止生成 production 数据。')

    contract = SCHEMAS
    schema = _read(contract / 'standard_comment_schema_v0.1.json')
    manifest_schema = _read(contract / 'source_file_manifest_schema_v0.1.json')
    for document in (schema, manifest_schema):
        Draft202012Validator.check_schema(document)
    Draft202012Validator(manifest_schema, format_checker=FormatChecker()).validate(manifest)
    selection = _read(run_dir / 'collection_summary.json')
    selected_ids = set(selection['selected_comment_ids'])
    if selection.get('bvid') != bvid or str(selection.get('oid')) != oid:
        raise ValueError('采集摘要与当前视频不一致。')
    occurrences, issues, files_by_name = [], [], {}
    for record in manifest['files']:
        rel = record['raw_file']
        path = validator.project_file(rel, PROJECT)
        if not path.is_relative_to(run_dir / 'raw') or hashlib.sha256(path.read_bytes()).hexdigest() != record['sha256']:
            raise ValueError('原始文件路径或指纹校验失败。')
        if not record.get('received_at') or record.get('run_time_basis') != 'collector':
            raise ValueError('生产原件必须保留真实响应接收时间。')
        if rel in files_by_name:
            raise ValueError('来源清单包含重复文件。')
        files_by_name[rel] = record
        values, problems = transformer.enumerate_occurrences(_read(path), record)
        issues.extend(problems)
        for occurrence in values:
            raw = occurrence.raw
            if isinstance(raw, dict) and str(raw.get('rpid')) in selected_ids:
                if str(raw.get('oid')) != oid:
                    raise ValueError('评论原件所属视频与 BV/OID 映射不一致。')
                occurrences.append(occurrence)
    ref = (run_dir / 'source_file_manifest.json').relative_to(PROJECT).as_posix()
    data, quarantine, statistics = transformer.transform(occurrences, ref, ingestion_mode='production')
    # Only fill fields already defined by Schema v0.1, using verified external provenance.
    for comment in data['comments']:
        comment['core']['video_bvid'] = bvid
        for observation in comment['observations']:
            observation['observed_at'] = files_by_name[observation['raw_file']]['received_at']
            observation['observed_at_precision'] = 'request'
    try:
        Draft202012Validator(schema, format_checker=FormatChecker()).validate(data)
        validator.validate_dataset(data, manifest, verify_sources=True, project=PROJECT)
    except (ValueError, ValidationError) as exc:
        # Keep failure evidence visible even when the frozen nonempty-package rule blocks publication.
        _publish(run_dir / 'cleaning_failure.json', {'stage':'standard_validation',
            'quarantine':quarantine,'position_issues':issues,'statistics':statistics,
            'error':str(exc), 'standard_published':False})
        raise ValueError(f'标准化校验失败；隔离来源 {len(quarantine)} 条，未覆盖位置 {len(issues)} 个。') from exc
    # Existing raw validator checks core text/IDs/relations/context. Separately check new metadata.
    for comment in data['comments']:
        assert comment['core']['video_oid'] == oid and comment['core']['video_bvid'] == bvid
        for observation in comment['observations']:
            assert observation['observed_at'] == files_by_name[observation['raw_file']]['received_at']
    budget = selection.get('analysis_comment_budget')
    if budget is not None and len(data['comments']) > budget:
        raise ValueError('标准化结果超过评论预算。')
    output_ids = {comment['core']['comment_id'] for comment in data['comments']}
    unmapped = sorted(selected_ids - output_ids, key=int)
    full_integrity = selection.get('mode','quick') == 'full' and selection.get('complete') is True and not quarantine and not issues and not unmapped
    report = {'schema_version': '0.1', 'ingestion_mode': 'production',
        'mode':selection.get('mode','quick'), 'full_data_integrity':full_integrity,
        'collection_complete':selection.get('complete',False),
        'input_selected_comments': len(selected_ids), 'output_comments': len(data['comments']),
        'unmapped_selected_comment_ids':unmapped,
        'partial':bool(quarantine or issues or unmapped or selection.get('partial')),
        'output_observations': sum(len(x['observations']) for x in data['comments']),
        'quarantine': quarantine, 'position_issues': issues, 'statistics': statistics,
        'validation': {'official_draft_2020_12_metaschema': 'passed', 'json_schema': 'passed',
            'business_and_raw_evidence': 'passed', 'verified_bvid_oid_mapping': 'passed'},
        'warnings': ([f'{len(quarantine)} 条来源被原转换器隔离。'] if quarantine else [])
            + ([f'{len(issues)} 个原件位置未被旧合同覆盖，已记录。'] if issues else [])
            + ([f'{len(unmapped)} 条选定评论未进入标准包，不能声称完整分析。'] if unmapped else [])}
    _publish(run_dir / 'source_file_manifest.json', manifest)
    _publish(run_dir / 'standard_comments.json', data)
    _publish(run_dir / 'cleaning_summary.json', report)
    return data
