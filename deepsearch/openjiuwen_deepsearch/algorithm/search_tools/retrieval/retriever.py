import asyncio
import json
from typing import Dict, List, Optional, Tuple

from pydantic import BaseModel
from pymilvus import AnnSearchRequest, MilvusClient, WeightedRanker
from pymilvus.client.search_result import SearchResult

from openjiuwen.core.foundation.store.base_embedding import EmbeddingConfig
from openjiuwen.core.retrieval.common.config import (
    KnowledgeBaseConfig,
    RetrievalConfig,
    VectorStoreConfig,
)
from openjiuwen.core.retrieval.knowledge_base import KnowledgeBase
from openjiuwen.core.retrieval.simple_knowledge_base import SimpleKnowledgeBase
from openjiuwen.core.retrieval.vector_store.milvus_store import MilvusVectorStore

from openjiuwen_deepsearch.algorithm.search_tools.retrieval.base_retriever import (
    MilvusBaseRetriever,
    RetrieveConfig,
)
from openjiuwen_deepsearch.algorithm.search_tools.retrieval.embedder import (
    AbstractEmbedder,
    OpenJiuwenAPIEmbedder,
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


class BrowsecompPlusMilvusRetriever(MilvusBaseRetriever):
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


class KnowledgeBaseRetriever(MilvusBaseRetriever):
    """
    Retrieves via ``KnowledgeBase.retrieve``.

    Constructor matches ``MilvusBaseRetriever`` (same positional and optional field names).
    Builds a ``SimpleKnowledgeBase`` from Milvus and embedder settings. Milvus connection and
    collection checks are skipped (``connect_milvus=False``); ``self.client`` is ``None``.
    """

    def __init__(
        self,
        milvus_host: str,
        milvus_port: str,
        database_name: str,
        collection_name: str,
        embedder: AbstractEmbedder,
        vector_field: str = "embedding",
        text_field: str = "content",
        sparse_field: str = "content_sparse",
        title_field: str = "title",
        id_field: str = "id",
        metric_type: str = "COSINE",
        client: MilvusClient = None,
        *,
        kb_id: Optional[str] = None,
        index_type: str = "vector",
        milvus_token: str = "",
    ):
        super().__init__(
            milvus_host,
            milvus_port,
            database_name,
            collection_name,
            embedder,
            vector_field=vector_field,
            text_field=text_field,
            sparse_field=sparse_field,
            title_field=title_field,
            id_field=id_field,
            metric_type=metric_type,
            client=client,
            connect_milvus=False,
        )
        self._knowledge_base = self._create_knowledge_base(
            milvus_host=milvus_host,
            milvus_port=milvus_port,
            database_name=database_name,
            collection_name=collection_name,
            embedder=embedder,
            kb_id=kb_id or collection_name,
            index_type=index_type,
            milvus_token=milvus_token,
            vector_field=vector_field,
            text_field=text_field,
            sparse_field=sparse_field,
        )

    @staticmethod
    def _embed_model_from_embedder(embedder: AbstractEmbedder):
        if isinstance(embedder, OpenJiuwenAPIEmbedder):
            return embedder.embedder
        token_str = embedder.api_token.decode("utf-8")
        embed_config = EmbeddingConfig(
            model_name=embedder.model_name,
            base_url=embedder.api_url,
            api_key=token_str,
        )
        embed_cls = OpenJiuwenAPIEmbedder.jiuwen_embedding_class_for_url(embedder.api_url)
        return embed_cls(config=embed_config, timeout=embedder.timeout)

    @classmethod
    def _create_knowledge_base(
        cls,
        *,
        milvus_host: str,
        milvus_port: str,
        database_name: str,
        collection_name: str,
        embedder: AbstractEmbedder,
        kb_id: str,
        index_type: str,
        milvus_token: str,
        vector_field: str,
        text_field: str,
        sparse_field: str,
    ) -> KnowledgeBase:
        milvus_uri = f"http://{milvus_host}:{milvus_port}"
        vs_config = VectorStoreConfig(
            store_provider="milvus",
            collection_name=collection_name,
            database_name=database_name,
        )
        vector_store = MilvusVectorStore(
            config=vs_config,
            milvus_uri=milvus_uri,
            milvus_token=milvus_token or None,
            text_field=text_field,
            vector_field=vector_field,
            sparse_vector_field=sparse_field,
        )
        return SimpleKnowledgeBase(
            config=KnowledgeBaseConfig(kb_id=kb_id, index_type=index_type),
            vector_store=vector_store,
            embed_model=cls._embed_model_from_embedder(embedder),
        )

    def _run_retrieve(self, query: str, retrieval_config: RetrievalConfig):
        return asyncio.run(
            self._knowledge_base.retrieve(query, config=retrieval_config)
        )

    @staticmethod
    def _result_id(res) -> str:
        if getattr(res, "chunk_id", None):
            return str(res.chunk_id)
        if getattr(res, "doc_id", None):
            return str(res.doc_id)
        meta = getattr(res, "metadata", None) or {}
        for key in ("id", "chunk_id", "doc_id"):
            if key in meta and meta[key] is not None:
                return str(meta[key])
        return ""

    def retrieve(
        self,
        retrieve_config: RetrieveConfig,
    ) -> Tuple[str, List[str]]:
        top_k = retrieve_config.top_k * max(1, retrieve_config.top_k_multiply_factor)
        retrieval_config = RetrievalConfig(top_k=top_k)

        docs = []
        id_list: List[str] = []
        for q in retrieve_config.query:
            results = self._run_retrieve(q, retrieval_config)
            entry = {"query": q, "results": [r.text for r in results]}
            if retrieve_config.save_as:
                entry["raw_results"] = [r.model_dump() for r in results]
            docs.append(entry)
            for r in results:
                rid = self._result_id(r)
                if rid:
                    id_list.append(rid)

        if retrieve_config.save_as:
            with open(retrieve_config.save_as, "w", encoding="utf-8") as f:
                json.dump(docs, f, ensure_ascii=False, indent=2)

        to_return = "\n\n".join(
            json.dumps(
                {"query": d["query"], "results": d["results"]},
                ensure_ascii=False,
            )
            for d in docs
        )

        return to_return, id_list
