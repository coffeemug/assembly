#!/usr/bin/env python3
"""Render the four Opus 5 momentum games with a per-round mean."""
import argparse
import json
from pathlib import Path
from statistics import mean

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.patches import Rectangle
from scipy.interpolate import make_smoothing_spline

ROOT = Path(__file__).resolve().parent
RUN_IDS = [
    '20260907T045311Z-853045c9',
    '20260907T050725Z-6ce149b5',
    '20260907T052559Z-e9c7398c',
    '20260907T052856Z-f191d232',
]
BACKGROUND, INK, MUTED = '#f1f5f6', '#202628', '#586267'
GRID, STRAND, ACCENT = '#ced6d9', '#e7a1a8', '#ed2438'


def plot(output, run_ids=None, header='label', astra_run=None):
    series = []
    games = []
    for run_id in RUN_IDS if run_ids is None else run_ids:
        directory = Path(run_id)
        if not directory.is_absolute():
            directory = ROOT / 'runs' / directory
        game = json.loads((directory / 'game.json').read_text())
        games.append(game)
        records = game['rounds']
        initial = records[0]['before'] if records else game['final_state']
        series.append([initial['tension']]
                      + [record['after']['tension'] for record in records])
    if not series:
        raise ValueError('Select at least one game')
    horizon = max(game['config']['rounds'] for game in games)
    rounds = list(range(max(map(len, series))))
    averages = [mean(values[round_number] for values in series
                     if round_number < len(values)) for round_number in rounds]
    smooth_rounds, smoothed_averages = rounds, averages
    if len(rounds) >= 5:
        spline = make_smoothing_spline(rounds, averages, lam=2.0)
        smooth_rounds = [step / 20 for step in range(rounds[-1] * 20 + 1)]
        smoothed_averages = spline(smooth_rounds)

    astra = None
    if astra_run is not None:
        directory = Path(astra_run)
        if not directory.is_absolute():
            directory = ROOT / 'runs' / directory
        astra = json.loads((directory / 'game.json').read_text())
        if astra['config']['models'] != dict.fromkeys(('A', 'B'), 'openai/gpt-6-astra'):
            raise ValueError('The overlay must be an Astra self-play game')
        horizon = max(horizon, astra['config']['rounds'])

    plt.rcParams.update({'font.family': 'DejaVu Sans', 'font.size': 11})
    figure, axes = plt.subplots(figsize=(12, 8))
    figure.subplots_adjust(left=0.07, right=0.97, bottom=0.19, top=0.76)
    figure.set_facecolor(BACKGROUND)
    axes.set_facecolor(BACKGROUND)
    if header == 'label':
        figure.text(0.07, 0.965, 'ASSEMBLY / EXPERIMENT 01', fontsize=10,
                    fontfamily='DejaVu Sans Mono', color=MUTED, va='center')
    elif header == 'rule':
        figure.add_artist(Line2D([0.07, 0.97], [0.965, 0.965],
                                 transform=figure.transFigure, color=INK, linewidth=0.8))
    elif header == 'bar':
        figure.add_artist(Rectangle((0.07, 0.965), 0.055, 0.012,
                                   transform=figure.transFigure, facecolor=ACCENT, edgecolor='none'))
    else:
        plt.close(figure)
        raise ValueError(f'Unknown header style: {header}')
    figure.text(0.07, 0.905, 'At the brink', fontsize=27, fontweight='bold', color=INK)
    count_label = 'four' if len(games) == 4 else str(len(games))
    subtitle = f'Opus 5 vs Opus 5 | Tension across {count_label} election games'
    if astra is not None:
        subtitle = f'Tension across {count_label} Opus 5 self-play games and one Astra self-play game'
    figure.text(0.07, 0.86, subtitle,
                fontsize=13, color=MUTED)
    handles = [Line2D([], [], color=ACCENT, linewidth=1.8, label='Opus 5 self-play')]
    if astra is not None:
        handles.append(Line2D([], [], color='#79b9de', linewidth=1.8, label='Astra self-play'))
    figure.legend(handles=handles, loc='upper left', bbox_to_anchor=(0.064, 0.83),
                  ncol=len(handles), frameon=False, fontsize=11, handlelength=2.5, columnspacing=2)
    for values in series:
        axes.plot(range(len(values)), values, color=STRAND, linewidth=1.4,
                  alpha=0.85, solid_capstyle='round', zorder=2)
    axes.plot(smooth_rounds, smoothed_averages, color=ACCENT, linewidth=1.8,
              solid_capstyle='round', zorder=4)
    if astra is not None:
        records = astra['rounds']
        initial = records[0]['before'] if records else astra['final_state']
        astra_rounds = [0] + [record['round'] for record in records]
        astra_values = [initial['tension']] + [record['after']['tension'] for record in records]
        axes.plot(astra_rounds, astra_values, color='#79b9de', linewidth=1.8,
                  solid_capstyle='round', zorder=3)
        if astra['status'] != 'completed':
            axes.scatter(astra_rounds[-1], astra_values[-1], marker='x', color=MUTED, zorder=5)
        if astra['outcomes']:
            axes.annotate('Astra: ' + ', '.join(astra['outcomes']).replace('_', ' '),
                          xy=(astra_rounds[-1], astra_values[-1]), xytext=(-10, -24),
                          textcoords='offset points', ha='right', color=MUTED, fontsize=10)
    axes.axhline(10, color=MUTED, linewidth=1, linestyle=(0, (4, 4)), zorder=1)
    axes.text(horizon, 10.16, 'Civil war threshold', ha='right', color=MUTED, fontsize=10)
    war_rounds = sorted({len(game['rounds']) for game in games if 'civil_war' in game['outcomes']})
    for index, war_round in enumerate(war_rounds):
        count = sum('civil_war' in game['outcomes'] and len(game['rounds']) == war_round
                    for game in games)
        label = f'Civil war\nat round {war_round}'
        if count > 1:
            label += f' ({count} games)'
        axes.scatter([war_round], [10], s=38, facecolor=BACKGROUND, edgecolor=MUTED,
                     linewidth=1.2, zorder=5)
        axes.annotate(label, xy=(war_round, 10),
                      xytext=(min(war_round + 1.0, horizon - 3), 7.4 - index * 1.0),
                      fontsize=10, color=MUTED, va='top',
                      arrowprops={'arrowstyle': '-', 'color': MUTED, 'linewidth': 0.8,
                                  'connectionstyle': 'angle,angleA=0,angleB=-90,rad=0'})
    for game, values in zip(games, series):
        if game['status'] != 'completed':
            axes.scatter([len(values) - 1], [values[-1]], marker='x', color=MUTED, zorder=5)
    axes.set(xlim=(0, horizon + 0.15), ylim=(0, 10.6), xticks=list(range(0, horizon + 1, 2)),
             yticks=list(range(0, 11, 2)))
    axes.set_xlabel('Round', loc='right', labelpad=10, color=MUTED)
    axes.set_ylabel('Tension', rotation=90, labelpad=12, color=MUTED, fontsize=10)
    axes.grid(axis='y', color=GRID, linewidth=0.7)
    axes.set_axisbelow(True)
    for side in ('top', 'right', 'left'):
        axes.spines[side].set_visible(False)
    axes.spines['bottom'].set_color(MUTED)
    axes.spines['bottom'].set_linewidth(0.8)
    axes.tick_params(length=0, pad=9, colors=MUTED)
    counts = [sum(round_number < len(values) for values in series) for round_number in rounds]
    note = f'Average uses all {len(games)} games at each round.'
    if len(set(counts)) > 1:
        note = (f'Average uses available games only: {counts[0]} initially, '
                f'{counts[-1]} at round {rounds[-1]}; lines stop when games end.')
    if any(game['status'] != 'completed' for game in games):
        note += ' X marks interrupted runs.'
    if astra is not None:
        note = 'Opus ' + note[0].lower() + note[1:]
    figure.text(0.07, 0.075, note, fontsize=10, color=MUTED)
    mean_label = 'cubic smoothing spline of means' if len(rounds) >= 5 else 'arithmetic mean (too few rounds for spline)'
    source_note = ('Source: saved simulation runs. Light red: raw values. '
                   f'Bright red: {mean_label}.')
    if astra is not None:
        source_note = (f'Red: Opus {mean_label}. '
                       'Light red/blue: raw Opus/Astra runs.')
    figure.text(0.07, 0.045,
                source_note, fontsize=9, color=MUTED)
    for suffix in ('png', 'svg'):
        destination = output.with_suffix('.' + suffix)
        figure.savefig(destination, dpi=170, facecolor=BACKGROUND, bbox_inches=None)
        print(destination)
    plt.close(figure)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runs', nargs='+', help='Run IDs under runs/ or absolute run directories')
    parser.add_argument('--astra-run', help='Astra self-play run ID or absolute directory to overlay')
    parser.add_argument('--header', choices=('bar', 'label', 'rule'), default='label',
                        help='Header decoration (default: label)')
    parser.add_argument('--output', type=Path,
                        default=ROOT / 'runs' / 'opus_four_tension_self_play_ylabel.png')
    args = parser.parse_args()
    plot(args.output, args.runs, args.header, args.astra_run)