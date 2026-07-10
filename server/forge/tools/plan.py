from __future__ import annotations

from forge.tools.base import Tool, ToolContext, ToolResult

PLAN_TOOL_NAME = "propose_plan"


class ProposePlanTool(Tool):
    """Gating tool: the actor intercepts it (like an approval gate) and awaits
    the user's approve/revise decision. `run` never executes in a session; it
    exists only so the tool registers and specs like any other."""

    name = PLAN_TOOL_NAME
    description = (
        "Present your implementation plan to the user for approval. Call this "
        "exactly once when your plan is ready (markdown). The user either "
        "approves it (the session switches to act mode so you can execute) or "
        "requests changes (revise and propose again)."
    )
    params = {
        "type": "object",
        "properties": {
            "plan": {"type": "string", "description": "The full plan, markdown"},
        },
        "required": ["plan"],
    }
    read_only = True  # gated by its own plan-approval flow, not the tool gate

    def display(self, args: dict) -> str:
        plan = args.get("plan") or ""
        first = next((ln.lstrip("# ").strip() for ln in plan.splitlines() if ln.strip()), "")
        return first or "plan"

    async def run(self, args: dict, ctx: ToolContext) -> ToolResult:
        return ToolResult(output="Plan noted.", is_error=False)
