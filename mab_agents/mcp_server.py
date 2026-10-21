"""`mab mcp`: expose the fleet's tools to Claude Desktop (or any MCP client) over stdio.

Install the extra first: pip install "mab-agents[mcp]". Then add to Claude
Desktop's config (Settings > Developer > Edit Config):

    {"mcpServers": {"mullikins": {"command": "mab", "args": ["mcp"], "cwd": "/path/to/MAB-Agents"}}}

Staff who already use Claude Desktop then get every shop tool (look up a
customer, draft the text, audit an estimate, price a part, search the SOPs) in
their normal Claude window, with Claude Desktop's own model and login.
"""
from __future__ import annotations

import functools

from . import fleet as fleet_mod
from .agents.base import Ctx, tool_name


def build_server(ctx: Ctx):
    try:
        from mcp.server.mcpserver import MCPServer
        from mcp.server.mcpserver.exceptions import ToolError as McpToolError
    except ImportError as exc:  # pragma: no cover - depends on optional extra
        raise SystemExit('The MCP extra is not installed. Run: pip install "mab-agents[mcp]"') from exc
    from anthropic.lib.tools import ToolError as SdkToolError

    def readable(func):
        """Expected failures (unknown RO, bad stage, bad date) reach Claude Desktop as text, not 'Error executing tool'."""

        @functools.wraps(func)
        def inner(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except (SdkToolError, KeyError, ValueError) as exc:
                raise McpToolError(str(exc.args[0]) if exc.args else str(exc)) from exc

        return inner

    shop = ctx.shop["shop"]["name"]
    server = MCPServer(
        "mullikins-auto-body",
        instructions=(
            f"Shop tools for {shop}, a collision repair shop. Tools draft texts, emails and orders; a human sends them. "
            "Numbers, dates and shop policy come from the tools and the shop's documents (search_knowledge); do not invent them."
        ),
    )
    seen: set[str] = set()
    for agent in fleet_mod.build_fleet(ctx).values():
        for t in agent.tools:
            name = tool_name(t)
            if isinstance(t, dict) or name in seen or not hasattr(t, "func"):
                continue  # server-side tools (web search) belong to the Claude API, not MCP
            seen.add(name)
            server.tool(name=name, description=t.description)(readable(t.func))
    return server


def run(ctx: Ctx) -> None:
    build_server(ctx).run(transport="stdio")
