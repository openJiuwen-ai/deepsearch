import logging
from typing import Any, Optional

from openjiuwen_codesearch.config.config import CodeSearchConfig
from openjiuwen_codesearch.framework.openjiuwen.agent import GraphCodeResolveAgent
from openjiuwen_codesearch.framework.openjiuwen.runtime_context import build_resolve_run_context
from openjiuwen_codesearch.llm.factory import LLMClient

logger = logging.getLogger(__name__)


class CodeResolver:
    """Public API for resolving an issue."""

    def __init__(
        self,
        retriever: Any,
        repo_dir: str,
        config: Optional[CodeSearchConfig] = None,
        main_llm: Optional[LLMClient] = None,
    ) -> None:
        self.config = config or CodeSearchConfig.from_env()
        self.repo_dir = repo_dir
        self.retriever = retriever
        self._main_llm = main_llm
        self.agent = GraphCodeResolveAgent()

    async def resolve(self, query: str, commit: str = "local", max_turns: int = 40) -> str:
        """Runs the resolver to fix the issue and return a diff patch."""
        main_llm = self._main_llm
        if not main_llm:
            from openjiuwen_codesearch.llm.factory import create_llm_client

            main_llm = create_llm_client(self.config.llm.main)

        run_config = self.config.model_copy(deep=True)
        run_config.agent.max_turns = max_turns

        ctx = build_resolve_run_context(
            config=run_config,
            query=query,
            commit=commit,
            repo_dir=self.repo_dir,
            retriever=self.retriever,
            main_llm=main_llm,
        )

        logger.info(f"Starting Agentic Resolver loop for query: {query}")
        return await self.agent.resolve(ctx)
