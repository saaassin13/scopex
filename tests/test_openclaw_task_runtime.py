from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import tempfile
import textwrap
import threading
import unittest

from scopex.agent.runtime import OpenClawTaskRuntime, OpenClawTaskSpec
from scopex.events.progress import EventType, InMemoryEventSink
from scopex.runtime.stop import SafeStopGate


class ModelHandler(BaseHTTPRequestHandler):
    bodies = []

    def log_message(self, *_):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        ModelHandler.bodies.append(body)
        request = json.loads(body)
        response = json.dumps({
            "id": "fake",
            "choices": [{"message": {"content": "model says: " + request["messages"][-1]["content"]}}],
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(response)))
        self.end_headers()
        self.wfile.write(response)


FAKE_CLI = r'''
import json
import os
from pathlib import Path
import sys
import urllib.request

cfg = json.loads(Path(os.environ["OPENCLAW_CONFIG_PATH"]).read_text())
provider = cfg["models"]["providers"]["vllm"]
def arg(name):
    i = sys.argv.index(name)
    return sys.argv[i + 1]
message = Path(arg("--message-file")).read_text()
session_key = arg("--session-key")
with Path("session-keys.txt").open("a") as f:
    f.write(session_key + "\n")
model_ref = "vllm/" + provider["models"][0]["id"]
extra = cfg["agents"]["defaults"]["models"][model_ref]["params"]["extra_body"]
tools = cfg["tools"]["allow"]
payload = {
    "model": provider["models"][0]["id"],
    "messages": [{"role": "user", "content": message}],
    "max_tokens": extra["max_tokens"],
    "chat_template_kwargs": extra["chat_template_kwargs"],
    "tools": [
        {"type": "function", "function": {"name": name, "parameters": {}}}
        for name in tools
    ],
    "tool_choice": "auto",
    "stream": False,
}
data = json.dumps(payload).encode()
request = urllib.request.Request(
    provider["baseUrl"] + "/chat/completions",
    data=data,
    headers={
        "Content-Type": "application/json",
        "Authorization": "Bearer " + provider["apiKey"],
    },
)
with urllib.request.urlopen(request, timeout=5) as response:
    upstream = json.loads(response.read())
answer = upstream["choices"][0]["message"]["content"]
print(json.dumps({
    "meta": {"executionTrace": {"fallbackUsed": False}},
    "payloads": [{"text": answer, "isReasoning": False}],
}))
'''


class OpenClawTaskRuntimeTests(unittest.TestCase):
    def setUp(self):
        ModelHandler.bodies = []
        self.model = ThreadingHTTPServer(("127.0.0.1", 0), ModelHandler)
        self.model_thread = threading.Thread(target=self.model.serve_forever, daemon=True)
        self.model_thread.start()
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.cli = self.root / "fake-openclaw"
        self.cli.write_text("#!/usr/bin/env python3\n" + textwrap.dedent(FAKE_CLI), encoding="utf-8")
        self.cli.chmod(0o755)
        (self.root / "workspace").mkdir()

    def tearDown(self):
        self.model.shutdown()
        self.model.server_close()
        self.model_thread.join(timeout=2)
        self.tmp.cleanup()

    def make_runtime(self, stop=None, safe=None):
        events = InMemoryEventSink()
        runtime = OpenClawTaskRuntime(
            task_id="task-1",
            session_key="agent:sx:task-1",
            spec=OpenClawTaskSpec(
                cli_path=self.cli,
                model_id="qwen-local",
                upstream_base_url=f"http://127.0.0.1:{self.model.server_port}/v1",
                upstream_api_key="",
                workspace=self.root / "workspace",
                runtime_root=self.root / "runtime",
                audit_root=self.root / "audit",
                image="fake-image",
                docker_host="unix:///var/run/docker.sock",
                agent_id="sx1",
                uid=os.getuid(),
                gid=os.getgid(),
                timeout_s=10,
                max_requests=4,
                max_tokens=256,
            ),
            events=events,
            stop_gate=stop or SafeStopGate(),
            on_safe_stop=safe,
        )
        return runtime, events

    def test_two_turns_reuse_session_and_state(self):
        runtime, events = self.make_runtime()
        first = runtime.run_turn("first", turn_name="turn-1")
        second = runtime.run_turn("second", turn_name="turn-2")
        self.assertTrue(first.cli_outcome.completed)
        self.assertTrue(second.cli_outcome.completed)
        self.assertEqual(first.cli_outcome.answer, "model says: first")
        self.assertEqual(second.cli_outcome.answer, "model says: second")
        keys = (self.root / "runtime" / "session-keys.txt").read_text().splitlines()
        self.assertEqual(keys, ["agent:sx:task-1", "agent:sx:task-1"])
        self.assertEqual(len(ModelHandler.bodies), 2)
        self.assertEqual(
            [event.type for event in events.events].count(EventType.MODEL_REQUEST),
            2,
        )
        self.assertTrue((self.root / "audit" / "turn-1" / "wire-01-meta.json").is_file())
        self.assertTrue((self.root / "audit" / "turn-2" / "wire-01-meta.json").is_file())

    def test_requested_stop_blocks_before_model_forward(self):
        stop = SafeStopGate()
        stop.request("user stop")
        boundaries = []
        runtime, _events = self.make_runtime(stop, boundaries.append)
        result = runtime.run_turn("will stop", turn_name="turn-stop")
        self.assertNotEqual(result.process.returncode, 0)
        self.assertIsNone(result.cli_outcome)
        self.assertEqual(ModelHandler.bodies, [])
        self.assertEqual(len(boundaries), 1)
        self.assertEqual(boundaries[0].request_index, 1)
        self.assertFalse(result.proxy_records[0]["forwarded"])


if __name__ == "__main__":
    unittest.main()
