import json
from typing import Dict, List, Optional, Tuple

from pydantic import BaseModel
from pymilvus import AnnSearchRequest, WeightedRanker
from pymilvus.client.search_result import SearchResult

from openjiuwen_deepsearch.algorithm.search_tools.retrieval.base_retriever import (
    BaseRetriever,
    RetrieveConfig,
)
from openjiuwen_deepsearch.common.exception import CustomValueException
from openjiuwen_deepsearch.common.status_code import StatusCode


class BrowsecompPlusHitMainBody(BaseModel):
    start_idx: Optional[int] = None


class BrowsecompPlusHitSource(BaseModel):
    prefix: Optional[str] = None
    docid: Optional[str] = None
    chunk_idx: Optional[int] = None
    main_body: Optional[BrowsecompPlusHitMainBody] = None


class BrowsecompPlusHitMetadata(BaseModel):
    title: Optional[str] = None
    authors: Optional[List[str]] = None
    source: Optional[BrowsecompPlusHitSource] = None


class BrowsecompPlusHit(BaseModel):
    id: str
    content: str

    docid: Optional[str] = None
    title: Optional[str] = None
    metadata: Optional[BrowsecompPlusHitMetadata] = None
    score: Optional[float] = None


class BrowsecompPlusCombinedHits(BaseModel):
    content_list: List[str]
    id_list: List[str]
    snippet: str

    doc_id: Optional[str] = None
    title: Optional[str] = None
    metadata: Optional[BrowsecompPlusHitMetadata] = None
    score: Optional[float] = None


class BrowsecompPlusDoc(BaseModel):
    query: str
    results: List[BrowsecompPlusCombinedHits]


class BrowsecompPlusMilvusRetriever(BaseRetriever):
    def _merge_hits_from_same_doc(
        self,
        hits_list: List[BrowsecompPlusHit],
        content_splitter: str = " [...] ",
    ) -> BrowsecompPlusCombinedHits:
        """
        Function for merging BrowseCompPlusHit's coming from the same `docid` and returning the resulting `snippet` in
        a single hit entry-dict.
        """

        score = max([hit.score for hit in hits_list])
        doc_id = hits_list[0].docid
        title = hits_list[0].title
        metadata = hits_list[0].metadata

        # Sort chunks by chunk_idx for proper merging
        hits_list = sorted(
            hits_list,
            key=lambda x: x.metadata.source.chunk_idx,
            reverse=False,
        )

        if hits_list[0].metadata.source.chunk_idx != 0:
            expr = f" id == '{doc_id}__0'"
            res = self.client.query(
                collection_name=self.collection_name,
                filter=expr,
                output_fields=["content"],
                limit=1,
            )
            new_hit_content = res[0]["content"]
            hits_list.insert(
                0, BrowsecompPlusHit(id=f"{doc_id}__0", content=new_hit_content)
            )

        id_list = [hit.id for hit in hits_list]

        content_list = [hit.content for hit in hits_list]
        snippet_list = []
        for idx, hit in enumerate(hits_list):
            if idx > 0:
                # Handle overlap using start_idx if available
                start_idx = hit.metadata.source.main_body.start_idx
                snippet_list.append(hit.content[start_idx:])
            else:
                snippet_list.append(hit.content)

        snippet = content_splitter.join(snippet_list)

        mergedhits = BrowsecompPlusCombinedHits(
            doc_id=doc_id,
            title=title,
            metadata=metadata,
            score=score,
            content_list=content_list,
            snippet=snippet,
            id_list=id_list,
        )

        return mergedhits

    def _return_unique(
        self, hits: List[BrowsecompPlusHit], k: int
    ) -> List[BrowsecompPlusCombinedHits]:
        """
        Retains only a list of unique `docid`s from the returned list of hits.
        Due to chunking `hits` may include more than one chunks from the same `docid`.
        """
        unique_docids: Dict[str, List[BrowsecompPlusHit]] = {}
        for hit in hits:
            docid = hit.docid
            if not docid:
                # Fallback to extracting from ID if docid is not present
                docid = str(hit.get("id", "")).split("__")[0]

            if docid not in unique_docids:
                unique_docids[docid] = [hit]
            else:
                unique_docids[docid].append(hit)

        best_doc_ids = sorted(
            unique_docids.keys(),
            key=lambda x: max([hit.score for hit in unique_docids[x]]),
            reverse=True,
        )
        best_doc_ids = best_doc_ids[:k]
        unique_docids = {docid: unique_docids[docid] for docid in best_doc_ids}

        merged_hits = []
        for docid in unique_docids:
            merged_hits.append(self._merge_hits_from_same_doc(unique_docids[docid]))

        return merged_hits

    def retrieve(
        self,
        retrieve_config: RetrieveConfig,
    ) -> Tuple[str, List[str]]:
        query_vec = self.embedder.encode(
            retrieve_config.query, is_query=retrieve_config.add_instruction
        )

        # Milvus search parameters
        search_params = {
            "metric_type": self.metric_type,
            "params": {"ef": 500},
        }

        # Increase limit to allow for merging multiple chunks from same doc
        search_limit = retrieve_config.top_k * retrieve_config.top_k_multiply_factor

        if retrieve_config.mode == "dense":
            results: SearchResult = self.client.search(
                collection_name=self.collection_name,
                data=query_vec,
                anns_field=self.vector_field,
                search_params=search_params,
                limit=search_limit,
                output_fields=[
                    self.text_field,
                    self.title_field,
                    self.id_field,
                    "docid",
                    "metadata",
                ],
            )
        elif retrieve_config.mode == "hybrid":
            search_param_1 = {
                "data": query_vec,
                "anns_field": self.vector_field,
                "limit": search_limit,
                "param": {"ef": 500},
            }
            request_1 = AnnSearchRequest(**search_param_1)

            # full-text search (sparse)
            search_param_2 = {
                "data": retrieve_config.query,
                "anns_field": self.sparse_field,
                "limit": search_limit,
                "param": {},
            }
            request_2 = AnnSearchRequest(**search_param_2)

            reqs = [request_1, request_2]

            results = self.client.hybrid_search(
                collection_name=self.collection_name,
                reqs=reqs,
                ranker=WeightedRanker(0.6, 0.4),
                limit=search_limit,
                output_fields=[
                    self.text_field,
                    self.title_field,
                    self.id_field,
                    "docid",
                    "metadata",
                ],
            )

        docs: List[BrowsecompPlusDoc] = []
        # Validate that results match query length
        if len(results) != len(retrieve_config.query):
            raise CustomValueException(
                StatusCode.PARAM_CHECK_ERROR_REQUEST_PARAM_ERROR.code,
                StatusCode.PARAM_CHECK_ERROR_REQUEST_PARAM_ERROR.errmsg.format(
                    e=f"Results length ({len(results)}) does not match query length "
                    f"({len(retrieve_config.query)})"
                ),
            )
        for i, result in enumerate(results):
            hits = []
            for hit in result:
                hits.append(
                    BrowsecompPlusHit(
                        id=hit.id,
                        docid=hit.entity.get("docid"),
                        title=hit.entity.get(self.title_field),
                        content=hit.entity.get(self.text_field),
                        metadata=BrowsecompPlusHitMetadata(
                            **hit.entity.get("metadata")
                        ),
                        score=hit.distance,
                    )
                )

            unique_hits = self._return_unique(hits, retrieve_config.top_k)
            docs.append(
                BrowsecompPlusDoc(query=retrieve_config.query[i], results=unique_hits)
            )

        if retrieve_config.save_as:
            with open(retrieve_config.save_as, "w", encoding="utf-8") as f:
                json.dump(
                    [doc.model_dump() for doc in docs], f, ensure_ascii=False, indent=2
                )

        to_return = "\n\n".join(
            [
                json.dumps(
                    {
                        "query": doc.query,
                        "results": [result.snippet for result in doc.results],
                    },
                    ensure_ascii=False,
                )
                for doc in docs
            ]
        )

        id_list = []
        for doc in docs:
            for result in doc.results:
                id_list.extend(result.id_list)

        return (
            to_return,
            id_list,
        )
