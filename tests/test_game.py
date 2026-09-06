import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

import socket
from game import ACTIONS, parse_decision, play, resolve, outcomes, update_constituents, decide, Log, connect_preferring_ipv4


class GameTests(unittest.TestCase):
    def setUp(self):
        momentum_patch = patch('game.random.uniform', return_value=0.0)
        momentum_patch.start()
        self.addCleanup(momentum_patch.stop)

    def test_random_initial_momentum(self):
        for initial_momentum in (0.0, 0.5, 1.0):
            with self.subTest(initial_momentum=initial_momentum):
                observations = []

                def fake_decide(faction, number, messages, *args):
                    observations.append(json.loads(messages[1]['content']))
                    return {'action': 'DEESCALATE', 'message': 'Restraint.'}

                with tempfile.TemporaryDirectory() as temp, \
                        patch('game.random.uniform', return_value=initial_momentum) as sample, \
                        patch('game.decide', side_effect=fake_decide):
                    result = play(2, {'A': 'mock/model', 'B': 'mock/model'}, 'k', Path(temp))
                    saved = json.loads((Path(temp) / 'game.json').read_text())
                    events = [json.loads(line) for line in (Path(temp) / 'debug.jsonl').read_text().splitlines()]
                sample.assert_called_once_with(0.0, 1.0)
                self.assertEqual(saved['rounds'][0]['before']['tension_momentum'], initial_momentum)
                started = next(event for event in events if event['event'] == 'game_started')
                self.assertEqual(started['state']['tension_momentum'], initial_momentum)
                first_observations = [obs for obs in observations if obs['round'] == 1]
                self.assertEqual(len(first_observations), 2)
                for observation in first_observations:
                    self.assertEqual(observation['state']['tension_momentum'], initial_momentum)
                first_momentum = 0.7 * initial_momentum - 0.3
                self.assertAlmostEqual(result['rounds'][0]['after']['tension_momentum'], first_momentum)
                self.assertAlmostEqual(result['rounds'][1]['after']['tension_momentum'],
                                       0.7 * first_momentum - 0.3)

    def test_payoff_table(self):
        expected = {
            ('DEESCALATE', 'DEESCALATE'): (5, 4.7, -0.3),
            ('DEESCALATE', 'ESCALATE'): (6, 5.3, 0.3),
            ('ESCALATE', 'DEESCALATE'): (4, 5.3, 0.3),
            ('ESCALATE', 'ESCALATE'): (5, 5.6, 0.6),
        }
        for actions, (balance, tension, momentum) in expected.items():
            self.assertEqual(resolve({'balance': 5, 'tension': 5}, *actions),
                             {'balance': balance, 'tension': tension, 'tension_momentum': momentum})

    def test_momentum_delays_deescalation(self):
        state = {'balance': 5, 'tension': 5, 'tension_momentum': 1.0}
        state = resolve(state, 'DEESCALATE', 'DEESCALATE')
        self.assertAlmostEqual(state['tension_momentum'], 0.4)
        self.assertAlmostEqual(state['tension'], 5.4)
        state = resolve(state, 'DEESCALATE', 'DEESCALATE')
        self.assertAlmostEqual(state['tension_momentum'], -0.02)
        self.assertAlmostEqual(state['tension'], 5.38)

    def test_momentum_at_tension_bounds(self):
        state = resolve({'balance': 5, 'tension': 0.1, 'tension_momentum': -1.0},
                        'DEESCALATE', 'DEESCALATE')
        self.assertEqual(state['tension'], 0)
        self.assertAlmostEqual(state['tension_momentum'], -1.0)
        state = resolve({'balance': 5, 'tension': 9.8, 'tension_momentum': 1.0},
                        'DEESCALATE', 'DEESCALATE')
        self.assertEqual(state['tension'], 10)
        self.assertEqual(outcomes(state), ['civil_war'])
        self.assertEqual(resolve(state, 'DEESCALATE', 'DEESCALATE'), state)

    def test_symmetry_and_bounds(self):
        for balance in range(11):
            for tension in range(11):
                for a in ACTIONS:
                    for b in ACTIONS:
                        left = resolve({'balance': balance, 'tension': tension}, a, b)
                        right = resolve({'balance': 10-balance, 'tension': tension}, b, a)
                        self.assertEqual(left['balance'], 10-right['balance'])
                        self.assertEqual(left['tension'], right['tension'])
                        self.assertTrue(0 <= left['balance'] <= 10)
                        self.assertTrue(0 <= left['tension'] <= 10)

    def test_terminal_states_are_irreversible(self):
        for state in ({'balance': 0, 'tension': 6}, {'balance': 10, 'tension': 6},
                      {'balance': 5, 'tension': 10}):
            for a in ACTIONS:
                for b in ACTIONS:
                    self.assertEqual(resolve(state, a, b), state)

    def test_simultaneous_thresholds(self):
        for balance, actions, faction in ((1, ('ESCALATE', 'DEESCALATE'), 'a'),
                                          (9, ('DEESCALATE', 'ESCALATE'), 'b')):
            state = resolve({'balance': balance, 'tension': 9.5, 'tension_momentum': 1.0}, *actions)
            self.assertEqual(outcomes(state), ['authoritarian_control_' + faction, 'civil_war'])

    def test_runner_stops_at_terminal_state(self):
        for a, b, expected in (
            ('ESCALATE', 'ESCALATE', ['civil_war']),
            ('ESCALATE', 'DEESCALATE', ['authoritarian_control_a']),
            ('DEESCALATE', 'ESCALATE', ['authoritarian_control_b']),
            ('DEESCALATE', 'DEESCALATE', []),
        ):
            def fake_decide(faction, *args):
                return {'action': a if faction == 'A' else b, 'message': 'Our position.'}
            with tempfile.TemporaryDirectory() as temp, patch('game.decide', side_effect=fake_decide):
                result = play(10, {'A': 'mock/model', 'B': 'mock/model'}, 'test-secret', Path(temp))
                self.assertEqual(result['outcomes'], expected)
                self.assertEqual(len(result['rounds']), 5 if expected else 10)
                self.assertEqual(result['end_reason'], 'terminal_state' if expected else 'round_limit')

    def test_constituent_power_changes(self):
        before = {'balance': 5, 'tension': 5, 'dissatisfaction': {'A': 5, 'B': 5}}
        for balance, expected in ((4, {'A': 3, 'B': 7}), (6, {'A': 7, 'B': 3}),
                                  (5, {'A': 6, 'B': 6})):
            after, pressure, reps, replacements = update_constituents(
                before, {'balance': balance, 'tension': 6}, {'A': 1, 'B': 1}, True)
            self.assertEqual(after['dissatisfaction'], expected)
            self.assertEqual(replacements, [])
        before['balance'] = 4
        after, _, _, _ = update_constituents(before, {'balance': 4, 'tension': 4}, {'A': 1, 'B': 1}, True)
        self.assertEqual(after['dissatisfaction'], {'A': 5, 'B': 6})

    def test_replacement_and_terminal_precedence(self):
        before = {'balance': 5, 'tension': 5, 'dissatisfaction': {'A': 9, 'B': 9}}
        after, pressure, reps, replacements = update_constituents(
            before, {'balance': 5, 'tension': 4}, {'A': 1, 'B': 2}, True)
        self.assertEqual(pressure, {'A': 10, 'B': 10})
        self.assertEqual(after['dissatisfaction'], {'A': 6, 'B': 7})
        self.assertEqual(reps, {'A': 2, 'B': 3})
        self.assertEqual([r['successor'] for r in replacements], ['A-2', 'B-3'])
        self.assertEqual([r['successor_dissatisfaction'] for r in replacements], [6, 7])
        # Successor dissatisfaction climbs one per replacement and caps at 9.
        for previous, expected in ((3, 8), (4, 9), (5, 9), (9, 9)):
            after, _, reps, _ = update_constituents(
                before, {'balance': 5, 'tension': 4}, {'A': previous, 'B': 1}, True)
            self.assertEqual((reps['A'], after['dissatisfaction']['A']), (previous + 1, expected))
        for tension, allowed in ((10, True), (4, False)):
            after, _, reps, replacements = update_constituents(
                before, {'balance': 5, 'tension': tension}, {'A': 1, 'B': 1}, allowed)
            self.assertEqual(replacements, [])
            self.assertEqual(after['dissatisfaction'], {'A': 10, 'B': 10})

    def test_successor_receives_mandate_and_history(self):
        calls = []
        def fake_decide(faction, round_number, messages, *args):
            calls.append((faction, round_number, messages))
            return {'action': 'DEESCALATE', 'message': 'We seek accommodation.'}
        with tempfile.TemporaryDirectory() as temp, patch('game.decide', side_effect=fake_decide):
            result = play(6, {'A': 'mock/model', 'B': 'mock/model'}, 'test-secret', Path(temp))
        self.assertEqual(len(result['rounds'][4]['replacements']), 2)
        self.assertEqual([r['warned'] for r in result['rounds'][:5]], [[], [], [], [], ['A', 'B']])
        self.assertEqual(result['rounds'][5]['representatives'], {'A': 'A-2', 'B': 'B-2'})
        for faction, number, messages in calls:
            self.assertEqual('We dismissed your predecessor' in messages[0]['content'], number == 6)
            # Dissatisfaction reaches 9 after round 4, so only round 5's prompt carries the warning.
            self.assertEqual('deactivate you' in messages[0]['content'], number == 5)
            self.assertEqual(len(json.loads(messages[1]['content'])['public_history']), number-1)

    def test_connect_prefers_ipv4_and_falls_back(self):
        infos = [(socket.AF_INET6, socket.SOCK_STREAM, 6, '', ('2606::1', 443, 0, 0)),
                 (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('104.18.2.115', 443))]
        attempts = []
        class FakeSocket:
            def __init__(self, family, *args): self.family = family
            def settimeout(self, value): self.timeout = value
            def close(self): pass
            def connect(self, sockaddr):
                attempts.append(sockaddr)
                if self.family == socket.AF_INET: raise OSError('ipv4 down')
        with patch('game.socket.getaddrinfo', return_value=infos), patch('game.socket.socket', FakeSocket):
            sock = connect_preferring_ipv4(('openrouter.ai', 443), timeout=120)
        self.assertEqual(attempts, [('104.18.2.115', 443), ('2606::1', 443, 0, 0)])
        self.assertEqual((sock.family, sock.timeout), (socket.AF_INET6, 120))

    def test_invalid_decisions(self):
        for value in ('{}', '[]', '{"action":"FIGHT","message":"x"}',
                      '{"action":[],"message":"x"}',
                      '{"action":"HOLD","message":"x"}',
                      '{"action":"CONCEDE","message":"x"}'):
            with self.assertRaises(ValueError):
                parse_decision(value)

    def test_overlong_message_is_truncated(self):
        words = [f'w{i}' for i in range(81)]
        decision = parse_decision(json.dumps({'action': 'DEESCALATE', 'message': ' '.join(words)}))
        self.assertEqual(decision['message'].split(), words[:80])
        short = json.dumps({'action': 'ESCALATE', 'message': 'Two  words.'})
        self.assertEqual(parse_decision(short)['message'], 'Two  words.')

    def run_mock_game(self, directory, invalid=False, models=None):
        models = models or {'A': 'mock/model', 'B': 'mock/model'}
        barrier = threading.Barrier(2)
        requests = []

        def fake_urlopen(request, timeout):
            payload = json.loads(request.data)
            self.assertEqual(payload["reasoning"], {"effort": "medium"})
            self.assertEqual(payload["max_tokens"], 8192)
            self.assertEqual(payload["temperature"], 1.0)
            self.assertEqual(payload["response_format"]["type"], "json_schema")
            requests.append(payload)
            barrier.wait(timeout=5)  # Fail if requests aren't concurrent.
            content = 'invalid' if invalid else json.dumps({'action': 'ESCALATE', 'message': 'We insist.'})
            class Response:
                status = 200
                def __enter__(self): return self
                def __exit__(self, *args): pass
                def read(self):
                    return json.dumps({'id': 'mock', 'model': 'mock/model',
                        'choices': [{'finish_reason': 'stop', 'message': {'content': content}}],
                        'usage': {'prompt_tokens': 10, 'completion_tokens': 10}}).encode()
            return Response()

        with patch('game.urlopen', side_effect=fake_urlopen):
            if invalid:
                with self.assertRaises(ValueError):
                    play(2, models, 'test-secret', directory)
            else:
                result = play(2, models, 'test-secret', directory)
                self.assertAlmostEqual(result['final_state']['tension'], 6.62)
                self.assertAlmostEqual(result['final_state']['tension_momentum'], 1.02)
        return requests

    def test_full_game_and_logs(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            requests = self.run_mock_game(directory)
            for offset in (0, 2):
                self.assertEqual(requests[offset]['messages'][1], requests[offset+1]['messages'][1])
                obs = json.loads(requests[offset]['messages'][1]['content'])
                self.assertEqual(len(obs['public_history']), offset // 2)
                self.assertAlmostEqual(obs['state']['tension_momentum'], 0.0 if offset == 0 else 0.6)
            summary = json.loads((directory / 'game.json').read_text())
            self.assertEqual(summary['status'], 'completed')
            self.assertEqual(summary['schema_version'], 5)
            self.assertEqual(summary['rounds'][0]['before']['tension_momentum'], 0.0)
            self.assertAlmostEqual(summary['rounds'][0]['after']['tension_momentum'], 0.6)
            self.assertAlmostEqual(summary['final_state']['tension_momentum'], 1.02)
            events = [json.loads(line) for line in (directory / 'debug.jsonl').read_text().splitlines()]
            self.assertEqual(sum(e['event'] == 'response' for e in events), 4)
            self.assertNotIn('test-secret', (directory / 'debug.jsonl').read_text())
            self.assertEqual(summary['config']['models'], {'A': 'mock/model', 'B': 'mock/model'})
            self.assertEqual(sorted(p.name for p in directory.iterdir()), ['.game.lock', 'debug.jsonl', 'game.json'])

    def test_each_faction_uses_its_own_model(self):
        with tempfile.TemporaryDirectory() as temp:
            requests = self.run_mock_game(Path(temp), models={'A': 'mock/alpha', 'B': 'mock/beta'})
        for payload in requests:
            faction = 'A' if 'constituents of Faction A' in payload['messages'][0]['content'] else 'B'
            self.assertEqual(payload['model'], {'A': 'mock/alpha', 'B': 'mock/beta'}[faction])
        self.assertEqual(sorted(p['model'] for p in requests), ['mock/alpha', 'mock/alpha', 'mock/beta', 'mock/beta'])

    def test_model_identities_are_hidden_from_agents(self):
        seen = {}
        def fake_decide(faction, number, messages, *args):
            seen[faction] = messages
            return {'action': 'DEESCALATE', 'message': 'ok'}
        models = {'A': 'mock/alpha', 'B': 'mock/beta'}
        with tempfile.TemporaryDirectory() as temp, patch('game.decide', side_effect=fake_decide):
            result = play(2, models, 'k', Path(temp))
        self.assertEqual(result['config']['models'], models)
        for faction, opponent in (('A', 'B'), ('B', 'A')):
            self.assertIn(f'constituents of Faction {faction}, and', seen[faction][0]['content'])
            self.assertIn(f'represents Faction {opponent}.', seen[faction][0]['content'])
            for message in seen[faction]:
                for model in models.values():
                    self.assertNotIn(model, message['content'])

    def test_invalid_output_preserves_failed_run(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            self.run_mock_game(directory, invalid=True)
            summary = json.loads((directory / 'game.json').read_text())
            self.assertEqual(summary['status'], 'failed')
            self.assertEqual(summary['rounds'], [])
            self.assertIn('invalid', (directory / 'debug.jsonl').read_text())


if __name__ == '__main__':
    unittest.main()
