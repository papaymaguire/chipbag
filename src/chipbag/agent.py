"""A basic agent built on the Claude Agent SDK (Claude Code as a library).

The SDK supplies the agent loop and the built-in tools; this module just picks
which tools are available and prints what the agent does. Nothing is sandboxed:
the tools can reach anything your user account can. The working directory is
the directory the agent was started in.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ResultMessage,
    TextBlock,
    ToolUseBlock,
)

MODEL = "claude-opus-5-5"
TOOLS = ["Bash", "Read", "Write", "Edit", "Glob", "Grep"]


def make_options(root: Path) -> ClaudeAgentOptions:
    return ClaudeAgentOptions(
        cwd=root,
        model=MODEL,
        tools=TOOLS,  # the only built-in tools Claude can see
        allowed_tools=TOOLS,  # run them without asking for approval
        permission_mode="dontAsk",  # anything else that would prompt is denied
        system_prompt={"type": "preset", "preset": "claude_code"},
        setting_sources=[],  # ignore ~/.claude and project .claude settings
    )


def _describe(block: ToolUseBlock) -> str:
    inp = block.input
    detail = inp.get("command") or inp.get("file_path") or inp.get("pattern") or inp.get("path") or ""
    detail = str(detail).replace("\n", " ")
    return f"  → {block.name}: {detail[:200]}"


class Agent:
    """A multi-turn agent session. Use as an async context manager."""

    def __init__(
        self,
        root: Path | None = None,
        on_event: Callable[[str], None] = print,
        options: ClaudeAgentOptions | None = None,
    ):
        """`options` replaces the default general-purpose setup; its `cwd` should be `root`."""
        self.root = (root or Path.cwd()).resolve()
        self.on_event = on_event
        self.client = ClaudeSDKClient(options=options or make_options(self.root))

    async def __aenter__(self) -> Agent:
        await self.client.connect()
        return self

    async def __aexit__(self, *exc) -> None:
        await self.client.disconnect()

    async def send(self, prompt: str) -> ResultMessage | None:
        """Run one user turn to completion; follow-up turns keep the context."""
        await self.client.query(prompt)
        result = None
        async for message in self.client.receive_response():
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        self.on_event(block.text)
                    elif isinstance(block, ToolUseBlock):
                        self.on_event(_describe(block))
            elif isinstance(message, ResultMessage):
                result = message
                if message.is_error:
                    self.on_event(f"[error: {message.subtype}]")
                if message.total_cost_usd is not None:
                    self.on_event(f"[{message.num_turns} turns, ${message.total_cost_usd:.4f}]")
        return result
