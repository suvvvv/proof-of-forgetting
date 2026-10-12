"""Swappable LLM backends for the agent loop. Gemini (free tier) by default; Claude if configured.

Both expose the same small interface:
    plan(request)                -> [{"tool": ..., "args": {...}}, ...]
    start(request)               -> begin a conversation
    turn()                       -> ("calls", [(call_id, name, args), ...]) or ("text", final_answer)
    add_results([(call_id, name, output_dict), ...])
"""

import json
import os

PLAN_TOOL = {
    "name": "submit_plan",
    "description": "Submit the ordered list of tool calls you intend to make.",
    "input_schema": {
        "type": "object",
        "properties": {
            "steps": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "tool": {"type": "string"},
                        "args": {"type": "object", "properties": {"user_email": {"type": "string"}}},
                    },
                    "required": ["tool", "args"],
                },
            }
        },
        "required": ["steps"],
    },
}


class GeminiLLM:
    def __init__(self, system: str, tools: list[dict]):
        from google import genai
        from google.genai import types

        self.types = types
        self.client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
        self.model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
        self.name = self.model
        self.system = system
        self.tools = tools
        self.history = []

    def _decls(self, tools):
        return [
            self.types.Tool(
                function_declarations=[
                    self.types.FunctionDeclaration(
                        name=t["name"], description=t["description"], parameters_json_schema=t["input_schema"]
                    )
                    for t in tools
                ]
            )
        ]

    def plan(self, request: str, planner_prompt: str) -> list[dict]:
        t = self.types
        response = self.client.models.generate_content(
            model=self.model,
            contents=[t.Content(role="user", parts=[t.Part(text=request)])],
            config=t.GenerateContentConfig(
                system_instruction=self.system + "\n\n" + planner_prompt,
                tools=self._decls(self.tools + [PLAN_TOOL]),
                tool_config=t.ToolConfig(
                    function_calling_config=t.FunctionCallingConfig(mode="ANY", allowed_function_names=["submit_plan"])
                ),
            ),
        )
        call = next(p.function_call for p in response.candidates[0].content.parts if p.function_call)
        return list(call.args["steps"])

    def start(self, request: str):
        self.history = [self.types.Content(role="user", parts=[self.types.Part(text=request)])]

    def turn(self):
        t = self.types
        response = self.client.models.generate_content(
            model=self.model,
            contents=self.history,
            config=t.GenerateContentConfig(system_instruction=self.system, tools=self._decls(self.tools)),
        )
        content = response.candidates[0].content
        # Keep the model's content verbatim: it carries thought signatures Gemini needs back.
        self.history.append(content)
        calls = [p.function_call for p in (content.parts or []) if p.function_call]
        if not calls:
            return "text", "".join(p.text or "" for p in (content.parts or []) if not p.thought)
        return "calls", [(c.id or c.name, c.name, dict(c.args or {})) for c in calls]

    def add_results(self, results):
        t = self.types
        self.history.append(
            t.Content(
                role="user",
                parts=[
                    t.Part(function_response=t.FunctionResponse(id=cid if cid != name else None, name=name, response=out))
                    for cid, name, out in results
                ],
            )
        )


class ClaudeLLM:
    def __init__(self, system: str, tools: list[dict]):
        import anthropic

        self.client = anthropic.Anthropic()
        self.model = os.getenv("AGENT_MODEL", "claude-sonnet-5-5")
        self.name = self.model
        self.system = system
        self.tools = tools
        self.messages = []

    def plan(self, request: str, planner_prompt: str) -> list[dict]:
        response = self.client.messages.create(
            model=self.model,
            max_tokens=1024,
            system=self.system + "\n\n" + planner_prompt,
            tools=self.tools + [PLAN_TOOL],
            tool_choice={"type": "tool", "name": "submit_plan"},
            messages=[{"role": "user", "content": request}],
        )
        return next(b for b in response.content if b.type == "tool_use").input["steps"]

    def start(self, request: str):
        self.messages = [{"role": "user", "content": request}]

    def turn(self):
        response = self.client.messages.create(
            model=self.model, max_tokens=1500, system=self.system, tools=self.tools, messages=self.messages
        )
        self.messages.append({"role": "assistant", "content": response.content})
        if response.stop_reason != "tool_use":
            return "text", "".join(b.text for b in response.content if b.type == "text")
        return "calls", [(b.id, b.name, b.input) for b in response.content if b.type == "tool_use"]

    def add_results(self, results):
        self.messages.append(
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": cid, "content": json.dumps(out)} for cid, _, out in results
                ],
            }
        )


def make_llm(system: str, tools: list[dict]):
    provider = os.getenv("LLM_PROVIDER") or ("gemini" if os.getenv("GEMINI_API_KEY") else "claude")
    if provider == "gemini":
        return GeminiLLM(system, tools)
    if provider == "claude":
        return ClaudeLLM(system, tools)
    raise ValueError(f"Unknown LLM_PROVIDER: {provider}")
