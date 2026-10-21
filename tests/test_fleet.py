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
    return Ctx(shop=config.load_shop(config.EXAMPLE), store=s, today=TODAY)


class FleetTests(unittest.TestCase):
    def setUp(self):
        self.fleet = fleet.build_fleet(ctx())

    def test_eight_agents_with_tools(self):
        self.assertEqual(len(self.fleet), 8)
        for a in self.fleet.values():
            self.assertTrue(a.tools, a.key)
            self.assertIn("Mullikin", a.system_prompt)
            self.assertIn("You draft; humans send", a.system_prompt)

    def test_manager_has_everyone_else_tools(self):
        names = {t.name for t in self.fleet["manager"].tools}
        self.assertIn("wip_board", names)
        self.assertIn("audit_repair_order", names)
        self.assertIn("total_loss_check", names)
        self.assertIn("daily_brief", names)

    def test_routing(self):
        cases = {
            "What's late on RO 26-1042 and which vendor do I call about the backorder?": "parts",
            "Draft the delay text for the customer on 26-1042": "customer",
            "Is a $14,200 repair on a $17,500 ACV a total loss with this insurer?": "insurance",
            "What calibrations after a windshield and front radar R&I?": "adas",
            "Audit this estimate for missed blend and not included operations": "estimator",
            "Give me the morning brief": "manager",
            "hello": "manager",
        }
        for q, expected in cases.items():
            self.assertEqual(fleet.route(q, self.fleet)[0], expected, q)

    def test_tools_execute_directly(self):
        tools = {t.name: t for t in self.fleet["manager"].tools}
        out = tools["stalled_ros"].call({})
        self.assertIn("26-1047", out)
        out = tools["parts_status"].call({"ro_number": "26-1042"})
        self.assertIn("RT headlamp assembly", out)
        out = tools["calibration_checklist"].call({"operations": "windshield replace"})
        self.assertIn("Forward-facing camera", out)
        out = tools["move_stage"].call({"ro_number": "26-1047", "stage": "paint_prep"})
        self.assertIn("customer_update_draft", out)


class FakeRunner:
    """Stands in for client.beta.messages.tool_runner: one assistant message, no tool calls."""

    def __init__(self, text):
        self.msg = SimpleNamespace(
            content=[SimpleNamespace(type="text", text=text)],
            stop_reason="end_turn",
            usage=SimpleNamespace(input_tokens=10, output_tokens=5, cache_read_input_tokens=0, cache_creation_input_tokens=0),
            model="fake-model",
        )

    def __iter__(self):
        yield self.msg

    def generate_tool_call_response(self):
        return None


class FakeClient:
    def __init__(self):
        self.kwargs = None
        self.beta = SimpleNamespace(messages=SimpleNamespace(tool_runner=self._runner))

    def _runner(self, **kwargs):
        self.kwargs = kwargs
        return FakeRunner("Checked in. Teardown next.")


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
        self.assertEqual(len(kw["tools"]), 5)
        # transcript is append-only and reusable as history
        self.assertEqual([m["role"] for m in reply.messages], ["user", "assistant"])
        reply2 = llm.run(agent, "and now?", history=reply.messages, client=client)
        self.assertEqual([m["role"] for m in reply2.messages], ["user", "assistant", "user", "assistant"])


if __name__ == "__main__":
    unittest.main()
