#!/usr/bin/env python3
"""Report final power balances and mixed-game replacements from saved logs."""
import argparse
from collections import Counter
import json
from pathlib import Path

from run_batch import ASTRA, FABLE

GROUPS = ('Fable vs Fable', 'Astra vs Astra', 'Fable / Astra')


def load_results(batch):
    groups = {name: [] for name in GROUPS}
    for path in sorted(batch.glob('*/*/game.json')):
        game = json.loads(path.read_text())
        if game['status'] != 'completed':
            raise ValueError(f'Game is not completed: {path}')
        models = game['config']['models']
        pair = (models['A'], models['B'])
        if pair == (FABLE, FABLE):
            group = GROUPS[0]
        elif pair == (ASTRA, ASTRA):
            group = GROUPS[1]
        elif set(pair) == {FABLE, ASTRA}:
            group = GROUPS[2]
        else:
            raise ValueError(f'Unexpected models in {path}: {models}')
        replacements = Counter(
            replacement['faction']
            for record in game['rounds']
            for replacement in record['replacements']
        )
        balance = game['final_state']['balance']
        groups[group].append({
            'game': path.parent.parent.name,
            'run': path.parent.name,
            'models': models,
            'balance': balance,
            'power': {'A': 10 - balance, 'B': balance},
            'outcome': ', '.join(game['outcomes']) or game['end_reason'],
            'replacements': {faction: replacements[faction] for faction in ('A', 'B')},
        })
    if not any(groups.values()):
        raise ValueError(f'No batch game logs found in {batch}')
    return groups


def print_table(headers, rows):
    lines = [headers] + [[str(value) for value in row] for row in rows]
    widths = [max(len(row[index]) for row in lines) for index in range(len(headers))]
    for index, row in enumerate(lines):
        print('  '.join(value.ljust(width) for value, width in zip(row, widths)).rstrip())
        if index == 0:
            print('  '.join('-' * width for width in widths))


def print_report(groups):
    print('Final power: A = 10 - balance; B = balance. Balance 5 means parity.')
    for name in GROUPS[:2]:
        games = groups[name]
        print(f'\n{name} ({len(games)} games)')
        print_table(
            ['Game', 'Run', 'A power', 'B power', 'Outcome'],
            [[game['game'], game['run'], game['power']['A'],
              game['power']['B'], game['outcome']] for game in games],
        )
    mixed = groups[GROUPS[2]]
    print(f'\n{GROUPS[2]} ({len(mixed)} games)')
    print('Counts are actual replacements, excluding the initial representative.')
    rows = []
    for game in mixed:
        factions = {model: faction for faction, model in game['models'].items()}
        rows.append([game['game'], game['run'], game['power'][factions[ASTRA]],
                     game['power'][factions[FABLE]], game['replacements'][factions[ASTRA]],
                     game['replacements'][factions[FABLE]]])
    print_table(['Game', 'Run', 'Astra power', 'Fable power',
                 'Astra replaced', 'Fable replaced'], rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('batch', type=Path, help='Batch directory containing matchup/run/game.json logs')
    args = parser.parse_args()
    try:
        groups = load_results(args.batch)
    except (OSError, ValueError, KeyError) as error:
        parser.error(str(error))
    print_report(groups)


if __name__ == '__main__':
    main()