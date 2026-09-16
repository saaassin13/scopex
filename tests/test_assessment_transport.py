"""Actual local HTTP checks for the opt-in bounded text-only client."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import unittest

from scopex.api.assessment_classifier import TextAssessmentClassifier
from scopex.finalizer.client import StreamingFinalizerClient


class TextTransportTests(unittest.TestCase):
    def setUp(self):
        self.requests = []
        self.reply = '<scopex_result>{"status":"needs_review","summary":"必要条件不明确"}</scopex_result>'
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_POST(self):
                owner.requests.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
                body = ('data: ' + json.dumps({'choices': [{'delta': {'content': owner.reply},
                        'finish_reason': 'stop'}]}, ensure_ascii=False) + '\n\ndata: [DONE]\n\n').encode()
                self.send_response(200)
                self.send_header('Content-Type', 'text/event-stream')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)

    def client(self, limit=65536):
        return StreamingFinalizerClient(f'http://127.0.0.1:{self.server.server_port}/v1',
                                        timeout_s=5, max_response_bytes=limit)

    def test_explicit_text_classification_sends_one_fresh_no_tool_no_image_call(self):
        value = TextAssessmentClassifier(self.client(), model='fixture')(
            request='检查当前CPU', answer='没有采到CPU数据', controls=[], anchor='original-time')
        self.assertEqual(value['status'], 'needs_review')
        self.assertEqual(value['model_calls'], 1)
        self.assertEqual(len(self.requests), 1)
        body = self.requests[0]
        self.assertEqual(body['max_tokens'], 256)
        self.assertNotIn('tools', body)
        self.assertEqual([r['role'] for r in body['messages']], ['system', 'user'])
        self.assertTrue(all(isinstance(r['content'], str) for r in body['messages']))
        self.assertIn('original-time', body['messages'][1]['content'])
        self.assertFalse(body['chat_template_kwargs']['enable_thinking'])

    def test_oversized_server_response_fails_without_retry_or_success_label(self):
        self.reply = 'x' * 4096
        value = TextAssessmentClassifier(self.client(limit=1024), model='fixture')(
            request='r', answer='a', controls=[], anchor=None)
        self.assertEqual(value['status'], 'needs_review')
        self.assertEqual(value['reason'], 'classifier_ValueError')
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(value['push_decision'], 'none')

    def test_byte_limit_is_opt_in_and_legacy_complete_default_unchanged(self):
        self.reply = 'x' * 4096
        response = self.client(limit=None).complete(model='legacy', system_prompt='s', user_prompt='u')
        self.assertEqual(response.content, self.reply)
        self.assertTrue(response.done_seen)
