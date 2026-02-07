import logging
import asyncio
from typing import List, Dict, Optional, Any
from pydantic import BaseModel, ConfigDict

from openjiuwen.core.retrieval.simple_knowledge_base import SimpleKnowledgeBase
from openjiuwen.core.retrieval.common.config import (
    KnowledgeBaseConfig,
    RetrievalConfig,
    EmbeddingConfig,
    VectorStoreConfig
)
from openjiuwen.core.retrieval.embedding.api_embedding import APIEmbedding
from openjiuwen.core.retrieval.vector_store.milvus_store import MilvusVectorStore

from jiuwen_deepsearch.config.config import NativeKnowledgeBaseConfig
from jiuwen_deepsearch.common.common_constants import MAX_SEARCH_CONTENT_LENGTH
from jiuwen_deepsearch.utils.log_utils.log_manager import LogManager

logger = logging.getLogger(__name__)


class NativeLocalSearchAPIWrapper(BaseModel):
    """驱动 agent core 层的 SimpleKnowledgeBase"""
    knowledge_base_configs: List[NativeKnowledgeBaseConfig]
    max_local_search_results: int = 5
    recall_threshold: float = 0.5
    
    _kb_instances: Optional[List[SimpleKnowledgeBase]] = None
    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        extra='ignore'  # 忽略掉config中多余的search_url等字段
    )
    
    async def _init_kb_instances(self):
        """从配置创建实例"""
        if self._kb_instances is not None:
            return self._kb_instances

        instances = []
        for kb_cfg in self.knowledge_base_configs:
            try:
                # 创建 Embedding 实例
                embed_cfg = EmbeddingConfig(
                    model_name=kb_cfg.embed_model_config.model_name,
                    api_key=kb_cfg.embed_model_config.api_key,
                    base_url=kb_cfg.embed_model_config.base_url
                )
                embed_model = APIEmbedding(
                    config=embed_cfg,
                    max_batch_size=kb_cfg.embed_model_config.max_batch_size,
                                        timeout=kb_cfg.embed_model_config.timeout,
                    max_retries=kb_cfg.embed_model_config.max_retries,
                )

                # 创建 Vector Store 实例
                vs_config = VectorStoreConfig(
                    collection_name=kb_cfg.vector_store.collection_name
                )
                
                vector_store = MilvusVectorStore(
                    config=vs_config,
                    milvus_uri=kb_cfg.vector_store.uri,
                    milvus_token=kb_cfg.vector_store.token
                )

                # 创建 SimpleKnowledgeBase
                kb_instance = SimpleKnowledgeBase(
                    config=KnowledgeBaseConfig(
                        kb_id=kb_cfg.id,
                        index_type=kb_cfg.index_type
                    ),
                    vector_store=vector_store,
                    embed_model=embed_model
                )
                instances.append(kb_instance)

            except Exception as e:
                if LogManager.is_sensitive():
                    logger.error(f"[Native Search] Failed to init Knowledge Base!")
                else:
                    logger.error(
                        f"[Native Search] Failed to init Knowledge Base {kb_cfg.id}! Error: {e}"
                    )
                continue
        
        self._kb_instances = instances
        return instances

    async def aopen(self) -> None:
        """Initialize KB resources when run starts"""
        if self._kb_instances is not None:
            return
        await self._init_kb_instances()

    async def aresults(self, query: str) -> List[Dict[str, Any]]:
        """Async search using native local search."""
        return await self._async_search(query, num=self.max_local_search_results)
    
    async def _async_search(self, search_term: str, num: int) -> List[Dict[str, Any]]:
        kbs = self._kb_instances
        if not kbs:
            return []

        async def _single_kb_retrieve(kb):
            try:
                config = RetrievalConfig(top_k=num, score_threshold=self.recall_threshold)
                raw_list = await kb.retrieve(query=search_term, config=config)
                return [{"raw": r, "kb_id": kb.config.kb_id} for r in raw_list]
            except Exception as e:
                if LogManager.is_sensitive():
                    logger.warning(f"[Native Search] Retrieval failed for a KB!")
                else:
                    logger.warning(f"[Native Search] Retrieval failed for KB {kb.config.kb_id}: {e}")
                return []

        tasks = [_single_kb_retrieve(kb) for kb in kbs]
        nested_results = await asyncio.gather(*tasks)

        all_wrapped_results = [item for sublist in nested_results for item in sublist]

        return self._process_and_format(all_wrapped_results, num)

    def _process_and_format(self, all_wrapped: List[Dict], num: int) -> List[Dict[str, Any]]:
        """对结果排序并格式化输出"""
        if not all_wrapped:
            return []

        sorted_items = sorted(all_wrapped, key=lambda x: x["raw"].score, reverse=True)
        top_items = sorted_items[:num]

        # 映射到最终输出结构
        final_results = []
        for item in top_items:
            r = item["raw"]
            kb_id = item["kb_id"]
            
            # 如果 doc_id 为空，使用 chunk_id
            unique_id = f"{r.doc_id}_id_{r.chunk_id}" if r.doc_id else f"missing_id_{r.chunk_id}"
            safe_content = r.text if r.text else ""

            final_results.append({
                "title": unique_id,
                "content": safe_content[:MAX_SEARCH_CONTENT_LENGTH],
                "score": r.score,
                "knowledge_base_id": kb_id,
                "file_id": r.doc_id if r.doc_id else unique_id,
                "document_name": unique_id,
                "chunk_id": r.chunk_id
            })
        return final_results

    async def aclose(self) -> None:
        """释放资源"""
        if not self._kb_instances:
            return

        for kb in self._kb_instances:
            try:
                await kb.close()
            except Exception as e:
                if LogManager.is_sensitive():
                    logger.warning(f"[Native Search] Error closing Knowledge Base!")
                else:
                    logger.warning(
                        f"[Native Search] Error closing Knowledge Base {kb.config.kb_id}: {e}"
                    )
        self._kb_instances = None
