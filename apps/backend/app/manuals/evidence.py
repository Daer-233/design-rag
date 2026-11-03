"""Expand ranked anchors without merging different documents or inventing steps."""
from collections import OrderedDict
from app.manuals.groups import batches


def assemble_sources(service, hits, question, page, entry_id, mode, force_page=False):
    budget = 24000
    groups = OrderedDict()
    omitted_anchors = 0
    for hit in hits:
        text = hit.entry['data']['steps'][hit.step_index]['instruction']
        pages = batches(hit.entry)
        source_page = page if force_page or page > 1 else next(
            (i + 1 for i, batch in enumerate(pages) if batch['start'] <= hit.step_index < batch['end']), 0)
        selected = pages[source_page - 1] if 0 < source_page <= len(pages) else None
        in_context = bool(selected and selected['start'] <= hit.step_index < selected['end'])
        if not selected:
            continue
        if in_context and len(text) > budget:
            omitted_anchors += 1
            continue
        if in_context:
            budget -= len(text)
        key = (str(hit.entry['id']), source_page)
        group = groups.setdefault(key, {'entry': hit.entry, 'page': source_page, 'hits': []})
        group['hits'].append(hit)
    sources = []
    for group in groups.values():
        entry, source_page = group['entry'], group['page']
        source = service.answer_entry(entry, question, source_page, mode, None, True)
        start = source.get('batch_start', (source_page - 1) * 20)
        anchors = {h.step_index: h for h in group['hits']}
        included = {index for index in anchors if start <= index < start + len(source['steps'])}
        # Anchor text was reserved first. Add remaining source context in original order.
        for offset, step in enumerate(source['steps']):
            index = start + offset
            if index in included:
                continue
            if len(step['instruction']) <= budget:
                included.add(index)
                budget -= len(step['instruction'])
        selected = []
        for offset, step in enumerate(source['steps']):
            index = start + offset
            if index not in included:
                continue
            hit = anchors.get(index)
            step.update(step_index=index, is_match=hit is not None,
                        rerank_score=hit.score if hit else None,
                        retrieval_lanes=hit.lanes if hit else [])
            step['source'].update(entry_id=str(entry['id']), title=entry['title'],
                                  product=entry.get('product'), version=entry.get('version'))
            selected.append(step)
        source.update(steps=selected, matched_evidence_ids=[h.evidence_id for h in group['hits'] if h.step_index in included],
                      retrieval_score=max(h.score for h in group['hits']),
                      context_complete=len(selected) == len(source['steps']),
                      omitted_context_count=len(source['steps']) - len(selected),
                      context_start_index=start, context_end_index=start + len(source['steps']),
                      previous_page=(source_page - 1 if source_page > 1 and
                                     (entry['data'].get('groups') is None or start > batches(entry)[source_page - 1]['group']['start']) else None))
        if selected:
            sources.append(source)
    limitations = []
    if omitted_anchors or any(s['omitted_context_count'] for s in sources):
        limitations.append('当前证据超过上下文预算，已明确标记未展开内容；可按资料分页继续查看，未改写或截断原文段落。')
    return sources, limitations, omitted_anchors
