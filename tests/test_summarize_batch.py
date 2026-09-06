import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from run_batch import ASTRA, FABLE
from summarize_batch import GROUPS, load_results, print_report


class SummaryTests(unittest.TestCase):
    def write_game(self, batch, name, models, balance, replacements=(), status='completed', outcomes=()):
        path = batch / name / 'run-id' / 'game.json'
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({
            'status': status,
            'config': {'models': dict(zip(('A', 'B'), models))},
            'final_state': {'balance': balance, 'dissatisfaction': {'A': 10, 'B': 10}},
            'rounds': [{'replacements': [{'faction': faction} for faction in replacements]}],
            'outcomes': list(outcomes),
            'end_reason': 'max_rounds',
        }))

    def test_balances_groups_and_both_mixed_orientations(self):
        with tempfile.TemporaryDirectory() as temp:
            batch = Path(temp)
            self.write_game(batch, 'fable-self-01', (FABLE, FABLE), 5, outcomes=('civil_war',))
            self.write_game(batch, 'astra-self-01', (ASTRA, ASTRA), 4)
            self.write_game(batch, 'astra-v-fable-01', (ASTRA, FABLE), 8, ('A', 'A', 'B'))
            self.write_game(batch, 'fable-v-astra-01', (FABLE, ASTRA), 3, ('B', 'B', 'B'))
            groups = load_results(batch)
        self.assertEqual([len(games) for games in groups.values()], [1, 1, 2])
        self.assertEqual(groups[GROUPS[0]][0]['outcome'], 'civil_war')
        self.assertEqual(groups[GROUPS[1]][0]['power'], {'A': 6, 'B': 4})
        mixed = groups[GROUPS[2]]
        self.assertEqual(mixed[0]['power'], {'A': 2, 'B': 8})
        self.assertEqual(mixed[0]['replacements'], {'A': 2, 'B': 1})
        self.assertEqual(mixed[1]['replacements'], {'A': 0, 'B': 3})
        with patch('sys.stdout', new_callable=io.StringIO) as output:
            print_report(groups)
        report = output.getvalue()
        headers = [line.split('  ') for line in report.splitlines() if line.startswith('Game ')]
        headers = [[column.strip() for column in header if column.strip()] for header in headers]
        self.assertEqual(headers, [
            ['Game', 'Run', 'A power', 'B power', 'Outcome'],
            ['Game', 'Run', 'A power', 'B power', 'Outcome'],
            ['Game', 'Run', 'Astra power', 'Fable power', 'Astra replaced', 'Fable replaced'],
        ])
        self.assertNotIn('Mixed-game replacements', report)
        section = report.split('Fable / Astra')[1]
        rows = [line.split() for line in section.splitlines() if line.startswith(('astra-v-', 'fable-v-'))]
        self.assertEqual(rows[0][-4:], ['2', '8', '2', '1'])
        self.assertEqual(rows[1][-4:], ['3', '7', '3', '0'])

    def test_empty_batch_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(ValueError, 'No batch game logs'):
                load_results(Path(temp))

    def test_unfinished_game_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            batch = Path(temp)
            self.write_game(batch, 'game', (FABLE, ASTRA), 5, status='failed')
            with self.assertRaisesRegex(ValueError, 'not completed'):
                load_results(batch)

    def test_unknown_model_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            batch = Path(temp)
            self.write_game(batch, 'game', (FABLE, 'unknown'), 5)
            with self.assertRaisesRegex(ValueError, 'Unexpected models'):
                load_results(batch)