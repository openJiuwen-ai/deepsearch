from abc import ABC, abstractmethod
import logging
from typing import Optional, Union

from pydantic import BaseModel

from openjiuwen.core.foundation.store.base_embedding import EmbeddingConfig
from openjiuwen.core.retrieval.embedding.dashscope_embedding import DashscopeEmbedding
from openjiuwen.core.retrieval.embedding.openai_embedding import OpenAIEmbedding
from openjiuwen_deepsearch.algorithm.search_nodes.utils import strip_quotes
from openjiuwen_deepsearch.common.exception import CustomValueException
from openjiuwen_deepsearch.common.status_code import StatusCode
from openjiuwen_deepsearch.utils.common_utils.url_utils import validate_embedding_service_url
from openjiuwen_deepsearch.utils.log_utils.log_manager import LogManager

logger = logging.getLogger(__name__)


class AbstractEmbedder(ABC):
    default_instruction = "Given a web search query, retrieve relevant passages that answer the query" 
    model_name: str
    embed_dim: int

    def __init__(
        self,
        pretrained_model: str,
        api_token: Union[str, bytearray],
        api_url: str,
        timeout: int = 60,
        model_dim: Optional[int] = None,
    ):
        if not api_token:
            raise CustomValueException(
                StatusCode.PARAM_CHECK_ERROR_REQUEST_PARAM_ERROR.code,
                StatusCode.PARAM_CHECK_ERROR_REQUEST_PARAM_ERROR.errmsg.format(
                    e="API token not provided."
                ),
            )
        if not api_url:
            raise CustomValueException(
                StatusCode.PARAM_CHECK_ERROR_REQUEST_PARAM_ERROR.code,
                StatusCode.PARAM_CHECK_ERROR_REQUEST_PARAM_ERROR.errmsg.format(
                    e="Embedding API URL not provided."
                ),
            )
        self.model_name = pretrained_model
        self._user_provided_dim = model_dim is not None
        self.embed_dim = (
            model_dim
            if model_dim is not None
            else 0
        )

        if isinstance(api_token, str):
            self.api_token = bytearray(api_token.encode("utf-8"))
        else:
            self.api_token = bytearray(api_token)
        self.api_url = strip_quotes(api_url)
        validate_embedding_service_url(self.api_url)
        self.timeout = timeout
        

    @abstractmethod
    def get_query_instruction(self, query: str) -> str:
        pass

    @abstractmethod
    def encode(
        self,
        input_texts: list[str],
        is_query: bool = False,
    ) -> list[list[float]]:
        pass


class OpenJiuwenAPIEmbedder(AbstractEmbedder):
    default_instruction = AbstractEmbedder.default_instruction

    def __init__(
        self,
        pretrained_model: str,
        api_token: Union[str, bytearray],
        api_url: str,
        timeout: int = 60,
        model_dim: Optional[int] = None,
        max_retries: int = 3,
    ):
        super().__init__(pretrained_model, api_token, api_url, timeout, model_dim)

        token_str = strip_quotes(self.api_token.decode("utf-8"))
        embed_config = EmbeddingConfig(
            model_name=self.model_name,
            base_url=self.api_url,
            api_key=token_str,
        )
        embed_cls = self.jiuwen_embedding_class_for_url(self.api_url)
        embed_kwargs: dict = {
            "config": embed_config,
            "timeout": timeout,
            "max_retries": max_retries,
        }
        if self.embed_dim > 0:
            embed_kwargs["dimension"] = self.embed_dim
        self.embedder = embed_cls(**embed_kwargs)

        if self.embed_dim <= 0:
            self.embed_dim = int(self.embedder.dimension)
        elif self._user_provided_dim:
            observed_dim = int(self.embedder.dimension)
            if observed_dim != self.embed_dim:
                raise CustomValueException(
                    StatusCode.PARAM_CHECK_ERROR_EMBED_DIMENSION_MODEL_MISMATCH.code,
                    StatusCode.PARAM_CHECK_ERROR_EMBED_DIMENSION_MODEL_MISMATCH.errmsg.format(
                        dimension=self.embed_dim,
                        model=self.model_name,
                        status_code=200,
                    ),
                )

    def get_query_instruction(self, query: str) -> str:
        return f"Instruct: {self.default_instruction}\nQuery:{query}"

    def encode(
        self,
        input_texts: list[str],
        is_query: bool = False,
    ) -> list[list[float]]:
        if is_query:
            input_texts = [self.get_query_instruction(inp) for inp in input_texts]
        try:
            embeddings = self.embedder.embed_documents_sync(input_texts)
        except Exception as e:
            detail = ""
            if not LogManager.is_sensitive():
                detail = str(e)
            raise CustomValueException(
                StatusCode.EMBED_API_CALL_FAILED.code,
                StatusCode.EMBED_API_CALL_FAILED.errmsg.format(
                    status_code=None,
                    detail=detail,
                ),
            ) from e

        if embeddings:
            observed_dim = len(embeddings[0])
            if self.embed_dim <= 0:
                self.embed_dim = observed_dim
            elif self._user_provided_dim and observed_dim != self.embed_dim:
                raise CustomValueException(
                    StatusCode.PARAM_CHECK_ERROR_EMBED_DIMENSION_MODEL_MISMATCH.code,
                    StatusCode.PARAM_CHECK_ERROR_EMBED_DIMENSION_MODEL_MISMATCH.errmsg.format(
                        dimension=self.embed_dim,
                        model=self.model_name,
                        status_code=200,
                    ),
                )
        return embeddings

    @classmethod
    def jiuwen_embedding_class_for_url(cls, api_url: str):
        """DashScope native HTTP API vs OpenAI-compatible base URL (incl. DashScope compatible-mode)."""
        url = api_url.lower()
        if "dashscope.aliyuncs.com" in url and "compatible-mode" not in url:
            return DashscopeEmbedding
        return OpenAIEmbedding
