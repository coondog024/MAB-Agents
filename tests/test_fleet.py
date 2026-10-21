import unittest
from datetime import date
from types import SimpleNamespace

from mab_agents import config, fleet, llm
from mab_agents.agents.base import Ctx
from mab_agents.store import Store

TODAY = date(2026, 10, 21)


def ctx():
    s = Store(":memory:")
    s.load_sample(today=TODAY)
    return Ctx(shop=config.load_shop(config.EXAMPLE), store=s, today=TODAY, kb_path=":memory:")


class FleetTests(unittest.TestCase):
    def setUp(self):
        self.fleet = fleet.build_fleet(ctx())

    def test_eight_agents_with_tools(self):
        self.assertEqual(len(self.fleet), 10)
        for a in self.fleet.values():
            self.assertTrue(a.tools, a.key)
            self.assertIn("Mullikin", a.system_prompt)
            self.assertIn("You draft; humans send", a.system_prompt)

    def test_manager_has_everyone_else_tools(self):
        from mab_agents.agents.base import tool_name

        names = {tool_name(t) for t in self.fleet["manager"].tools}
        self.assertIn("wip_board", names)
        self.assertIn("audit_repair_order", names)
        self.assertIn("total_loss_check", names)
        self.assertIn("daily_brief", names)
        self.assertIn("lookup_customer", names)
        self.assertIn("search_knowledge", names)
        self.assertIn("web_search", names)

    def test_every_agent_can_search_knowledge(self):
        for a in self.fleet.values():
            self.assertIn("search_knowledge", [getattr(t, "name", t.get("name") if isinstance(t, dict) else "") for t in a.tools], a.key)

    def test_every_example_routes_to_its_agent_or_the_manager(self):
        for a in self.fleet.values():
            for ex in a.examples:
                got = fleet.route(ex, self.fleet)[0]
                self.assertIn(got, {a.key, "manager"}, f"{ex!r} -> {got}, expected {a.key}")

    def test_routing(self):
        cases = {
            "What's late on RO 26-1042 and which vendor do I call about the backorder?": "parts",
            "Draft the delay text for the customer on 26-1042": "customer",
            "Is a $14,200 repair on a $17,500 ACV a total loss with this insurer?": "insurance",
            "What calibrations after a windshield and front radar R&I?": "adas",
            "Audit this estimate for missed blend and not included operations": "estimator",
            "Give me the morning brief": "manager",
            "hello": "manager",
            "A customer is on the phone asking why she pays the deductible to us. What do I say?": "front_desk",
            "Source a front bumper cover 52119-0R918 online and tell me the price and markup": "parts",
            "Who do I need to text today?": "front_desk",
            "Draft the supplement for 26-1042 from the audit findings.": "insurance",
            "What's late on RO 26-1042 and what do I do about it?": "parts",
            "2022 CR-V, replacing the windshield and R&I front bumper cover. What calibrations do I scope?": "adas",
            "Repair is $14,200 on a RAV4 the carrier valued at $17,500. Is it a total?": "insurance",
            "Look up the RO for 410-555-0142 and tell me about the parts": "parts",
        }
        for q, expected in cases.items():
            self.assertEqual(fleet.route(q, self.fleet)[0], expected, q)

    def test_tools_execute_directly(self):
        tools = {t.name: t for t in self.fleet["manager"].tools if hasattr(t, "name")}
        out = tools["stalled_ros"].call({})
        self.assertIn("26-1047", out)
        out = tools["parts_status"].call({"ro_number": "26-1042"})
        self.assertIn("RT headlamp assembly", out)
        out = tools["calibration_checklist"].call({"operations": "windshield replace"})
        self.assertIn("Forward-facing camera", out)
        out = tools["move_stage"].call({"ro_number": "26-1047", "stage": "paint_prep"})
        self.assertIn("customer_update_draft", out)


def _msg(text="", stop="end_turn", tool_use=False):
    content = [SimpleNamespace(type="text", text=text)] if text else []
    if tool_use:
        content.append(SimpleNamespace(type="tool_use", name="wip_board", input={}, id="tu1"))
    return SimpleNamespace(content=content, stop_reason=stop, model="fake-model",
                           usage=SimpleNamespace(input_tokens=10, output_tokens=5, cache_read_input_tokens=0, cache_creation_input_tokens=0))


class FakeRunner:
    """Stands in for client.beta.messages.tool_runner."""

    def __init__(self, msgs):
        self.msgs = msgs
        self.tool_responses = 0

    def __iter__(self):
        yield from self.msgs

    def generate_tool_call_response(self):
        self.tool_responses += 1
        return {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "tu1", "content": "{}"}]}


class FakeClient:
    def __init__(self, msgs=None, closing_text="Wrapped up."):
        self.kwargs = None
        self.create_kwargs = None
        self.runner = None
        self.msgs = msgs or [_msg("Checked in. Teardown next.")]
        self.closing_text = closing_text
        self.beta = SimpleNamespace(messages=SimpleNamespace(tool_runner=self._runner, create=self._create))

    def _runner(self, **kwargs):
        self.kwargs = kwargs
        self.runner = FakeRunner(self.msgs)
        return self.runner

    def _create(self, **kwargs):
        self.create_kwargs = kwargs
        return _msg(self.closing_text)


class LlmTests(unittest.TestCase):
    def test_run_builds_request_and_returns_reply(self):
        agent = fleet.build_fleet(ctx())["production"]
        client = FakeClient()
        reply = llm.run(agent, "status?", client=client)
        self.assertEqual(reply.text, "Checked in. Teardown next.")
        self.assertEqual(reply.stop_reason, "end_turn")
        self.assertEqual(reply.usage["input_tokens"], 10)
        kw = client.kwargs
        self.assertEqual(kw["model"], llm.DEFAULT_MODEL)
        self.assertEqual(kw["system"][0]["cache_control"], {"type": "ephemeral"})
        self.assertIn("effort", kw["output_config"])
        self.assertEqual(kw["fallbacks"], "default")
        self.assertIn(llm.FALLBACK_BETA, kw["betas"])
        self.assertEqual(len(kw["tools"]), 6)
        self.assertEqual(client.runner.tool_responses, 0)       # end_turn: no tools executed
        # transcript is append-only and reusable as history
        self.assertEqual([m["role"] for m in reply.messages], ["user", "assistant"])
        reply2 = llm.run(agent, "and now?", history=reply.messages, client=client)
        self.assertEqual([m["role"] for m in reply2.messages], ["user", "assistant", "user", "assistant"])

    def test_tools_only_run_on_tool_use_turns(self):
        agent = fleet.build_fleet(ctx())["production"]
        cut_off = FakeClient([_msg("partial", stop="max_tokens", tool_use=True)])
        reply = llm.run(agent, "x", client=cut_off)
        self.assertEqual(cut_off.runner.tool_responses, 0)
        self.assertIn("cut off", reply.text)

    def test_iteration_cap_gets_a_closing_answer(self):
        agent = fleet.build_fleet(ctx())["production"]
        capped = FakeClient([_msg("", stop="tool_use", tool_use=True)], closing_text="Here is what I found so far.")
        reply = llm.run(agent, "x", client=capped)
        self.assertEqual(capped.runner.tool_responses, 1)
        self.assertTrue(reply.hit_iteration_cap)
        self.assertEqual(reply.text, "Here is what I found so far.")
        self.assertEqual(capped.create_kwargs["tool_choice"], {"type": "none"})
        self.assertEqual(reply.messages[-1]["role"], "assistant")

    def test_tool_errors_are_readable_not_fatal(self):
        from anthropic.lib.tools import ToolError

        tools = {t.name: t for t in fleet.build_fleet(ctx())["manager"].tools if hasattr(t, "name")}
        with self.assertRaises(ToolError) as cm:
            tools["parts_status"].call({"ro_number": "99-9999"})
        self.assertIn("99-9999", str(cm.exception))
        with self.assertRaises(ToolError):
            tools["move_stage"].call({"ro_number": "26-1047", "stage": "nonsense"})


if __name__ == "__main__":
    unittest.main()
