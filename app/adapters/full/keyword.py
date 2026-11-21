"""KeywordIndex on OpenSearch BM25 (full profile).

Every chunk is a document in one index, with its access-control fields next to the text.
A search runs the BM25 text query with the access rule as a FILTER inside OpenSearch,
built from the same user context as the Qdrant filter, so forbidden chunks are never
scored. The searcher then re-checks each result (defence in depth).

One shard and no replicas: on a single node this keeps BM25's document statistics global,
like the lite index. Scores still differ from rank_bm25 in detail (OpenSearch's analyser
and BM25 parameters), so rankings are measured, not assumed equal.
"""

from collections.abc import Sequence

from sqlalchemy.orm import Session, sessionmaker

from app.ports import AccessScope

KEYWORD_FIELDS = ("chunk_id", "doc_id", "source_id", "source_type", "access_level",
                  "engagement_id", "classification", "effective_date")

MAPPING = {
    "settings": {"index": {"number_of_shards": 1, "number_of_replicas": 0}},
    "mappings": {
        "dynamic": "strict",
        "properties": {
            **{f: {"type": "keyword"} for f in KEYWORD_FIELDS},
            "superseded": {"type": "boolean"},
            "text": {"type": "text", "analyzer": "standard"},
        },
    },
}


def access_filter(scope: AccessScope) -> list[dict]:
    """The access rule as OpenSearch filter clauses (mirrors acl.qdrant_filter)."""
    user = scope.user
    clauses: list[dict] = [{"terms": {"access_level": list(user.allowed_levels)}}]
    if not user.sees_all_engagements:
        # "" = firm-wide document; otherwise only the user's own engagements.
        clauses.append({"terms": {"engagement_id": ["", *sorted(user.engagement_ids)]}})
    if scope.source_types:
        clauses.append({"terms": {"source_type": list(scope.source_types)}})
    return clauses


def _source(chunk: dict) -> dict:
    """The stored document for one chunk: keyword fields ("" when absent), flag, text."""
    doc = {k: chunk.get(k) or "" for k in KEYWORD_FIELDS}
    doc["superseded"] = bool(chunk.get("superseded", False))
    doc["text"] = chunk.get("text", "")
    return doc


class OpenSearchKeywordIndex:
    def __init__(self, url: str, index: str = "chunks", client=None) -> None:
        if client is None:
            from opensearchpy import OpenSearch

            client = OpenSearch(hosts=[url], timeout=30)
        self.client, self.index = client, index
        if not self.client.indices.exists(index=index):
            self.client.indices.create(index=index, body=MAPPING)
        else:
            # An index created before a field was added (e.g. source_id and classification,
            # M12a) gets the new fields; adding fields to a mapping is always allowed.
            self.client.indices.put_mapping(index=index, body={"properties": MAPPING[
                "mappings"]["properties"]})

    def upsert_chunks(self, chunks: Sequence[dict]) -> None:
        from opensearchpy import helpers

        actions = [{"_index": self.index, "_id": c["chunk_id"], "_source": _source(c)}
                   for c in chunks]
        if actions:
            helpers.bulk(self.client, actions)

    def delete_document(self, doc_id: str) -> None:
        self.client.delete_by_query(index=self.index, refresh=True, conflicts="proceed",
                                    body={"query": {"term": {"doc_id": doc_id}}})

    def delete_chunks(self, chunk_ids: Sequence[str]) -> None:
        """Remove these chunks only (M12b); a chunk's OpenSearch _id is its chunk id."""
        from opensearchpy import helpers

        actions = [{"_op_type": "delete", "_index": self.index, "_id": c} for c in chunk_ids]
        if actions:
            helpers.bulk(self.client, actions, raise_on_error=False)  # 404s: already gone

    def set_document_payload(self, doc_id: str, payload: dict) -> None:
        fields = {k: v for k, v in payload.items() if k in (*KEYWORD_FIELDS, "superseded")}
        if not fields:
            return
        script = {"source": "for (e in params.f.entrySet()) { ctx._source[e.getKey()] = e.getValue(); }",
                  "params": {"f": fields}}
        self.client.update_by_query(index=self.index, refresh=True, conflicts="proceed",
                                    body={"query": {"term": {"doc_id": doc_id}}, "script": script})

    def refresh(self, sessions: sessionmaker[Session] | None = None) -> None:
        self.client.indices.refresh(index=self.index)

    def search_scoped(self, query: str, scope: AccessScope,
                      limit: int) -> list[tuple[str, float]]:
        body = {"size": limit, "_source": ["chunk_id"],
                "query": {"bool": {"must": [{"match": {"text": query}}],
                                   "filter": access_filter(scope)}}}
        hits = self.client.search(index=self.index, body=body)["hits"]["hits"]
        return [(h["_source"]["chunk_id"], float(h["_score"])) for h in hits]

    # Helpers for tests and the copy script.
    def count(self, doc_id: str | None = None) -> int:
        body = {"query": {"term": {"doc_id": doc_id}}} if doc_id else None
        return int(self.client.count(index=self.index, body=body)["count"])

    def index_chunk_ids(self) -> dict[str, set[str]]:
        """Every document's chunk ids (a scan over the whole index)."""
        from opensearchpy import helpers

        self.client.indices.refresh(index=self.index)
        found: dict[str, set[str]] = {}
        for hit in helpers.scan(self.client, index=self.index, _source=["doc_id", "chunk_id"],
                                query={"query": {"match_all": {}}}):
            found.setdefault(hit["_source"]["doc_id"], set()).add(hit["_source"]["chunk_id"])
        return found

    def chunk_ids(self, doc_id: str) -> set[str]:
        body = {"size": 10000, "_source": ["chunk_id"], "query": {"term": {"doc_id": doc_id}}}
        return {h["_source"]["chunk_id"]
                for h in self.client.search(index=self.index, body=body)["hits"]["hits"]}
