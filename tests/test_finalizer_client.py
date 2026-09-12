from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import unittest

from scopex.finalizer.client import StreamingFinalizerClient


class Handler(BaseHTTPRequestHandler):
    request_json = None

    def log_message(self, *_):
        pass

    def do_POST(self):
        if self.path != "/v1/chat/completions":
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length", "0"))
        Handler.request_json = json.loads(self.rfile.read(length))
        rows = [
            {
                "id": "x",
                "object": "chat.completion.chunk",
                "created": 0,
                "model": Handler.request_json["model"],
                "choices": [{"index": 0, "delta": {"content": "{\"claims\":"}, "finish_reason": None}],
            },
            {
                "id": "x",
                "object": "chat.completion.chunk",
                "created": 0,
                "model": Handler.request_json["model"],
                "choices": [{"index": 0, "delta": {"content": "[]}"}, "finish_reason": None}],
            },
            {
                "id": "x",
                "object": "chat.completion.chunk",
                "created": 0,
                "model": Handler.request_json["model"],
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 3, "total_tokens": 13},
            },
        ]
        body = "".join("data: " + json.dumps(row) + "\n\n" for row in rows) + "data: [DONE]\n\n"
        data = body.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class FinalizerClientTests(unittest.TestCase):
    def setUp(self):
        Handler.request_json = None
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def test_streaming_client_uses_fresh_no_tool_request(self):
        client = StreamingFinalizerClient(
            f"http://127.0.0.1:{self.server.server_port}/v1",
            timeout_s=5,
        )
        response = client.complete(
            model="local-model",
            system_prompt="system",
            user_prompt="user",
            max_tokens=256,
        )
        self.assertEqual(response.content, '{"claims":[]}')
        self.assertEqual(response.finish_reasons[-1], "stop")
        self.assertTrue(response.done_seen)
        self.assertEqual(response.usage["total_tokens"], 13)
        request = Handler.request_json
        self.assertEqual(request["chat_template_kwargs"]["enable_thinking"], False)
        self.assertNotIn("tools", request)
        self.assertEqual([row["role"] for row in request["messages"]], ["system", "user"])

    def test_non_loopback_endpoint_is_rejected(self):
        with self.assertRaises(ValueError):
            StreamingFinalizerClient("http://example.com/v1")


if __name__ == "__main__":
    unittest.main()
