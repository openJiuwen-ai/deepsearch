import os
import shlex
import subprocess

from .registry import ToolSpec, ToolOutcome


def split_command(command: str) -> list[str]:
    """把命令行拆成参数列表，按平台选择分词规则。

    POSIX 模式下 ``shlex.split`` 把反斜杠当转义符，Windows 路径
    ``C:\\tmp\\a.txt`` 会被拆成 ``C:tmpa.txt``，命令直接执行失败。Windows 上改用
    非 POSIX 模式，并去掉成对包裹的引号（非 POSIX 模式会保留引号）。
    """
    if os.name != "nt":
        return shlex.split(command)
    parts = shlex.split(command, posix=False)
    return [
        part[1:-1] if len(part) >= 2 and part[0] == part[-1] and part[0] in "\"'" else part
        for part in parts
    ]


async def execute(ctx, args: dict) -> ToolOutcome:
    command = args.get("command")
    if not command:
        return ToolOutcome(error="Error: command is required.")

    try:
        cmd_args = split_command(command)
        result = subprocess.run(
            cmd_args, shell=False, cwd=ctx.repo_dir, capture_output=True, text=True, timeout=300
        )
        output = f"Exit code: {result.returncode}\n"
        if result.stdout:
            output += f"STDOUT:\n{result.stdout[:2000]}\n"  # Truncate to avoid context explosion
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
        "description": (
            "Executes a bash command in the repository root directory. "
            "Use this to run tests or verify code changes."
        ),
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
