from .registry import ToolSpec, ToolOutcome


async def execute(ctx, args: dict) -> ToolOutcome:
    query = args.get("query")
    max_turns = args.get("max_turns", 10)
    
    if not query:
        return ToolOutcome(error="Error: query is required.")

    try:
        original_max_turns = ctx.retriever.config.agent.max_turns
        ctx.retriever.config.agent.max_turns = max_turns
        try:
            result = await ctx.retriever.search(
                query=query,
                revision=ctx.commit,
                top_k=ctx.config.agent.search_topk,
            )
            snippets = result.hits
        finally:
            ctx.retriever.config.agent.max_turns = original_max_turns
        
        context_str = f"Found {len(snippets)} snippets:\n"
        for snip in snippets:
            if isinstance(snip, dict):
                fp = snip.get("file_path", "unknown")
                st = snip.get("start_line", "?")
                en = snip.get("end_line", "?")
                text = snip.get("text", "")
            else:
                fp = getattr(snip, "file_path", "unknown")
                st = getattr(snip, "start_line", "?")
                en = getattr(snip, "end_line", "?")
                text = getattr(snip, "text", "")
            context_str += (
                f"\nFile: {fp} (lines {st}-{en})\n```python\n{text}\n```\n"
            )
        return ToolOutcome(message=context_str)
    except Exception as e:
        return ToolOutcome(error=f"Error retrieving context: {e}")

SCHEMA = {
    "type": "function",
    "function": {
        "name": "retrieve_context",
        "description": (
            "Uses the Retrieval Subagent to deeply search the codebase and return relevant code "
            "snippets. WARNING: This launches a full multi-turn search agent and is expensive/slow. "
            "Formulate comprehensive queries and avoid calling it repeatedly for minor lookups."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "The search query to pass to the retriever (e.g. "
                        "'How does the user authentication work?')."
                    ),
                },
                "max_turns": {
                    "type": "integer",
                    "description": (
                        "The maximum number of agentic search turns the retriever is allowed to use. "
                        "Adjust this depending on the difficulty of the query (e.g. 5 for simple "
                        "lookups, 20 for complex tracing)."
                    ),
                    "default": 10,
                },
            },
            "required": ["query"],
        },
    },
}

SPEC = ToolSpec(name="retrieve_context", schema=SCHEMA, executor=execute)
