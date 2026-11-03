"""Three-lane retrieval over published knowledge, followed by one shared reranker."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import math
import re
from time import perf_counter

from qdrant_client.http import models

from app.clients.inference import EmbeddingClient, RerankClient
from app.core.config import settings
from app.providers.vector.qdrant_provider import QdrantVectorProvider


def keyword_score(query, text):
    query, text = query.casefold().strip(), text.casefold()
    if not query:
        return 0.0
    terms = set(re.findall(r'[a-z0-9_-]{2,}', query))
    for segment in re.findall(r'[\u4e00-\u9fff]+', query):
        terms.update(segment[i:i + 2] for i in range(len(segment) - 1))
    return (4.0 if query in text else 0.0) + sum(term in text for term in terms) / max(1, len(terms))


@dataclass
class EvidenceHit:
    entry: dict
    step_index: int
    evidence_id: str
    score: float
    lanes: list[str]


@dataclass
class RetrievalResult:
    hits: list[EvidenceHit]
    mode: str
    limitations: list[str]
    metrics: dict


class HybridRetriever:
    def __init__(self, embedding=None, reranker=None, provider_factory=None):
        self.embedding = embedding or EmbeddingClient()
        self.reranker = reranker or RerankClient()
        self.provider_factory = provider_factory or QdrantVectorProvider

    def vector_lanes(self, question, lookup, metrics):
        started = perf_counter()
        encoded = self.embedding.embed([question])
        metrics['embedding_ms'] = round((perf_counter() - started) * 1000, 2)
        started = perf_counter()
        provider = self.provider_factory()
        versions = sorted({str(pair[0]['document_version_id']) for pair in lookup.values()})
        query_filter = models.Filter(must=[
            models.FieldCondition(key='document_version_id', match=models.MatchAny(any=versions)),
            models.FieldCondition(key='chunk_level', match=models.MatchValue(value='child'))])
        sparse = encoded['sparse_vectors'][0]
        points = [provider.client.query_points(
            collection_name=provider.collection_name, using=kind, query=vector,
            query_filter=query_filter, limit=40, with_payload=True).points
            for kind, vector in [('dense', encoded['dense_vectors'][0]),
                                 ('sparse', models.SparseVector(indices=sparse['indices'], values=sparse['values']))]]
        lanes = {}
        for name, rows in zip(('dense', 'sparse'), points):
            ids = []
            for point in rows:
                eid = str(point.id)
                payload = point.payload or {}
                if eid not in lookup:
                    continue
                entry, _ = lookup[eid]
                # Recheck all vector hits against current PostgreSQL authority.
                if (str(payload.get('document_version_id')) != str(entry['document_version_id'])
                        or str(payload.get('manual_entry_id')) != str(entry['id'])
                        or payload.get('chunk_level') != 'child'):
                    continue
                if eid not in ids:
                    ids.append(eid)
            lanes[name] = ids
        metrics['vector_ms'] = round((perf_counter() - started) * 1000, 2)
        return lanes

    def search(self, question, entries, evidence_limit=None):
        started = perf_counter()
        lookup = {str(step['evidence_id']): (entry, index)
                  for entry in entries for index, step in enumerate(entry['data'].get('steps', []))
                  if step.get('evidence_id')}
        metrics = {'dense_count': 0, 'sparse_count': 0, 'keyword_count': 0,
                   'rerank_input_count': 0, 'evidence_count': 0, 'embedding_ms': 0,
                   'vector_ms': 0, 'rerank_ms': 0,
                   'rerank_candidate_top_k': settings.effective_rerank_candidate_top_k,
                   'unindexed_entries': sum(e.get('index_status') != 'indexed' for e in entries)}
        if not lookup:
            return RetrievalResult([], 'empty_corpus', [], metrics)
        lexical_scores = {}
        for eid, (entry, index) in lookup.items():
            step = entry['data']['steps'][index]
            lexical_scores[eid] = (keyword_score(question, entry['title']) * 2
                                   + keyword_score(question, entry.get('product', ''))
                                   + keyword_score(question, step['instruction']) * 3)
        keywords = sorted((eid for eid in lookup if lexical_scores[eid] > 0),
                          key=lambda eid: (-lexical_scores[eid], eid))[:40]
        limitations = []
        lanes = {'keyword': keywords}
        vectors_available = False
        try:
            lanes.update(self.vector_lanes(question, lookup, metrics))
            vectors_available = True
        except Exception as error:
            metrics['vector_error_type'] = type(error).__name__
            limitations.append('向量检索不可用，本次仅从标题和原文关键词召回候选。')
        for name, ids in lanes.items():
            metrics[name + '_count'] = len(ids)
        fused = defaultdict(float)
        for ids in lanes.values():
            for rank, eid in enumerate(ids, 1):
                fused[eid] += 1 / (60 + rank)
        order = sorted(fused, key=lambda eid: (-fused[eid], -lexical_scores[eid], eid))
        budget = settings.effective_rerank_candidate_top_k
        # Keep room for another document before filling the remaining slots.
        candidates, by_document = [], defaultdict(int)
        for eid in order:
            owner = str(lookup[eid][0]['id'])
            if by_document[owner] < max(1, math.ceil(budget / 2)):
                candidates.append(eid)
                by_document[owner] += 1
            if len(candidates) == budget:
                break
        candidates += [eid for eid in order if eid not in candidates][:budget - len(candidates)]
        metrics['rerank_input_count'] = len(candidates)
        scored = []
        mode = 'hybrid_rerank' if vectors_available else 'keyword_rerank'
        if candidates:
            rerank_started = perf_counter()
            try:
                texts = []
                for eid in candidates:
                    entry, index = lookup[eid]
                    texts.append(f"资料：{entry['title']}\n适用：{entry.get('product', '')} · {entry.get('version', '')}\n"
                                 + entry['data']['steps'][index]['instruction'][:1800])
                ranked = self.reranker.rerank(question, texts)
                seen = set()
                for item in ranked:
                    index, score = int(item['index']), float(item['score'])
                    if index in seen or index < 0 or index >= len(candidates) or not math.isfinite(score):
                        raise ValueError('Invalid reranker result')
                    seen.add(index)
                    if score >= settings.knowledge_rerank_min_score:
                        scored.append((candidates[index], score))
                if len(seen) != len(candidates):
                    raise ValueError('Incomplete reranker result')
                scored.sort(key=lambda item: -item[1])
            except Exception as error:
                metrics['rerank_error_type'] = type(error).__name__
                mode = 'keyword_fallback'
                limitations.append('重排序不可用，已明确降级为关键词原文结果；不代表已完成向量与重排筛选。')
                scored = [(eid, lexical_scores[eid]) for eid in keywords]
            metrics['rerank_ms'] = round((perf_counter() - rerank_started) * 1000, 2)
        if metrics['unindexed_entries']:
            limitations.append(f"{metrics['unindexed_entries']} 份资料尚未完成向量索引，仍可参与关键词候选召回。")
        limit = min(evidence_limit or settings.retrieval_evidence_top_k, settings.retrieval_evidence_top_k)
        hits, selected_by_doc = [], defaultdict(int)
        for eid, score in scored:
            entry, index = lookup[eid]
            owner = str(entry['id'])
            if selected_by_doc[owner] >= settings.retrieval_max_evidence_per_document:
                continue
            selected_by_doc[owner] += 1
            hits.append(EvidenceHit(entry, index, eid, score, [lane for lane, ids in lanes.items() if eid in ids]))
            if len(hits) == limit:
                break
        metrics['evidence_count'] = len(hits)
        metrics['total_ms'] = round((perf_counter() - started) * 1000, 2)
        return RetrievalResult(hits, mode, limitations, metrics)
