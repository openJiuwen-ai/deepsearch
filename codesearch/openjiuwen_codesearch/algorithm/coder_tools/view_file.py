import os
from .registry import ToolSpec, ToolOutcome

async def execute(ctx, args: dict) -> ToolOutcome:
    file_path = args.get("file_path")
    start_line = args.get("start_line")
    end_line = args.get("end_line")
    
    if not file_path:
        return ToolOutcome(error="Error: file_path is required.")

    full_path = os.path.join(ctx.repo_dir, file_path)
    if not os.path.exists(full_path):
        return ToolOutcome(error=f"Error: File {file_path} not found.")
    try:
        with open(full_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        st = (start_line - 1) if start_line else 0
        en = end_line if end_line else len(lines)
        st = max(0, st)
        en = min(len(lines), en)

        output = f"--- {file_path} (Lines {st+1}-{en}) ---\n"
        for i in range(st, en):
            output += f"{i+1}: {lines[i]}"
        return ToolOutcome(message=output)
    except Exception as e:
        return ToolOutcome(error=f"Error reading file: {e}")

SCHEMA = {
    "type": "function",
    "function": {
        "name": "view_file",
        "description": "View the contents of a file. Use this to read the files mentioned in your initial context.",
        "parameters": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Path to the file relative to repo root.",
                },
                "start_line": {
                    "type": "integer",
                    "description": "1-indexed start line (optional)",
                },
                "end_line": {
                    "type": "integer",
                    "description": "1-indexed end line (optional)",
                },
            },
            "required": ["file_path"],
        },
    },
}

SPEC = ToolSpec(name="view_file", schema=SCHEMA, executor=execute)
