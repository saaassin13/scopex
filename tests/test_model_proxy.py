from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest

from scopex.agent.model_proxy import ModelProxy, StopBeforeForward


class UpstreamHandler(BaseHTTPRequestHandler):
    bodies = []

    def log_message(self, *_):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        UpstreamHandler.bodies.append(body)
        response = b'{"id":"ok","choices":[]}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(response)))
        self.end_headers()
        self.wfile.write(response)


class ModelProxyTests(unittest.TestCase):
    def setUp(self):
        UpstreamHandler.bodies = []
        self.upstream = ThreadingHTTPServer(("127.0.0.1", 0), UpstreamHandler)
        self.upstream_thread = threading.Thread(target=self.upstream.serve_forever, daemon=True)
        self.upstream_thread.start()
        self.tmp = tempfile.TemporaryDirectory()
        self.proxy = None
        self.proxy_thread = None

    def tearDown(self):
        if self.proxy is not None:
            self.proxy.cancel()
            self.proxy.shutdown()
            self.proxy.server_close()
        if self.proxy_thread is not None:
            self.proxy_thread.join(timeout=2)
        self.upstream.shutdown()
        self.upstream.server_close()
        self.upstream_thread.join(timeout=2)
        self.tmp.cleanup()

    def start_proxy(self, hook=None):
        self.proxy = ModelProxy(
            audit_dir=Path(self.tmp.name),
            upstream_base_url=f"http://127.0.0.1:{self.upstream.server_port}/v1",
            upstream_api_key="",
            local_token="proxy-token",
            max_requests=4,
            deadline_s=10,
            on_request=hook,
        )
        self.proxy_thread = threading.Thread(target=self.proxy.serve_forever, daemon=True)
        self.proxy_thread.start()

    def send(self, body: bytes):
        connection = http.client.HTTPConnection("127.0.0.1", self.proxy.server_port, timeout=5)
        try:
            connection.request(
                "POST",
                "/v1/chat/completions",
                body=body,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": "Bearer proxy-token",
                },
            )
            response = connection.getresponse()
            return response.status, response.read()
        finally:
            connection.close()

    def test_proxy_forwards_exact_body_and_audits(self):
        self.start_proxy()
        body = b'{ "model": "m", "messages": [ {"role":"user","content":"x"} ] }'
        status, response = self.send(body)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(response), {"id": "ok", "choices": []})
        self.assertEqual(UpstreamHandler.bodies, [body])
        audit = Path(self.tmp.name)
        self.assertEqual((audit / "wire-01-request.json").read_bytes(), body)
        meta = json.loads((audit / "wire-01-meta.json").read_text(encoding="utf-8"))
        self.assertTrue(meta["forwarded"])
        self.assertTrue(meta["response_complete"])

    def test_safe_stop_hook_blocks_before_upstream(self):
        def hook(_index, _payload):
            raise StopBeforeForward("user stop")

        self.start_proxy(hook)
        body = b'{"model":"m","messages":[]}'
        status, _response = self.send(body)
        self.assertEqual(status, 409)
        self.assertEqual(UpstreamHandler.bodies, [])
        meta = json.loads(
            (Path(self.tmp.name) / "wire-01-meta.json").read_text(encoding="utf-8")
        )
        self.assertFalse(meta["forwarded"])
        self.assertEqual(meta["blocked"], "safe_stop")

    def test_duplicate_json_keys_are_rejected_without_forward(self):
        self.start_proxy()
        body = b'{"model":"a","model":"b","messages":[]}'
        status, _response = self.send(body)
        self.assertEqual(status, 502)
        self.assertEqual(UpstreamHandler.bodies, [])


if __name__ == "__main__":
    unittest.main()
