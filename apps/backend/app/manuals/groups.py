"""Procedure boundaries for editable knowledge entries."""
from __future__ import annotations

import re
from uuid import uuid4


def suggest_groups(steps, title):
    """Suggest only explicit headings; leave all generated boundaries unconfirmed."""
    groups = []
    heading = re.compile(r'^\s*(?:#{1,4}\s+|第[一二三四五六七八九十百\d]+[章节篇]\s*|[一二三四五六七八九十]+、)\S')
    for index, step in enumerate(steps):
        text = step.get('instruction', '').strip()
        is_heading = step.get('heading_hint') or (len(text) <= 100 and '\n' not in text and heading.match(text))
        if not groups or is_heading:
            groups.append({'id': str(uuid4()), 'title': text if is_heading else title,
                           'start': index, 'end': index + 1, 'confirmed': False})
        else:
            groups[-1]['end'] = index + 1
    return groups


def validate_groups(groups, step_count):
    if not groups or step_count < 1:
        raise ValueError('请至少保留一个流程和一段内容')
    cursor, seen = 0, set()
    for group in groups:
        gid = str(group.get('id', ''))
        if not gid or gid in seen or not str(group.get('title', '')).strip():
            raise ValueError('每个流程需要唯一标识和标题')
        if group.get('start') != cursor or not isinstance(group.get('end'), int) or group['end'] <= cursor:
            raise ValueError('流程必须按顺序覆盖所有内容，不能重叠或遗漏')
        cursor = group['end']
        seen.add(gid)
    if cursor != step_count:
        raise ValueError('流程必须按顺序覆盖所有内容，不能重叠或遗漏')
    return groups


def batches(entry):
    """Page within explicit procedures; preserve historic 20-item pagination."""
    steps = entry['data'].get('steps', [])
    groups = entry['data'].get('groups')
    if groups is None:
        groups = [{'id': f'legacy-{start}', 'title': entry['title'], 'start': start,
                   'end': min(start + 20, len(steps))} for start in range(0, len(steps), 20)]
    result = []
    for group in groups:
        for start in range(group['start'], group['end'], 20):
            result.append({'group': group, 'start': start, 'end': min(start + 20, group['end'])})
    return result
