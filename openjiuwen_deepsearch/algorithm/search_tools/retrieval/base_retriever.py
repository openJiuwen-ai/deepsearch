from abc import ABC, abstractmethod
from dataclasses import dataclass
import logging
from typing import List, Optional, Tuple

from pymilvus import MilvusClient

from openjiuwen_deepsearch.algorithm.search_tools.retrieval.embedder import AbstractEmbedder
from openjiuwen_deepsearch.common.exception import CustomValueException
from openjiuwen_deepsearch.common.status_code import StatusCode

logger = logging.getLogger(__name__)


@dataclass
class RetrieveConfig:
    query: List[str]
    top_k: int = 5
    add_instruction: bool = False
    mode: str = "dense"
    top_k_multiply_factor: int = 1
    save_as: Optional[str] = None


class BaseRetriever(ABC):
    """Abstract retriever: maps ``RetrieveConfig`` to aggregated text and id lists."""

    @abstractmethod
    def retrieve(
        self,
        retrieve_config: RetrieveConfig,
    ) -> Tuple[str, List[str]]:
        pass


class MilvusBaseRetriever(BaseRetriever):
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
        connect_milvus: bool = True,
    ):
        self.embedder = embedder
        self.vector_field = vector_field
        self.text_field = text_field
        self.title_field = title_field
        self.id_field = id_field
        self.metric_type = metric_type
        self.collection_name = collection_name
        self.database_name = database_name
        self.sparse_field = sparse_field

        if not connect_milvus:
            self.client = client
            return

        self.client = client or MilvusClient(
            uri=f"http://{milvus_host}:{milvus_port}",
        )

        if database_name and database_name != "default":
            if database_name not in self.client.list_databases():
                error_msg = f"[RETRIEVER] Milvus database '{database_name}' does not exist."
                logger.error(error_msg)
                raise CustomValueException(
                    StatusCode.PARAM_CHECK_ERROR_REQUEST_PARAM_ERROR.code,
                    StatusCode.PARAM_CHECK_ERROR_REQUEST_PARAM_ERROR.errmsg.format(e=error_msg))
            self.client.use_database(database_name)

        if collection_name not in self.client.list_collections():
            error_msg = f"[RETRIEVER] Milvus collection '{collection_name}' does not exist."
            logger.error(error_msg)
            raise CustomValueException(
                StatusCode.PARAM_CHECK_ERROR_REQUEST_PARAM_ERROR.code,
                StatusCode.PARAM_CHECK_ERROR_REQUEST_PARAM_ERROR.errmsg.format(e=error_msg))
        self.client.load_collection(collection_name)
