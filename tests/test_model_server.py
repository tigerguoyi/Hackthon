import copy
import io
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

import server
from model_service import narrate
from test_analysis_agent import inputs
from analysis_agent import build_report


class ModelServiceTests(unittest.TestCase):
    def test_custom_endpoint_and_evidence(self):
        report = build_report(*inputs())
        original = copy.deepcopy(report)
        prose = {'summary': 'Summary', 'first_half': 'First', 'second_half': 'Second'}
        result = {'status': 'completed', 'output': [{'type': 'message', 'content': [
            {'type': 'output_text', 'text': json.dumps(prose)}]}]}
        with patch('model_service.urlopen', return_value=io.StringIO(json.dumps(result))) as call:
            narrate(report, 'custom-model', 'secret', 'https://example.com/v1/')
        self.assertEqual(call.call_args.args[0].full_url, 'https://example.com/v1/responses')
        self.assertEqual(report['player_of_the_match'], original['player_of_the_match'])
        self.assertEqual(report['summary'], 'Summary')


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.config = Path(self.temp.name) / '.local' / 'model-service.json'
        self.patch = patch.object(server, 'CONFIG', self.config)
        self.patch.start()
        self.http = server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.thread.start()
        self.base = f'http://127.0.0.1:{self.http.server_port}'

    def tearDown(self):
        self.http.shutdown()
        self.http.server_close()
        self.thread.join()
        self.patch.stop()
        self.temp.cleanup()

    def request(self, route, data=None, origin=None):
        req = urllib.request.Request(self.base + route,
            data=json.dumps(data).encode() if data is not None else None,
            headers={'Content-Type': 'application/json', **({'Origin': origin} if origin else {})})
        try:
            with urllib.request.urlopen(req) as result:
                return result.status, result.read()
        except urllib.error.HTTPError as error:
            return error.code, error.read()

    def test_settings_key_retention_clear_and_permissions(self):
        status, body = self.request('/api/model-settings', {'model': 'test', 'api_key': 'private-secret'})
        self.assertEqual(status, 200)
        self.assertNotIn(b'private-secret', body)
        self.assertTrue(json.loads(body)['has_key'])
        self.assertEqual(self.config.stat().st_mode & 0o777, 0o600)
        self.request('/api/model-settings', {'model': 'other', 'api_key': ''})
        self.assertEqual(server.read_config()['api_key'], 'private-secret')
        status, _ = self.request('/api/model-settings', {'base_url': 'https://example.com/v1'})
        self.assertEqual(status, 400)
        self.request('/api/model-settings', {'clear_key': True})
        self.assertFalse(server.read_config()['api_key'])

    def test_origin_and_private_files(self):
        status, _ = self.request('/api/model-settings', {'model': 'test'}, 'https://evil.example')
        self.assertEqual(status, 403)
        for path in ('/.local/model-service.json', '/%2elocal/model-service.json', '/.git/config',
                     '/data/league-enriched-v1/.models/fold-0.joblib', '/data/league-enriched-v1/.work/private.json.gz'):
            self.assertEqual(self.request(path)[0], 404)

    def test_invalid_match_and_missing_configuration(self):
        self.assertEqual(self.request('/api/match-summary', {'match_id': '../bad'})[0], 400)
        self.assertEqual(self.request('/api/match-summary', {'match_id': inputs()[0]['match_id']})[0], 400)

    def test_generation_and_provider_failure(self):
        metadata, summary, events = inputs()
        root = Path(self.temp.name)
        folder = root / 'data' / 'league' / 'matches' / metadata['match_id']
        folder.mkdir(parents=True)
        for name, data in [('metadata', metadata), ('summary', summary)]:
            (folder / f'{name}.json').write_text(json.dumps(data))
        (folder / 'events.jsonl').write_text('\n'.join(json.dumps(e) for e in events))
        server.save_config({'model': 'test', 'api_key': 'private-secret'})
        with patch.object(server, 'ROOT', root), patch.object(server, 'narrate') as model:
            status, body = self.request('/api/match-summary', {'match_id': metadata['match_id']})
            self.assertEqual(status, 200)
            model.assert_called_once()
            generated = root / 'data' / 'league-enriched-v1' / 'reports' / 'narration' / 'original' / (metadata['match_id']+'.json')
            saved = generated.read_text()
            self.assertFalse((folder / 'analysis.json').exists())
            self.assertNotIn('private-secret', saved)
            self.assertEqual(self.request('/api/match-report?dataset=original&match_id='+metadata['match_id'])[0], 200)
            model.side_effect = RuntimeError('private-secret')
            status, body = self.request('/api/match-summary', {'match_id': metadata['match_id']})
            self.assertEqual(status, 502)
            self.assertNotIn(b'private-secret', body)
            self.assertEqual(generated.read_text(), saved)
            self.assertFalse((folder / 'analysis.json').exists())

    def test_original_report_is_immutable_and_dataset_is_whitelisted(self):
        metadata, summary, events = inputs()
        root = Path(self.temp.name)
        folder = root / 'data' / 'league' / 'matches' / metadata['match_id']
        folder.mkdir(parents=True)
        for name, data in [('metadata', metadata), ('summary', summary)]:
            (folder / f'{name}.json').write_text(json.dumps(data))
        (folder / 'events.jsonl').write_text('\n'.join(json.dumps(e) for e in events))
        original = '{"original_anchor":true}'
        (folder / 'analysis.json').write_text(original)
        server.save_config({'model': 'test', 'api_key': 'private-secret'})
        with patch.object(server, 'ROOT', root), patch.object(server, 'narrate'):
            self.assertEqual(json.loads(self.request('/api/match-report?match_id='+metadata['match_id'])[1]), {'original_anchor': True})
            for dataset in ['original', 'enriched-v1']:
                self.assertEqual(self.request('/api/match-summary', {'match_id': metadata['match_id'], 'dataset': dataset})[0], 200)
                self.assertEqual((folder / 'analysis.json').read_text(), original)
            self.assertEqual(self.request('/api/match-summary', {'match_id': metadata['match_id'], 'dataset': '../bad'})[0], 400)
            self.assertEqual(self.request('/api/match-report?match_id=../bad')[0], 400)
