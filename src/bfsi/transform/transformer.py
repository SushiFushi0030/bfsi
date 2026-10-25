"""Deterministic, offline BFSI v0.1 mapping. No network, profile export or text cleaning."""
from collections import Counter, defaultdict, deque
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import re
from typing import Any, Iterable, Optional

MAIN = '/x/v2/reply/wbi/main'
REPLY = '/x/v2/reply/reply'
FIELDS = {'emote': dict, 'at_name_to_mid': dict, 'at_name_to_mid_str': dict,
          'members': list, 'jump_url': dict, 'pictures': list}
ROOT_ROLES = {'ordinary_root', 'pinned_root', 'reply_api_root_copy'}


class Invalid(ValueError):
    """Controlled, value-free error suitable for a privacy-minimal audit."""
    def __init__(self, code: str, field: Optional[str] = None):
        self.code, self.field = code, field
        super().__init__(code + (':' + field if field else ''))


def require(condition, code, field=None):
    if not condition:
        raise Invalid(code, field)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def integer(value, field, maximum=None):
    require(type(value) is int and value >= 0, 'invalid_nonnegative_integer', field)
    require(maximum is None or value <= maximum, 'integer_out_of_range', field)
    return value


def identifier(value, field, zero=False):
    require(type(value) in (int, str), 'invalid_identifier', field)
    pattern = r'(?:0|[1-9][0-9]*)' if zero else r'[1-9][0-9]*'
    require(re.fullmatch(pattern, str(value)) is not None, 'invalid_identifier', field)
    return None if zero and str(value) == '0' else str(value)


def raw_id(raw, name, zero=False):
    value = identifier(raw.get(name), name, zero)
    if name + '_str' in raw:
        require(identifier(raw[name + '_str'], name + '_str', zero) == value,
                'identifier_copy_conflict', name)
    return value


def core_from(raw):
    require(isinstance(raw, dict), 'comment_not_object')
    content = raw.get('content')
    require(isinstance(content, dict), 'unsupported_content_type', 'content')
    text = content.get('message')
    require(isinstance(text, str), 'invalid_raw_text', 'content.message')
    stamp = integer(raw.get('ctime'), 'ctime', 253402300799)
    return dict(comment_id=raw_id(raw, 'rpid'), video_bvid=None, video_oid=raw_id(raw, 'oid'),
                root_comment_id=raw_id(raw, 'root', True), parent_comment_id=raw_id(raw, 'parent', True),
                raw_text=text, publish_time_raw=stamp,
                published_at=datetime.fromtimestamp(stamp, timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z'))


def states_from(content):
    states = {}
    for field, cls in FIELDS.items():
        if field not in content:
            states[field] = 'missing'
        elif content[field] is None:
            states[field] = 'null'
        else:
            require(type(content[field]) is cls, 'unsupported_content_source_type', 'content.' + field)
            states[field] = 'object' if cls is dict else 'array'
    return states


def minimal_entries(content, source, key_field, value_field):
    result = []
    for i, (key, item) in enumerate(sorted((content.get(source) or {}).items())):
        require(isinstance(key, str) and bool(key), 'invalid_context_key', 'content.' + source)
        require(isinstance(item, dict), 'invalid_context_entry', 'content.' + source)
        value = item.get(value_field)
        require(value is None or isinstance(value, str), 'invalid_context_label', 'content.' + source + '.' + value_field)
        result.append({'source_index': i, key_field: key, 'label' if source == 'emote' else 'title': value})
    return result


def mentions_from(content, text):
    # Account identifiers/names exist ONLY in this function's local memory.
    a, b = content.get('at_name_to_mid') or {}, content.get('at_name_to_mid_str') or {}
    for name in a.keys() & b.keys():
        require(identifier(a[name], 'at_name_to_mid') == identifier(b[name], 'at_name_to_mid_str'),
                'mention_map_conflict', 'content.at_name_to_mid')
    targets = {}
    for field in ['at_name_to_mid_str', 'at_name_to_mid', 'members']:
        values = content.get(field) or ([] if field == 'members' else {})
        sequence = list(enumerate(values)) if field == 'members' else list(enumerate(sorted(values.items())))
        for index, item in sequence:
            if field == 'members':
                require(isinstance(item, dict), 'invalid_member_evidence', 'content.members')
                mid = identifier(item.get('mid'), 'content.members.mid')
                if 'mid_str' in item:
                    require(identifier(item['mid_str'], 'content.members.mid_str') == mid,
                            'mention_identifier_copy_conflict', 'content.members')
                name = item.get('uname')
                require(name is None or isinstance(name, str), 'invalid_mention_name', 'content.members.uname')
            else:
                name, value = item
                require(isinstance(name, str), 'invalid_mention_name', 'content.' + field)
                mid = identifier(value, 'content.' + field)
            group = targets.setdefault(mid, {'local_ref': f'mention_{len(targets)+1}', 'spans': set(), 'source_refs': []})
            group['source_refs'].append({'field': field, 'index': index})
            if name:
                for match in re.finditer(re.escape(name), text):
                    start, end = match.span()
                    group['spans'].add((start - 1 if start and text[start - 1] == '@' else start, end))
    return [{'local_ref': g['local_ref'], 'text_spans': [{'start': a, 'end': b} for a, b in sorted(g['spans'])],
             'source_refs': g['source_refs']} for g in targets.values()]


@dataclass
class Occurrence:
    raw: Any
    file: dict
    path: str
    role: str

    def location(self):
        return {'source_run': self.file['source_run'], 'raw_file': self.file['raw_file'], 'raw_object_path': self.path}


def quarantine_row(occ, code, comment_id=None, field=None, **extra):
    row = {'code': code, 'comment_id': comment_id, **occ.location(), **extra}
    if field:
        row['field'] = field
    return row


def observation_from(occ, core):
    raw, role, content = occ.raw, occ.role, occ.raw['content']
    states = states_from(content)
    root, parent = core['root_comment_id'], core['parent_comment_id']
    require((root is None) == (parent is None), 'incomplete_relationship_pair')
    require((parent is None) == (role in ROOT_ROLES), 'role_relationship_mismatch')
    control = raw.get('reply_control', {})
    require(isinstance(control, dict), 'unsupported_pin_control_type', 'reply_control')
    flag = control.get('is_up_top')
    require(flag is None or type(flag) is bool, 'unsupported_pin_flag_type', 'reply_control.is_up_top')
    require(not (role == 'pinned_root' and flag is False), 'contradictory_pinned_evidence')
    require(not (role == 'pinned_preview_reply' and flag is True), 'contradictory_pinned_preview_evidence')
    pinned = True if role == 'pinned_root' or flag is True else False if flag is False or role != 'reply_api_root_copy' else None
    pictures = content.get('pictures', [])
    return {**occ.location(), 'source_endpoint': occ.file['source_endpoint'], 'source_role': role,
            'observed_at': None, 'observed_at_precision': 'unknown',
            'like_count': integer(raw.get('like'), 'like'), 'reply_count': integer(raw.get('rcount'), 'rcount'),
            'is_pinned': pinned, 'emotes': minimal_entries(content, 'emote', 'token', 'text'),
            'mentions': mentions_from(content, core['raw_text']),
            'links': minimal_entries(content, 'jump_url', 'anchor_text', 'title'),
            'has_picture': None if pictures is None else bool(pictures),
            'picture_count': None if pictures is None else len(pictures), 'content_source_states': states}


def enumerate_occurrences(document, file):
    """Visit approved endpoint-specific slots once. Never recursively collect rpid dictionaries."""
    occurrences, issues = [], []

    def issue(path, code):
        issues.append(quarantine_row(Occurrence(None, file, path, ''), code))

    def slot(obj, path, role, preview=None, optional=False):
        if obj is None and optional:
            return
        occurrences.append(Occurrence(obj, file, path, role))
        if isinstance(obj, dict):
            children = obj.get('replies')
            if children is not None:
                if not isinstance(children, list):
                    issue(path + '/replies', 'unsupported_reply_container')
                elif preview:
                    for j, child in enumerate(children):
                        slot(child, path + f'/replies/{j}', preview)
                elif children:
                    issue(path + '/replies', 'unsupported_nested_comment_position')

    require(isinstance(document, dict) and document.get('code') == 0, 'invalid_response')
    data = document.get('data')
    require(isinstance(data, dict), 'invalid_response_data')
    endpoint = file['source_endpoint']
    require(endpoint in (MAIN, REPLY), 'unsupported_endpoint')
    roots = data.get('replies')
    if roots is not None and not isinstance(roots, list):
        issue('/data/replies', 'unsupported_reply_container')
    else:
        for i, raw in enumerate(roots or []):
            slot(raw, f'/data/replies/{i}', 'ordinary_root' if endpoint == MAIN else 'full_reply',
                 'preview_reply' if endpoint == MAIN else None)
    if endpoint == REPLY:
        slot(data.get('root'), '/data/root', 'reply_api_root_copy', optional=True)
    else:
        top = data.get('top')
        if top is not None:
            if not isinstance(top, dict):
                issue('/data/top', 'unsupported_top_container')
            else:
                slot(top.get('upper'), '/data/top/upper', 'pinned_root', 'pinned_preview_reply', optional=True)
                for name, value in top.items():
                    if name != 'upper' and value is not None:
                        issue('/data/top/' + name, 'unsupported_pinned_slot')
        tops = data.get('top_replies')
        if tops is not None and not isinstance(tops, list):
            issue('/data/top_replies', 'unsupported_top_container')
        else:
            for i, raw in enumerate(tops or []):
                slot(raw, f'/data/top_replies/{i}', 'pinned_root', 'pinned_preview_reply')
    for name in ('hots',):
        if data.get(name):
            issue('/data/' + name, 'unsupported_comment_position')
    return occurrences, issues


def obs_key(obs):
    return tuple(obs[k] for k in ('source_run', 'raw_file', 'raw_object_path'))


def stable(core):
    return {k: v for k, v in core.items() if k != 'depth'}


def relationship_depths(cores):
    """Iterative graph walk + dependency propagation; no recursion/depth business cap."""
    invalid, depths, dependents = {}, {}, defaultdict(set)
    for cid, core in cores.items():
        parent, root = core['parent_comment_id'], core['root_comment_id']
        for target in (parent, root):
            if target is not None:
                dependents[target].add(cid)
        if parent is None or root is None:
            if parent is root is None:
                depths[cid] = 0
            else:
                invalid[cid] = ('incomplete_relationship_pair', [])
        elif cid in (parent, root):
            invalid[cid] = ('self_relationship', [cid])
        elif parent not in cores or root not in cores:
            invalid[cid] = ('missing_relationship_target', sorted({x for x in (parent, root) if x not in cores}))
        elif cores[root]['parent_comment_id'] is not None or cores[root]['root_comment_id'] is not None:
            invalid[cid] = ('root_is_not_top_level', [root])
        elif cores[parent]['video_oid'] != core['video_oid'] or cores[root]['video_oid'] != core['video_oid']:
            invalid[cid] = ('cross_video_relationship', [parent, root])
        elif parent != root and cores[parent]['root_comment_id'] != root:
            invalid[cid] = ('cross_tree_relationship', [parent, root])
    for cid in cores:
        if cid in depths or cid in invalid:
            continue
        chain, position, cursor = [], {}, cid
        while cursor not in depths and cursor not in invalid:
            if cursor in position:
                cycle = chain[position[cursor]:]
                for node in cycle:
                    invalid[node] = ('relationship_cycle', sorted(cycle))
                break
            position[cursor] = len(chain)
            chain.append(cursor)
            cursor = cores[cursor]['parent_comment_id']
        if cursor in depths:
            depth = depths[cursor]
            for node in reversed(chain):
                depth += 1
                depths[node] = depth
    queue = deque(invalid)
    while queue:
        target = queue.popleft()
        for child in sorted(dependents[target]):
            if child not in invalid:
                invalid[child] = ('depends_on_quarantined_comment', [target])
                queue.append(child)
    for cid in invalid:
        depths.pop(cid, None)
    return depths, invalid


def transform(occurrences: Iterable[Occurrence], manifest_ref: str, previous=None, ingestion_mode='historical_fixture'):
    """Append to a separately validated previous version, or conservatively isolate batch conflicts.

    Cold-batch conflicting cores/identities are ALL excluded: no arbitrary winning source.
    With previous, only incoming conflicts are excluded; old data are copied exactly.
    """
    occurrences = list(occurrences)
    existing = deepcopy(previous['comments']) if previous else []
    old = {c['core']['comment_id']: c for c in existing}
    old_obs = {obs_key(o): (c['core']['comment_id'], o) for c in existing for o in c['observations']}
    candidates, quarantine, signatures = [], [], defaultdict(set)
    for occ in occurrences:
        core = None
        try:
            core = core_from(occ.raw)
            signatures[core['comment_id']].add(canonical(core))
            obs = observation_from(occ, core)
            candidates.append((occ, core, obs))
        except Invalid as error:
            cid = core['comment_id'] if core else None
            if cid is None and isinstance(occ.raw, dict):
                try:
                    cid = identifier(occ.raw.get('rpid'), 'rpid')
                except Invalid:
                    pass
            quarantine.append(quarantine_row(occ, error.code, cid, error.field))
    conflicted = {cid for cid, values in signatures.items() if len(values) > 1 and cid not in old}
    identities = defaultdict(set)
    for _, core, obs in candidates:
        identities[obs_key(obs)].add(canonical([core, obs]))
    identity_conflicts = {key for key, values in identities.items() if len(values) > 1 and key not in old_obs}
    comments = deepcopy(old)
    accepted_keys = dict(old_obs)
    imported, duplicates = defaultdict(list), 0
    for occ, core, obs in candidates:
        cid, key = core['comment_id'], obs_key(obs)
        reason = None
        if cid in old and stable(old[cid]['core']) != core:
            reason = 'stable_core_conflict_with_previous'
        elif cid in conflicted:
            reason = 'stable_core_conflict_in_batch'
        elif key in old_obs and old_obs[key] != (cid, obs):
            reason = 'observation_conflict_with_previous'
        elif key in identity_conflicts:
            reason = 'observation_identity_conflict_in_batch'
        if reason:
            quarantine.append(quarantine_row(occ, reason, cid))
            continue
        if key in accepted_keys:
            duplicates += 1
            continue
        comments.setdefault(cid, {'core': core, 'observations': []})['observations'].append(obs)
        accepted_keys[key] = (cid, obs)
        imported[cid].append(occ)
    depths, invalid = relationship_depths({cid: c['core'] for cid, c in comments.items()})
    require(not (old.keys() & invalid.keys()), 'previous_relationship_invalid')
    for cid, (code, dependencies) in invalid.items():
        for occ in imported[cid]:
            quarantine.append(quarantine_row(occ, code, cid, dependency_comment_ids=dependencies))
        comments.pop(cid)
    for cid, comment in comments.items():
        comment['core']['depth'] = depths[cid]
        comment['observations'].sort(key=obs_key)
    dataset = {'schema_version': '0.1', 'ingestion_mode': ingestion_mode, 'file_manifest': manifest_ref,
               'comments': [comments[cid] for cid in sorted(comments, key=int)]}
    quarantine.sort(key=lambda q: (q['raw_file'], q['raw_object_path'], q['code']))
    stats = {'input_occurrences': len(occurrences), 'input_unique_comment_ids': len(signatures),
             'output_comments': len(comments), 'output_observations': sum(len(c['observations']) for c in comments.values()),
             'duplicate_imports_skipped': duplicates, 'quarantined_occurrences': len(quarantine),
             'quarantined_comment_ids': sorted({q['comment_id'] for q in quarantine if q['comment_id']}, key=int),
             'quarantine_reason_counts': dict(Counter(q['code'] for q in quarantine)),
             'previous_comments': len(old), 'previous_observations': len(old_obs)}
    return dataset, quarantine, stats
