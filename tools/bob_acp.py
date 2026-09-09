"""Drive IBM Bob Shell as an Agent Client Protocol server over stdio.

Bob Shell's headless `bob run` requires BOB_API_KEY. This client instead speaks
ACP to `bob acp`, which authenticates from the cached browser SSO session, so no
credential is ever read, passed or stored by this process.

Every session writes a full JSON-RPC transcript to bob/transcripts/, which is the
evidence that Bob authored the modules it authored. Bob's own SQLite ledger at
~/.bob/db/bob.db independently records line-level attribution for the same edits.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys
import threading
import time

PROTOCOL_VERSION = 1


class BobSession:
    def __init__(self, cwd: str, mode: str = "agent", log_level: str = "error") -> None:
        self.cwd = str(pathlib.Path(cwd).resolve())
        self.mode = mode
        self.proc = subprocess.Popen(
            ["bob", "acp", "--trust", "--auto-approve", "--log-level", log_level],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            cwd=self.cwd,
        )
        self.events: list[dict] = []
        self.results: dict[int, dict] = {}
        self.stderr_lines: list[str] = []
        self._next_id = 0
        self._lock = threading.Lock()
        self.session_id: str | None = None
        self.agent_info: dict = {}
        self.tool_calls: list[str] = []
        threading.Thread(target=self._read_stdout, daemon=True).start()
        threading.Thread(target=self._read_stderr, daemon=True).start()

    def _read_stdout(self) -> None:
        for line in self.proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            with self._lock:
                self.events.append(msg)
                if "id" in msg and ("result" in msg or "error" in msg):
                    self.results[msg["id"]] = msg
            self._on_update(msg)

    def _read_stderr(self) -> None:
        for line in self.proc.stderr:
            self.stderr_lines.append(line.rstrip())

    def _on_update(self, msg: dict) -> None:
        if msg.get("method") != "session/update":
            return
        update = msg.get("params", {}).get("update", {})
        kind = update.get("sessionUpdate")
        if kind == "agent_message_chunk":
            sys.stdout.write(update.get("content", {}).get("text", ""))
            sys.stdout.flush()
        elif kind == "tool_call":
            title = update.get("title", "")
            self.tool_calls.append(title)
            print(f"\n[bob tool] {title}", flush=True)
        elif kind == "plan":
            print(f"\n[bob plan] {len(update.get('entries', []))} steps", flush=True)

    def _call(self, method: str, params: dict, timeout: float) -> dict:
        with self._lock:
            self._next_id += 1
            call_id = self._next_id
        self.proc.stdin.write(
            json.dumps({"jsonrpc": "2.0", "id": call_id, "method": method, "params": params}) + "\n"
        )
        self.proc.stdin.flush()
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self._lock:
                if call_id in self.results:
                    reply = self.results[call_id]
                    break
            if self.proc.poll() is not None:
                raise RuntimeError(
                    f"bob acp exited with code {self.proc.returncode}: "
                    + "\n".join(self.stderr_lines[-10:])
                )
            time.sleep(0.15)
        else:
            raise TimeoutError(f"{method} did not return within {timeout}s")
        if "error" in reply:
            raise RuntimeError(f"{method} failed: {reply['error']}")
        return reply["result"]

    def start(self) -> None:
        init = self._call(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "clientCapabilities": {"fs": {"readTextFile": True, "writeTextFile": True}},
            },
            timeout=90,
        )
        self.agent_info = init["agentInfo"]
        if not init.get("authMethods"):
            raise RuntimeError("bob acp reported no auth methods")
        new = self._call("session/new", {"cwd": self.cwd, "mcpServers": []}, timeout=120)
        self.session_id = new["sessionId"]
        if self.mode != "agent":
            self._call(
                "session/set_mode",
                {"sessionId": self.session_id, "modeId": self.mode},
                timeout=60,
            )

    def prompt(self, text: str, timeout: float = 1800) -> str:
        result = self._call(
            "session/prompt",
            {"sessionId": self.session_id, "prompt": [{"type": "text", "text": text}]},
            timeout=timeout,
        )
        return result.get("stopReason", "")

    def save(self, path: str) -> None:
        out = pathlib.Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps(
                {
                    "agentInfo": self.agent_info,
                    "sessionId": self.session_id,
                    "cwd": self.cwd,
                    "mode": self.mode,
                    "toolCalls": self.tool_calls,
                    "events": self.events,
                },
                indent=1,
            )
        )

    def close(self) -> None:
        self.proc.kill()


def main() -> int:
    ap = argparse.ArgumentParser(description="Run one IBM Bob task over ACP.")
    ap.add_argument("--cwd", required=True)
    ap.add_argument("--mode", default="agent", choices=["agent", "plan", "ask"])
    ap.add_argument("--prompt-file", required=True)
    ap.add_argument("--transcript", required=True)
    ap.add_argument("--timeout", type=float, default=1800)
    args = ap.parse_args()

    prompt = pathlib.Path(args.prompt_file).read_text()
    session = BobSession(args.cwd, mode=args.mode)
    try:
        session.start()
        print(
            f"[bob] {session.agent_info.get('name')} {session.agent_info.get('version')} "
            f"session={session.session_id}",
            flush=True,
        )
        stop_reason = session.prompt(prompt, timeout=args.timeout)
        print(f"\n[bob stopReason] {stop_reason}", flush=True)
    finally:
        session.save(args.transcript)
        print(f"[bob transcript] {args.transcript} events={len(session.events)}", flush=True)
        session.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
