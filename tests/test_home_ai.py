"""Every AI's events become the stream-json events the home screen reads."""

import unittest

import home_ai


def run(kind, events):
    t = home_ai.Translate(kind)
    return [out for ev in events for out in t(ev)]


def steps(out):
    return [b for ev in out if ev.get("type") == "assistant" for b in ev["message"]["content"]]


class Codex(unittest.TestCase):
    def test_a_job_with_a_command(self):
        out = run(
            "codex",
            [
                {"type": "thread.started", "thread_id": "t1"},
                {"type": "turn.started"},
                {"type": "item.completed", "item": {"id": "0", "type": "agent_message", "text": "Looking."}},
                {"type": "item.started", "item": {"id": "1", "type": "command_execution", "command": "ls ~"}},
                {
                    "type": "item.completed",
                    "item": {"id": "2", "type": "file_change", "changes": [{"path": "/a/b.txt", "kind": "add"}]},
                },
                {"type": "item.completed", "item": {"id": "3", "type": "agent_message", "text": "Done: three files."}},
                {"type": "turn.completed", "usage": {"input_tokens": 10, "cached_input_tokens": 4, "output_tokens": 5}},
            ],
        )
        self.assertEqual(out[0], {"type": "system", "subtype": "init", "session_id": "t1", "model": None})
        self.assertEqual(
            [(b.get("name") or "text", b.get("text") or b.get("input")) for b in steps(out)],
            [("text", "Looking."), ("Bash", {"command": "ls ~"}), ("Write", {"file_path": "/a/b.txt"})],
        )
        self.assertEqual(out[-1]["result"], "Done: three files.")
        self.assertEqual(out[-1]["session_id"], "t1")
        self.assertFalse(out[-1]["is_error"])
        self.assertEqual(out[-1]["usage"]["cache_read_input_tokens"], 4)

    def test_a_failed_turn(self):
        out = run(
            "codex",
            [{"type": "error", "message": "401 Unauthorized"}, {"type": "turn.failed", "error": {"message": ""}}],
        )
        self.assertTrue(out[-1]["is_error"])
        self.assertEqual(out[-1]["errors"], ["401 Unauthorized"])


class Gemini(unittest.TestCase):
    def test_deltas_and_a_tool(self):
        out = run(
            "gemini",
            [
                {"type": "init", "session_id": "g1", "model": "gemini-3-pro"},
                {"type": "message", "role": "user", "content": "hi"},
                {"type": "message", "role": "assistant", "content": "Let me ", "delta": True},
                {"type": "message", "role": "assistant", "content": "look.", "delta": True},
                {
                    "type": "tool_use",
                    "tool_name": "run_shell_command",
                    "tool_id": "x",
                    "parameters": {"command": "df -h"},
                },
                {"type": "tool_result", "tool_id": "x", "status": "success", "output": "..."},
                {"type": "message", "role": "assistant", "content": "Plenty of space.", "delta": True},
                {"type": "result", "status": "success", "stats": {"input_tokens": 7, "output_tokens": 3, "cached": 0}},
            ],
        )
        self.assertEqual(out[0]["model"], "gemini-3-pro")
        self.assertEqual([b.get("name") or b.get("text") for b in steps(out)], ["Let me look.", "Bash"])
        self.assertEqual(out[-1]["result"], "Plenty of space.")
        self.assertEqual(out[-1]["session_id"], "g1")


class Grok(unittest.TestCase):
    def test_claudes_shape_with_its_own_tool_names(self):
        ev = {
            "type": "assistant",
            "message": {"content": [{"type": "tool_use", "name": "run_terminal_cmd", "input": {"command": "ls"}}]},
        }
        self.assertEqual(steps(run("grok", [ev]))[0]["name"], "Bash")
        done = {"type": "result", "result": "ok", "session_id": "k1"}
        self.assertEqual(run("grok", [done]), [done])


class Copilot(unittest.TestCase):
    def test_a_job_ends_without_a_result_event(self):
        t = home_ai.Translate("copilot")
        events = [
            {"type": "session.start", "data": {"sessionId": "c1"}},
            {"type": "assistant.message", "data": {"content": "Checking.", "toolRequests": [{"name": "bash"}]}},
            {"type": "tool.execution_start", "data": {"toolName": "bash", "arguments": {"command": "uptime"}}},
            {"type": "tool.execution_start", "agentId": "helper", "data": {"toolName": "view", "arguments": {}}},
            {"type": "assistant.usage", "data": {"inputTokens": 9, "outputTokens": 2, "cacheReadTokens": 1}},
            {"type": "assistant.message", "data": {"content": "Up for 3 days."}},
            {"type": "session.idle", "ephemeral": True, "data": {}},
        ]
        out = [o for ev in events for o in t(ev)] + list(t.finish())
        self.assertEqual(out[0]["session_id"], "c1")
        self.assertEqual([b.get("name") or b.get("text") for b in steps(out)], ["Checking.", "Bash"])
        self.assertEqual(out[-1]["result"], "Up for 3 days.")
        self.assertEqual(out[-1]["usage"]["input_tokens"], 9)
        self.assertEqual(list(t.finish()), [])  # given once

    def test_its_own_error_line(self):
        t = home_ai.Translate("copilot")
        list(t({"type": "session.start", "data": {"sessionId": "c2"}}))
        t.plain("Error: Access denied by policy settings")
        end = list(t.finish())[-1]
        self.assertTrue(end["is_error"])
        self.assertEqual(end["errors"], ["Access denied by policy settings"])


class CommandLines(unittest.TestCase):
    def test_new_and_resumed(self):
        a = home_ai.argv("codex", "codex", "Q", "SYS", None, False, [])
        self.assertIn("--dangerously-bypass-approvals-and-sandbox", a)
        self.assertTrue(a[-1].startswith("SYS") and a[-1].endswith("Q"))
        a = home_ai.argv("codex", "codex", "Q", "SYS", "t1", True, [])
        self.assertEqual(a[-3:], ["resume", "t1", "Q"])
        self.assertIn("workspace-write", a)
        g = home_ai.argv("gemini", "gemini", "Q", "SYS", "g1", True, [])
        self.assertEqual(g[g.index("--approval-mode") + 1], "auto_edit")
        self.assertEqual(g[-2:], ["--resume", "g1"])
        k = home_ai.argv("grok", "grok", "Q", "SYS", None, False, [])
        self.assertEqual(k[k.index("--rules") + 1], "SYS")
        self.assertIn("bypassPermissions", k)
        self.assertIn("run_terminal_cmd", home_ai.argv("grok", "grok", "Q", "SYS", None, True, []))
        c = home_ai.argv("copilot", "copilot", "Q", "SYS", None, True, [])
        self.assertEqual(c[c.index("--deny-tool") + 1], "shell")
        self.assertEqual(len(c[c.index("--session-id") + 1]), 36)  # a new conversation's own id
        c = home_ai.argv("copilot", "copilot", "Q", "SYS", "c1", False, [])
        self.assertNotIn("--deny-tool", c)
        self.assertEqual(c[-2:], ["--session-id", "c1"])
        self.assertEqual(c[2], "Q")


if __name__ == "__main__":
    unittest.main()
