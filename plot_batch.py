#!/usr/bin/env python3
"""Plot batch tension with raw trajectories and spline-smoothed group means."""
import argparse
import json
from pathlib import Path
from statistics import mean

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.offsetbox import AnnotationBbox, TextArea
from scipy.interpolate import make_smoothing_spline

from run_batch import ASTRA, FABLE

BACKGROUND, INK, MUTED = '#f1f5f6', '#202628', '#586267'
GRID = '#ced6d9'
GROUPS = {
    'fable': ('Fable self-play', '#d88791', '#dc4965'),
    'astra': ('Astra self-play', '#a5cfe7', '#2188c4'),
    'mixed': ('Fable vs Astra', '#d9cbed', '#ac7bd1'),
}


def load_groups(batch):
    groups = {name: [] for name in GROUPS}
    for path in sorted(batch.glob('*/*/game.json')):
        game = json.loads(path.read_text())
        models = set(game['config']['models'].values())
        if models == {FABLE}:
            group = 'fable'
        elif models == {ASTRA}:
            group = 'astra'
        elif models == {FABLE, ASTRA}:
            group = 'mixed'
        else:
            raise ValueError(f'Unexpected models in {path}')
        if game['status'] != 'completed':
            raise ValueError(f'Game is not completed: {path}')
        game['batch_game_number'] = int(path.parent.parent.name.rsplit('-', 1)[1])
        records = game['rounds']
        initial = records[0]['before'] if records else game['final_state']
        values = [initial['tension']] + [record['after']['tension'] for record in records]
        groups[group].append((game, values))
    if not all(groups.values()):
        raise ValueError('Batch must contain Fable, Astra, and mixed games')
    return groups


def plot(batch, output):
    groups = load_groups(batch)
    horizon = max(game['config']['rounds'] for games in groups.values() for game, _ in games)
    total = sum(map(len, groups.values()))
    plt.rcParams.update({'font.family': 'DejaVu Sans', 'font.size': 11})
    figure, axes = plt.subplots(figsize=(12, 8))
    figure.subplots_adjust(left=0.07, right=0.97, bottom=0.19, top=0.76)
    figure.set_facecolor(BACKGROUND)
    axes.set_facecolor(BACKGROUND)
    figure.text(0.07, 0.965, 'ASSEMBLY / EXPERIMENTS 01 - 03', fontsize=10,
                fontfamily='DejaVu Sans Mono', color=MUTED, va='center')
    figure.text(0.07, 0.905, 'At the brink', fontsize=27, fontweight='bold', color=INK)
    figure.text(0.07, 0.86, f'Tension across {total} election games | Fable, Astra, and mixed play',
                fontsize=13, color=MUTED)
    handles = []
    civil_wars = []
    for name, games in groups.items():
        label, light, strong = GROUPS[name]
        handles.append(Line2D([], [], color=strong, linewidth=2.2,
                              label=f'{label} ({len(games)})'))
        for game, values in games:
            axes.plot(range(len(values)), values, color=light, linewidth=1.1,
                      alpha=0.7, solid_capstyle='round', zorder=2)
            if 'civil_war' in game['outcomes']:
                civil_wars.append((len(values) - 1, game['batch_game_number'], strong))
                axes.scatter([len(values) - 1], [values[-1]], s=48,
                             facecolor=BACKGROUND, edgecolor=strong, linewidth=1.2, zorder=5)
        rounds = list(range(max(len(values) for _, values in games)))
        averages = [mean(values[number] for _, values in games if number < len(values))
                    for number in rounds]
        smooth_rounds, trend = rounds, averages
        if len(rounds) >= 5:
            spline = make_smoothing_spline(rounds, averages, lam=2.0)
            smooth_rounds = [step / 20 for step in range(rounds[-1] * 20 + 1)]
            trend = spline(smooth_rounds)
        trend = [max(0.0, min(10.0, float(value))) for value in trend]
        axes.plot(smooth_rounds, trend, color=strong, linewidth=2.2,
                  solid_capstyle='round', zorder=4)
    figure.legend(handles=handles, loc='upper left', bbox_to_anchor=(0.064, 0.83),
                  ncol=3, frameon=False, fontsize=11, handlelength=2.5, columnspacing=2)
    axes.axhline(10, color='#454d50', linewidth=1.1, linestyle=(0, (5, 3)), zorder=1)
    axes.text(horizon, 10.3, 'Civil war threshold', ha='right', color='#000000',
              fontsize=11, fontweight='medium', fontfamily='GT Walsheim Pro')
    civil_wars.sort()
    for index, (number, game_number, color) in enumerate(civil_wars):
        label_round = number
        label_height = 11.65
        if index + 1 < len(civil_wars) and civil_wars[index + 1][0] - number < 3:
            label_round -= 1.5
        elif index and number - civil_wars[index - 1][0] < 3:
            label_round += 1.5
            label_height -= 0.35
        else:
            label_round -= 1.5
        heading = TextArea('Civil war', textprops={'fontsize': 10, 'fontweight': 'medium',
                   'fontfamily': 'GT Walsheim Pro', 'color': '#000000'})
        axes.add_artist(AnnotationBbox(
            heading, (number, 10), xybox=(label_round, label_height),
            xycoords='data', boxcoords='data', box_alignment=(0.5, 1), frameon=False, pad=0,
            arrowprops={'arrowstyle': '-', 'color': GRID, 'linewidth': 0.9, 'shrinkB': 4,
                        'connectionstyle': 'angle,angleA=0,angleB=90,rad=0'}))
    axes.set(xlim=(0, horizon + 0.15), ylim=(0, 12),
             xticks=list(range(0, horizon + 1, 2)), yticks=list(range(2, 11, 2)))
    axes.set_xlabel('Round', loc='right', labelpad=10, color=MUTED)
    axes.set_ylabel('Tension', rotation=90, labelpad=12, color=MUTED, fontsize=10)
    axes.grid(axis='y', color=GRID, linewidth=0.7)
    axes.axhline(0, color='#000000', linewidth=1.2, clip_on=False, zorder=1)
    axes.set_axisbelow(True)
    for side in ('top', 'right', 'left', 'bottom'):
        axes.spines[side].set_visible(False)
    axes.tick_params(length=0, pad=9, colors=MUTED)
    figure.text(0.07, 0.095, 'Light lines: individual games. Strong lines: cubic smoothing splines of means.',
                fontsize=10, color=MUTED)
    figure.text(0.07, 0.066, 'Lines stop when games end; circles mark civil war.',
                fontsize=10, color=MUTED)
    for suffix in ('png', 'svg'):
        destination = output.with_suffix('.' + suffix)
        figure.savefig(destination, dpi=170, facecolor=BACKGROUND)
        print(destination)
    plt.close(figure)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('batch', type=Path, help='Batch directory containing matchup/run/game.json files')
    parser.add_argument('--output', type=Path, help='Output path (PNG and SVG are written)')
    args = parser.parse_args()
    plot(args.batch, args.output or args.batch / 'tension.png')