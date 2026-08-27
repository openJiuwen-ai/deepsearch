from .registry import ToolSpec, ToolOutcome


async def execute(ctx, args: dict) -> ToolOutcome:
    summary = args.get("summary", "")
    return ToolOutcome(message=f"Patch submitted. Summary: {summary}", patch_submitted=True)


SCHEMA = {
    "type": "function",
    "function": {
        "name": "submit_patch",
        "description": "Submit your final resolution when you believe the issue is fixed and verified.",
        "parameters": {
            "type": "object",
            "properties": {
                "summary": {
                    "type": "string",
                    "description": "Summary of the changes made.",
                }
            },
            "required": ["summary"],
        },
    },
}

SPEC = ToolSpec(name="submit_patch", schema=SCHEMA, executor=execute)
