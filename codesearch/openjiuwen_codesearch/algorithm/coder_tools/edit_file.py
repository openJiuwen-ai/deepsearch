import os
from .registry import ToolSpec, ToolOutcome


async def execute(ctx, args: dict) -> ToolOutcome:
    file_path = args.get("file_path")
    old_string = args.get("old_string")
    new_string = args.get("new_string")
    replace_all = args.get("replace_all", False)

    if not all([file_path, old_string, new_string is not None]):
        return ToolOutcome(error="Error: file_path, old_string, and new_string are required.")

    full_path = os.path.join(ctx.repo_dir, file_path)
    if not os.path.exists(full_path):
        return ToolOutcome(error=f"Error: File {file_path} does not exist in {ctx.repo_dir}.")

    with open(full_path, "r", encoding="utf-8") as f:
        content = f.read()

    occurrences = content.count(old_string)
    if occurrences == 0:
        return ToolOutcome(
            error="Error: old_string not found in file. Ensure exact "
            "matching including whitespace and indentation."
        )
    if occurrences > 1 and not replace_all:
        return ToolOutcome(
            error=f"Error: Found {occurrences} occurrences of old_string. "
            "Provide a larger unique snippet or set replace_all=true."
        )

    if replace_all:
        new_content = content.replace(old_string, new_string)
    else:
        new_content = content.replace(old_string, new_string, 1)

    with open(full_path, "w", encoding="utf-8") as f:
        f.write(new_content)

    return ToolOutcome(message=f"Successfully edited {file_path}.")


SCHEMA = {
    "type": "function",
    "function": {
        "name": "edit_file",
        "description": (
            "Performs exact string replacements in files. The old_string must uniquely match exactly "
            "one block of text in the file. Preserve exact indentation and spacing."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Path to the file relative to repo root.",
                },
                "old_string": {
                    "type": "string",
                    "description": "The exact string to be replaced. Must be unique in the file.",
                },
                "new_string": {
                    "type": "string",
                    "description": "The new string that will replace the old_string.",
                },
                "replace_all": {
                    "type": "boolean",
                    "description": "If true, replaces all occurrences of old_string. Default is false.",
                },
            },
            "required": ["file_path", "old_string", "new_string"],
        },
    },
}

SPEC = ToolSpec(name="edit_file", schema=SCHEMA, executor=execute)
