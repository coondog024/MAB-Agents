"""Thin wrapper around the Anthropic SDK tool runner.

One place owns model choice, effort, prompt caching, refusal fallbacks and the
tool loop, so the agents only define a system prompt and tools.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Callable

import anthropic

DEFAULT_MODEL = "claude-opus-5-5"
FALLBACK_BETA = "server-side-fallback-2026-07-01"


def settings() -> dict[str, Any]:
    return {
        "model": os.getenv("MAB_MODEL", DEFAULT_MODEL),
        "effort": os.getenv("MAB_EFFORT", "medium"),
        "fallbacks": os.getenv("MAB_FALLBACKS", "1") != "0",
        "max_tokens": int(os.getenv("MAB_MAX_TOKENS", "16000")),
        "max_iterations": int(os.getenv("MAB_MAX_ITERATIONS", "12")),
    }


@dataclass
class Reply:
    text: str
    stop_reason: str | None
    messages: list[dict[str, Any]]          # full transcript incl. tool calls, append-only
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    usage: dict[str, int] = field(default_factory=dict)
    served_by: str | None = None

    def refused(self) -> bool:
        return self.stop_reason == "refusal"


def make_client() -> anthropic.Anthropic:
    """Credentials resolve from ANTHROPIC_API_KEY, ANTHROPIC_AUTH_TOKEN or an `ant auth login` profile."""
    return anthropic.Anthropic()


def run(
    agent: Any,
    user_prompt: str,
    *,
    history: list[dict[str, Any]] | None = None,
    client: Any | None = None,
    effort: str | None = None,
    on_tool: Callable[[str, dict[str, Any]], None] | None = None,
) -> Reply:
    """Run one user turn through an agent, executing its tools until Claude finishes.

    `history` is the append-only transcript from a previous Reply; pass it back
    unchanged to continue a conversation.
    """
    cfg = settings()
    client = client or make_client()
    messages: list[dict[str, Any]] = list(history or []) + [{"role": "user", "content": user_prompt}]

    kwargs: dict[str, Any] = dict(
        model=cfg["model"],
        max_tokens=cfg["max_tokens"],
        max_iterations=cfg["max_iterations"],
        system=[{"type": "text", "text": agent.system_prompt, "cache_control": {"type": "ephemeral"}}],
        tools=list(agent.tools),
        messages=messages,
        output_config={"effort": effort or cfg["effort"]},
    )
    if cfg["fallbacks"]:
        kwargs["betas"] = [FALLBACK_BETA]
        kwargs["fallbacks"] = "default"

    runner = client.beta.messages.tool_runner(**kwargs)
    final = None
    tool_calls: list[dict[str, Any]] = []
    usage: dict[str, int] = {}
    for message in runner:
        final = message
        messages.append({"role": "assistant", "content": message.content})
        for block in message.content:
            if getattr(block, "type", None) == "tool_use":
                call = {"name": block.name, "input": block.input}
                tool_calls.append(call)
                if on_tool:
                    on_tool(block.name, block.input)
        tool_response = runner.generate_tool_call_response()
        if tool_response is not None:
            messages.append(tool_response)
        u = getattr(message, "usage", None)
        if u is not None:
            for k in ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"):
                usage[k] = usage.get(k, 0) + int(getattr(u, k, 0) or 0)

    if final is None:
        return Reply(text="", stop_reason=None, messages=messages, tool_calls=tool_calls, usage=usage)

    if final.stop_reason == "refusal":
        details = getattr(final, "stop_details", None)
        why = f" ({details.category})" if details is not None and getattr(details, "category", None) else ""
        text = f"The model declined this request{why}. Rephrase, or handle it manually."
    else:
        text = "".join(b.text for b in final.content if getattr(b, "type", None) == "text").strip()
    return Reply(
        text=text,
        stop_reason=final.stop_reason,
        messages=messages,
        tool_calls=tool_calls,
        usage=usage,
        served_by=getattr(final, "model", None),
    )
