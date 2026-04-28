import logging
import time
from typing import Optional, Union

import requests
from pydantic import BaseModel

from openjiuwen_deepsearch.algorithm.search_nodes.utils import strip_quotes
from openjiuwen_deepsearch.common.exception import CustomValueException
from openjiuwen_deepsearch.common.status_code import StatusCode
from openjiuwen_deepsearch.utils.log_utils.log_manager import LogManager

logger = logging.getLogger(__name__)

SUPPORTED_EMBED_MODELS = ("qwen3-embedding-0.6b", "qwen3-embedding-8b")


class QwenEmbedderData(BaseModel):
    index: int
    object: str
    embedding: list[float]


class RemoteQwenEmbedder:
    default_instruction = "Given a web search query, retrieve relevant passages that answer the query" 

    _model2embed_dim = {
        "qwen3-embedding-0.6b": 1024,
        "qwen3-embedding-8b": 4096,
        "qwen3/qwen3-embedding-8b": 4096,
    }

    def __init__(
        self,
        pretrained_model: str,
        api_token: Union[str, bytearray],
        api_url: str,
        timeout: int = 60,
        model_dim: Optional[int] = None,
    ):
        matched = None
        for supported_model in self._model2embed_dim:
            if supported_model in pretrained_model.lower():
                matched = supported_model
                break

        if matched is None and model_dim is None:
            raise CustomValueException(
                StatusCode.PARAM_CHECK_ERROR_REQUEST_PARAM_ERROR.code,
                StatusCode.PARAM_CHECK_ERROR_REQUEST_PARAM_ERROR.errmsg.format(
                    e=(
                        f"Unsupported embedder model: {pretrained_model}. Supported: {SUPPORTED_EMBED_MODELS}. "
                        "Please use suported models or provide the model_dim."
                    )
                ),
            )
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
        self._matched_model = matched
        self._user_provided_dim = model_dim is not None
        self.embed_dim = model_dim or (
            self._model2embed_dim[matched] if matched else 0
        )
        if isinstance(api_token, str):
            self._api_token = bytearray(api_token.encode("utf-8"))
        else:
            self._api_token = bytearray(api_token)
        self._api_url = strip_quotes(api_url)
        self.timeout = timeout
        token_str = strip_quotes(self._api_token.decode("utf-8"))
        self.request_headers = {
            "Authorization": f"Bearer {token_str}",
            "Content-Type": "application/json",
        }

    def get_query_instruction(self, query: str) -> str:
        return f"Instruct: {self.default_instruction}\nQuery:{query}"

    def encode(
        self,
        input_texts: list[str],
        is_query: bool = False,
    ) -> list[list[float]]:
        if is_query:
            input_texts = [self.get_query_instruction(inp) for inp in input_texts]

        response = None
        last_status_code = None
        last_exception = None
        max_try = 10
        for i in range(max_try):
            try:
                payload = {
                    "model": self.model_name,
                    "input": input_texts,
                    "dimensions": self.embed_dim,
                    "encoding_format": "float",
                }
                response = requests.post(
                    self._api_url,
                    json=payload,
                    headers=self.request_headers,
                    timeout=self.timeout,
                )
                response.raise_for_status()
                break
            except requests.exceptions.HTTPError as e:
                last_status_code = e.response.status_code
                last_exception = e
                logger.warning(
                    f"Embedder encode failed. HTTP status: {last_status_code}, error: {e}"
                    f"Try {i} out of {max_try}"
                )
                time.sleep(10)
        else:
            if (
                self._user_provided_dim
                and self._matched_model is not None
                and last_status_code is not None
            ):
                raise CustomValueException(
                    StatusCode.PARAM_CHECK_ERROR_EMBED_DIMENSION_MODEL_MISMATCH.code,
                    StatusCode.PARAM_CHECK_ERROR_EMBED_DIMENSION_MODEL_MISMATCH.errmsg.format(
                        dimension=self.embed_dim,
                        model=self.model_name,
                        status_code=last_status_code,
                    ),
                ) from last_exception
            detail = ""
            if not LogManager.is_sensitive() and last_exception is not None:
                try:
                    body = getattr(last_exception.response, "text", None) or ""
                    detail = f"Response summary: {body[:200]}..." if len(body) > 200 else f"Response: {body}"
                except Exception:
                    detail = "Unable to read response body"
            raise CustomValueException(
                StatusCode.EMBED_API_CALL_FAILED.code,
                StatusCode.EMBED_API_CALL_FAILED.errmsg.format(
                    status_code=last_status_code,
                    detail=detail.strip(),
                ),
            ) from last_exception

        embeddings = [QwenEmbedderData(**data) for data in response.json().get("data")]
        embeddings = [data.embedding for data in embeddings]
        return embeddings
