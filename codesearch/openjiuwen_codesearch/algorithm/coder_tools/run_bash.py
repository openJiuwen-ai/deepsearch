import subprocess
from .registry import ToolSpec, ToolOutcome

async def execute(ctx, args: dict) -> ToolOutcome:
    command = args.get("command")
    if not command:
        return ToolOutcome(error="Error: command is required.")

    try:
        result = subprocess.run(
            command, shell=True, cwd=ctx.repo_dir, capture_output=True, text=True, timeout=300
        )
        output = f"Exit code: {result.returncode}\n"
        if result.stdout:
            output += (
                f"STDOUT:\n{result.stdout[:2000]}\n"  # Truncate to avoid context explosion
            )
        if result.stderr:
            output += f"STDERR:\n{result.stderr[:2000]}\n"
        return ToolOutcome(message=output)
    except subprocess.TimeoutExpired:
        return ToolOutcome(error="Error: Command timed out after 300 seconds.")
    except Exception as e:
        return ToolOutcome(error=f"Error executing command: {e}")

SCHEMA = {
    "type": "function",
    "function": {
        "name": "run_bash_command",
        "description": "Executes a bash command in the repository root directory. Use this to run tests or verify code changes.",
        "parameters": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "The shell command to execute (e.g. 'pytest tests/').",
                }
            },
            "required": ["command"],
        },
    },
}

SPEC = ToolSpec(name="run_bash_command", schema=SCHEMA, executor=execute)
