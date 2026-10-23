import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from analysis_agent import build_report, clock, narrate, write_report

ROOT = Path(__file__).resolve().parents[1]
MATCH = ROOT / 'data/league/matches/PL26-W01-MCI-CHE'


def inputs(path=MATCH):
    return (json.loads((path / 'metadata.json').read_text()),
            json.loads((path / 'summary.json').read_text()),
            [json.loads(line) for line in (path / 'events.jsonl').read_text().splitlines()])


class AnalysisTests(unittest.TestCase):
    def setUp(self):
        self.metadata, self.summary, self.events = inputs()

    def build(self):
        return build_report(self.metadata, self.summary, self.events)

    def test_waits_for_actual_match_end(self):
        self.events = [e for e in self.events if e['type'] != 'match_end']
        self.assertIsNone(self.build())

    def test_rejects_incomplete_and_mismatched_data(self):
        for kind in ('score', 'count', 'match', 'duplicate'):
            with self.subTest(kind=kind):
                m, s, e = inputs()
                if kind == 'score':
                    s['final_score'] = [99, 99]
                elif kind == 'count':
                    e.pop(10)
                elif kind == 'match':
                    m['match_id'] = 'other'
                else:
                    e[10] = copy.deepcopy(e[11])
                with self.assertRaises(ValueError):
                    build_report(m, s, e)

    def test_all_matches_have_valid_awards_and_full_coverage(self):
        paths = sorted((ROOT / 'data/league/matches').glob('*'))
        self.assertEqual(len(paths), 40)
        for path in paths:
            with self.subTest(match=path.name):
                m, s, e = inputs(path)
                report = build_report(m, s, e)
                ids = {event['event_id'] for event in e}
                self.assertEqual(report['source']['event_count'], len(e))
                self.assertEqual(report['periods'][-1]['score_at_end'], s['final_score'])
                self.assertEqual(report['ranking'][0]['player_id'], report['player_of_the_match']['player_id'])
                self.assertTrue(all(p['minutes'] > 0 for p in report['ranking']))
                self.assertTrue(all(set(p['evidence_event_ids']) <= ids for p in report['ranking']))
                self.assertEqual(sum(p['goals'] for p in report['ranking']), sum(s['final_score']))
                for team in ('HOME', 'AWAY'):
                    self.assertEqual(sum(p['shots'] for p in report['ranking'] if p['team_id'] == team),
                                     s['final_stats'][team]['shots'])

    def test_keeper_and_substitute_evidence_and_minutes(self):
        report = self.build()
        rows = {p['player_id']: p for p in report['ranking']}
        self.assertNotIn('H13', rows)
        self.assertNotIn('A14', rows)
        self.assertAlmostEqual(rows['H06']['minutes'] + rows['H12']['minutes'], 100, places=1)
        self.assertLess(rows['A05']['minutes'], 100)
        self.assertEqual(rows['A05']['red_cards'], 1)
        self.assertEqual(rows['H01']['saves'], sum(e['type'] == 'save' and e.get('player_id') == 'H01'
                                                 for e in self.events))

    def test_scoreless_draw_and_tie_break(self):
        self.events = [e for e in self.events if e['type'] in ('period_start', 'period_end', 'match_end')]
        self.events[-1]['score'] = [0, 0]
        self.summary['final_score'] = [0, 0]
        self.summary['event_count'] = len(self.events)
        report = self.build()
        self.assertIn('drew 0–0', report['summary'])
        self.assertEqual(report['key_moments'], [])
        self.assertEqual(report['player_of_the_match']['impact_points'], 0)
        self.assertIn('tie-break', report['player_of_the_match']['reasons'][-1])
        self.assertEqual(report['player_of_the_match']['player_id'], 'A01')

    def test_cache_and_changed_source(self):
        with tempfile.TemporaryDirectory() as path:
            a = write_report(path, self.metadata, self.summary, self.events, mode='local')
            stamp = (Path(path) / 'analysis.json').stat().st_mtime_ns
            b = write_report(path, self.metadata, self.summary, self.events, mode='local')
            self.assertEqual(a, b)
            self.assertEqual(stamp, (Path(path) / 'analysis.json').stat().st_mtime_ns)
            self.metadata['teams'][0]['name'] = 'Renamed Club'
            c = write_report(path, self.metadata, self.summary, self.events, mode='local')
            self.assertNotEqual(a['source']['fingerprint'], c['source']['fingerprint'])
            self.events.pop()
            self.assertIsNone(write_report(path, self.metadata, self.summary, self.events, mode='local'))
            self.assertFalse((Path(path) / 'analysis.json').exists())

    def test_missing_config_and_failed_model_fall_back(self):
        with tempfile.TemporaryDirectory() as path, patch.dict(os.environ, {}, clear=True):
            r = write_report(path, self.metadata, self.summary, self.events, mode='openai')
            self.assertEqual(r['agent']['mode'], 'local')
            self.assertIn('missing', r['agent']['notice'])
            with patch.dict(os.environ, {'OPENAI_API_KEY': 'test-secret', 'OPENAI_MODEL': 'test-model'}):
                with patch('model_service.urlopen', side_effect=TimeoutError('test-secret')):
                    r = write_report(path, self.metadata, self.summary, self.events, mode='openai')
                    self.assertIn('unavailable', r['agent']['notice'])
                    self.assertNotIn('test-secret', json.dumps(r))

    def test_structured_model_response_preserves_award(self):
        import io
        report = self.build()
        award = copy.deepcopy(report['player_of_the_match'])
        prose = {'summary': 'A simulated draw.', 'first_half': 'The hosts scored first.',
                 'second_half': 'The visitors equalised.'}
        result = {'status': 'completed', 'output': [{'type': 'message', 'content': [
            {'type': 'output_text', 'text': json.dumps(prose)}]}]}
        with patch('model_service.urlopen', return_value=io.StringIO(json.dumps(result))) as call:
            narrate(report, 'test-model', 'secret')
            payload = json.loads(call.call_args.args[0].data)
            self.assertFalse(payload['store'])
            self.assertEqual(payload['text']['format']['type'], 'json_schema')
        self.assertEqual(report['agent']['mode'], 'openai')
        self.assertEqual(report['summary'], prose['summary'])
        self.assertEqual(report['player_of_the_match'], award)

    def test_incomplete_model_response_is_not_published(self):
        import io
        with patch('model_service.urlopen',
                   return_value=io.StringIO('{"status":"incomplete","output":[]}')):
            with self.assertRaises(ValueError):
                narrate(self.build(), 'test-model', 'secret')

    def test_stoppage_clock_preserves_period(self):
        e = {'period': 1, 'end_ms': 3000000}
        self.assertEqual(clock(e, self.metadata), '45+5:00')
        e.update(period=2, end_ms=6000000)
        self.assertEqual(clock(e, self.metadata), '90+5:00')


if __name__ == '__main__':
    unittest.main()
