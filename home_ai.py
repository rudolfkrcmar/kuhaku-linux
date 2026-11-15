"""The home screen's AI: Claude Code, or OpenAI's Codex, Google's Gemini CLI, xAI's Grok, or a model
on this computer (Ollama, through Codex). Each runs in the background the way `claude -p` does.

What each one says is turned into the stream-json events the home screen already reads (Claude
Code's shape: an init, assistant messages with text and tool_use blocks, one result), so the rest of
Kūhaku does not need to know which AI is working. Grok Build already speaks that shape; Codex
(`exec --json`) and Gemini CLI (`--output-format stream-json`) are translated here.
"""

from collections.abc import Iterator
from typing import Any

# Other tools' names for the same work, so the steps read the same whoever does them.
TOOL_NAMES = {
    "run_terminal_cmd": "Bash",
    "run_shell_command": "Bash",
    "shell": "Bash",
    "read_file": "Read",
    "read_many_files": "Read",
    "write_file": "Write",
    "search_replace": "Edit",
    "replace": "Edit",
    "edit_file": "Edit",
    "grep": "Grep",
    "search_file_content": "Grep",
    "glob": "Glob",
    "list_dir": "Glob",
    "list_directory": "Glob",
    "web_search": "WebSearch",
    "google_web_search": "WebSearch",
    "web_fetch": "WebFetch",
    "todo_write": "TodoWrite",
    "write_todos": "TodoWrite",
}


# Kūhaku works in your home folder, which these tools would otherwise not trust to act in
# (Gemini quietly falls back to asking, which a job in the background cannot answer).
ENV = {"gemini": {"GEMINI_CLI_TRUST_WORKSPACE": "true"}}


def first_prompt(system: str, question: str) -> str:
    """Codex and Gemini have no way to add to their own instructions from the command line:
    Kūhaku's go in front of the first question of a conversation."""
    return f"{system}\n\n---\n\n{question}"


def argv(
    k: str, exe: str, question: str, system: str, session_id: str | None, careful: bool, local: list[str]
) -> list[str]:
    """The command line for a job by a non-Claude AI (Claude's is built in kuhaku_claude.argv_for).
    careful: Kūhaku cannot show a permission prompt, so careful means no commands, or as near as
    the tool allows."""
    if k == "grok":
        a = [
            exe,
            "-p",
            question,
            "--output-format",
            "streaming-messages-json",
            "--rules",
            system,
            "--no-auto-update",
            "--trust",
        ]
        a += ["--disallowed-tools", "run_terminal_cmd"] if careful else ["--permission-mode", "bypassPermissions"]
        return a + (["--resume", session_id] if session_id else [])
    text = question if session_id else first_prompt(system, question)
    if k == "gemini":
        a = [exe, "-p", text, "--output-format", "stream-json", "--approval-mode", "auto_edit" if careful else "yolo"]
        return a + (["--resume", session_id] if session_id else [])
    # codex, and local (Codex driving Ollama): careful keeps its commands in a sandbox that may only write here
    a = [exe, "exec", "--json", "--skip-git-repo-check", *local]
    a += ["--sandbox", "workspace-write"] if careful else ["--dangerously-bypass-approvals-and-sandbox"]
    return a + (["resume", session_id] if session_id else []) + [text]


def tool_use(name: str, inp: dict) -> dict:
    return {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": name, "input": inp}]}}


def thought(text: str) -> dict:
    return {"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}}


class Translate:
    """One job's events, in Claude Code's shape. Call it with each parsed line; it yields events."""

    def __init__(self, k: str):
        self.k = k
        self.session_id: str | None = None
        self.reply = ""  # what the AI said since its last step: the answer, once it stops
        self.error = ""

    def __call__(self, ev: dict) -> Iterator[dict]:
        if self.k == "claude":
            yield ev
        elif self.k == "grok":
            yield from self.grok(ev)
        elif self.k == "gemini":
            yield from self.gemini(ev)
        else:
            yield from self.codex(ev)

    def said_before_step(self) -> Iterator[dict]:
        if self.reply.strip():
            yield thought(self.reply)
        self.reply = ""

    # --- Grok Build: already Claude's shape; only its tool names differ ---------------------
    def grok(self, ev: dict) -> Iterator[dict]:
        for block in (ev.get("message") or {}).get("content") or []:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                block["name"] = TOOL_NAMES.get(block.get("name", ""), block.get("name", ""))
        yield ev

    # --- Gemini CLI: init, message (deltas), tool_use, tool_result, error, result -----------
    def gemini(self, ev: dict) -> Iterator[dict]:
        t = ev.get("type")
        if t == "init":
            self.session_id = ev.get("session_id")
            yield {"type": "system", "subtype": "init", "session_id": self.session_id, "model": ev.get("model")}
        elif t == "message" and ev.get("role") == "assistant":
            self.reply += ev.get("content") or ""
        elif t == "tool_use":
            yield from self.said_before_step()
            name = ev.get("tool_name") or ""
            yield tool_use(TOOL_NAMES.get(name, name), ev.get("parameters") or {})
        elif t == "error" and ev.get("severity") == "error":
            self.error = ev.get("message") or self.error
        elif t == "result":
            yield self.result(
                ev.get("status") == "error", (ev.get("error") or {}).get("message"), ev.get("stats") or {}
            )

    # --- Codex: thread.started, item.started/completed, turn.completed/failed, error ----------
    def codex(self, ev: dict) -> Iterator[dict]:
        t, item = ev.get("type"), ev.get("item") or {}
        kind = item.get("type")
        if t == "thread.started":
            self.session_id = ev.get("thread_id")
            yield {"type": "system", "subtype": "init", "session_id": self.session_id, "model": None}
        elif t == "item.completed" and kind == "agent_message":
            yield from self.said_before_step()  # an earlier message was a thought on the way
            self.reply = item.get("text") or ""
        elif t == "item.started" and kind == "command_execution":
            yield from self.said_before_step()
            yield tool_use("Bash", {"command": item.get("command") or ""})
        elif t == "item.started" and kind == "web_search":
            yield from self.said_before_step()
            yield tool_use("WebSearch", {"query": item.get("query") or ""})
        elif t == "item.started" and kind == "mcp_tool_call":
            yield from self.said_before_step()
            yield tool_use(f"mcp__{item.get('server')}__{item.get('tool')}", item.get("arguments") or {})
        elif t == "item.completed" and kind == "file_change":
            yield from self.said_before_step()
            for change in item.get("changes") or []:
                yield tool_use(
                    "Write" if change.get("kind") == "add" else "Edit", {"file_path": change.get("path") or ""}
                )
        elif t == "error":
            self.error = ev.get("message") or self.error
        elif t == "turn.failed":
            yield self.result(True, (ev.get("error") or {}).get("message"), {})
        elif t == "turn.completed":
            u = ev.get("usage") or {}
            yield self.result(
                False,
                None,
                {
                    "input_tokens": u.get("input_tokens", 0),
                    "output_tokens": u.get("output_tokens", 0),
                    "cached": u.get("cached_input_tokens", 0),
                },
            )

    def result(self, failed: bool, why: str | None, stats: dict) -> dict:
        why = why or self.error
        answer = self.reply.strip()
        out: dict[str, Any] = {
            "type": "result",
            "session_id": self.session_id,
            "is_error": failed and not answer,
            "result": answer or None,
            "usage": {
                "input_tokens": stats.get("input_tokens", 0),
                "output_tokens": stats.get("output_tokens", 0),
                "cache_read_input_tokens": stats.get("cached", 0),
            },
        }
        if failed and not answer:
            out["errors"] = [why or "it stopped"]
        return out
