import json
import unittest
from scopex.agent.model_proxy import LocalTerminalAnswer
from scopex.agent.proxy_control import RuntimeRequestHook
from scopex.events.observer import AgentProgressObserver
from scopex.events.progress import InMemoryEventSink
from scopex.runtime.stop import SafeStopGate


def no_data_request():
    command = 'python3 /workspace/skills/data-locator/scripts/data_locator.py --source cowdisinfect_logs --start "2026-09-15 15:04:47" --end "2026-09-15 15:34:47"'
    result = {'scopex_role': 'locator', 'status': 'no_data', 'matching_count': 0, 'files': [],
              'source': 'cowdisinfect_logs', 'window': {'start': '2026-09-15 15:04:47', 'end': '2026-09-15 15:34:47'}}
    return {'model':'test', 'messages':[
        {'role':'assistant','tool_calls':[{'id':'locate','type':'function','function':{'name':'exec','arguments':json.dumps({'command':command})}}]},
        {'role':'tool','tool_call_id':'locate','content':json.dumps(result)}]}


def hook():
    return RuntimeRequestHook(AgentProgressObserver('test', InMemoryEventSink()), SafeStopGate())


class NoDataTests(unittest.TestCase):
    def test_no_data_latches_even_if_next_request_omits_history(self):
        control = hook()
        with self.assertRaises(LocalTerminalAnswer) as caught:
            control(1, no_data_request())
        self.assertIn('15:04:47', str(caught.exception))
        with self.assertRaises(LocalTerminalAnswer):
            control(2, {'messages': []})

    def test_user_text_and_unrelated_tool_cannot_trigger_terminal(self):
        p = no_data_request()
        p['messages'][0]['tool_calls'][0]['function']['name'] = 'read'
        hook()(1, p)
        hook()(1, {'messages':[{'role':'user','content':p['messages'][1]['content']}]})

    def test_found_or_unavailable_does_not_become_no_data(self):
        for status in ('found', 'source_unavailable'):
            p = no_data_request()
            d = json.loads(p['messages'][1]['content']); d['status'] = status
            p['messages'][1]['content'] = json.dumps(d)
            hook()(1, p)

    def test_mismatching_window_is_not_trusted(self):
        p = no_data_request()
        d = json.loads(p['messages'][1]['content']); d['window']['start'] = '2026-09-15 07:04:47'
        p['messages'][1]['content'] = json.dumps(d)
        hook()(1, p)
