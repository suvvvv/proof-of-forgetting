"""ArmorIQ enforcement around every tool call: sign the plan once, then allow / block / hold each call."""

import os
import time
from dataclasses import dataclass

from armoriq_sdk import ArmorIQClient, ReportOptions, SessionOptions

MCP_NAME = os.getenv("ARMORIQ_MCP_NAME", "memory-mcp")


@dataclass
class Decision:
    action: str  # allow | block | hold
    reason: str | None = None
    policy: str | None = None
    delegation_id: str | None = None


class ArmorGuard:
    def __init__(self, user_email: str, llm_name: str = "agent", approval_timeout: float | None = None):
        self.user_email = user_email
        self.approval_timeout = approval_timeout or float(os.getenv("APPROVAL_TIMEOUT", "600"))
        client = ArmorIQClient()
        self.session = client.for_user(user_email).start_session(
            SessionOptions(mode="sdk", default_mcp_name=MCP_NAME, llm=llm_name)
        )
        self.token = None

    def sign_plan(self, goal: str, steps: list[dict]) -> dict:
        """Mint a signed intent token for the plan. Anything outside it will be refused."""
        self.token = self.session.start_plan([{"name": s["tool"], "args": s.get("args", {})} for s in steps], goal=goal)
        return {"plan_hash": self.token.plan_hash, "token_id": self.token.token_id, "plan_id": self.token.plan_id,
                "merkle_root": (self.token.raw_token or {}).get("merkle_root"),
                "signed_token": (self.token.raw_token or {}).get("token") or {}}

    def check(self, tool: str, args: dict) -> Decision:
        result = self.session.check(tool, args, user_email=self.user_email)
        return Decision(result.action, result.reason, result.matched_policy, result.delegation_id)

    async def wait_for_approval(self, delegation_id: str) -> str:
        return await self.session.await_approval(
            delegation_id, timeout=self.approval_timeout, interval=3.0, user_email=self.user_email
        )

    def report(self, tool: str, args: dict, result, started: float, ok: bool = True, error: str | None = None):
        self.session.report(
            tool,
            args,
            result,
            ReportOptions(
                status="success" if ok else "failed",
                error_message=error,
                duration_ms=(time.perf_counter() - started) * 1000,
            ),
        )

    def close(self):
        self.session.close()
