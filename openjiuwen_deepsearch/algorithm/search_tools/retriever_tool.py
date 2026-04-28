import os
import sys
from typing import List, Tuple

from openjiuwen_deepsearch.algorithm.search_tools.retrieval.base_retriever import (
    BaseRetriever,
    RetrieveConfig,
)
from openjiuwen_deepsearch.algorithm.search_tools.retrieval.embedder import (
    RemoteQwenEmbedder,
)
from openjiuwen_deepsearch.algorithm.search_tools.retrieval.retriever import (
    BrowsecompPlusMilvusRetriever,
)

sys.path.append(os.path.dirname(os.path.dirname(__file__)))


class Retrieve:
    name = "retrieve"
    description = ""

    def __init__(self, retriever: BaseRetriever):
        self.retriever = retriever

    def call(self, params: dict) -> Tuple[str, List[str]]:
        """
        params: dict = {
            "query": List[str],
            "top_k": int,
            "add_instruction": bool,
            "mode": str,
            "top_k_multiply_factor": int,
            "save_as": None | str
        }
        """
        return self.retriever.retrieve(
            RetrieveConfig(
                query=params["query"],
                top_k=params["top_k"],
                add_instruction=params["add_instruction"],
                mode=params["mode"],
                top_k_multiply_factor=params["top_k_multiply_factor"],
                save_as=params.get("save_as", None),
            )
        )


class RetrieveBrowsecompPlus(Retrieve):
    name = "retrieve"
    description = "Retrieve BrowsecompPlus evidence paragraphs from Milvus."

    def __init__(self, config: dict):
        milvus_host = config.get("milvus_host")
        milvus_port = config.get("milvus_port")
        database_name = config.get("database_name")
        collection_name = config.get("collection_name")
        embedder_model_name = config.get("embedder_model_name")
        embedder_api_key = config.get("embedder_api_key")
        embedder_base_url = config.get("embedder_base_url")
        embedder_timeout = config.get("embedder_timeout")
        embedder = RemoteQwenEmbedder(
            pretrained_model=embedder_model_name,
            api_token=embedder_api_key,
            api_url=embedder_base_url,
            timeout=embedder_timeout,
        )
        retriever = BrowsecompPlusMilvusRetriever(
            milvus_host=milvus_host or "localhost",
            milvus_port=str(milvus_port) if milvus_port else "19530",
            database_name=database_name or "deepsearch_benchmarks",
            collection_name=collection_name or "browsecompplus_with_bm25",
            embedder=embedder,
        )
        super().__init__(retriever)
