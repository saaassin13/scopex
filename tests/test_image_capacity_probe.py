from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import unittest

from scripts.check_model_image_capacity import check_capacity
from scopex.model_capabilities import count_request_images


class CapacityProbeTests(unittest.TestCase):
    def probe(self, server_limit, requested):
        seen = []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                n = count_request_images(payload)
                seen.append(n)
                if n > server_limit:
                    body = json.dumps({'error': {'message': f'At most {server_limit} image(s) may be provided in one prompt.'}}).encode()
                    self.send_response(400)
                    self.send_header('Content-Length', str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                self.send_response(200)
                self.send_header('Content-Type', 'text/event-stream')
                self.end_headers()
                event = {'choices': [{'delta': {'content': 'OK'}, 'finish_reason': 'stop'}]}
                self.wfile.write(('data: ' + json.dumps(event) + '\n\ndata: [DONE]\n\n').encode())
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        t = threading.Thread(target=server.serve_forever, daemon=True); t.start()
        try:
            result = check_capacity(base_url=f'http://127.0.0.1:{server.server_port}/v1', model='test-model', images=requested)
        finally:
            server.shutdown(); server.server_close(); t.join(2)
        return result, seen

    def test_reports_four_image_server_rejection_before_business_work(self):
        result, seen = self.probe(4, 6)
        self.assertEqual(seen, [6])
        self.assertFalse(result['accepted'])
        self.assertIn('At most 4 image(s)', result['error'])

    def test_twelve_image_probe_succeeds_on_matching_server(self):
        result, seen = self.probe(12, 12)
        self.assertEqual(seen, [12])
        self.assertTrue(result['accepted'])
        self.assertEqual(result['verification_scope'], 'tiny_image_transport_only_not_visual_quality')


if __name__ == '__main__':
    unittest.main()
