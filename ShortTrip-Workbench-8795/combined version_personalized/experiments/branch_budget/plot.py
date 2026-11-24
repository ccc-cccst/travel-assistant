"""Plot measured branch concentration and worst penalty for each budget."""

import argparse
from pathlib import Path
from statistics import median

from experiments.evaluation.common import read_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("analysis", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.out.exists():
        raise FileExistsError(args.out)

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.ticker import PercentFormatter

    rows = read_json(args.analysis)["runs"]
    budgets = sorted({r["budget_id"] for r in rows}, key=lambda k: (k[0], float(k[1:])))
    labels = [f"{k[1:]} 次入口" if k.startswith("n") else f"{k[1:]} 秒" for k in budgets]
    methods = (("dfs", "普通 DFS", "#66758b"), ("round_robin", "轮转 DFS", "#087e82"))
    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
    plt.rcParams["axes.spines.top"] = False
    plt.rcParams["axes.spines.right"] = False
    fig, axes = plt.subplots(1, 2, figsize=(9.6, 3.7), layout="constrained")
    for arm, (method, label, color) in enumerate(methods):
        positions = [i + (arm - .5) * .34 for i in range(len(budgets))]
        for pos, budget in zip(positions, budgets):
            group = [r for r in rows if r["method"] == method and r["budget_id"] == budget]
            shares = [r["max_first_share"] for r in group if r["max_first_share"] is not None]
            penalties = [r["best_penalty"] for r in group if r["validity"] == "valid"]
            if shares:
                center = median(shares)
                axes[0].bar(pos, center, width=.29, color=color,
                            label=label if budget == budgets[0] else None)
                axes[0].errorbar(pos, center, yerr=[[center-min(shares)], [max(shares)-center]],
                                 color="#283241", capsize=3, linewidth=1)
                axes[0].text(pos, max(shares)+.025, f"{center:.0%}", ha="center", fontsize=9)
            if len(penalties) == len(group):
                worst = max(penalties)
                axes[1].bar(pos, worst, width=.29, color=color)
                axes[1].text(pos, worst+.45, f"{worst:.2f}", ha="center", fontsize=9, color=color)
            else:
                axes[1].text(pos, .45, "有失败", ha="center", fontsize=9, color=color)

    axes[0].set(title="最大首景点分支占用比例", ylim=(0, 1.16), ylabel="占首景点分支入口的比例")
    axes[0].yaxis.set_major_formatter(PercentFormatter(1))
    axes[0].legend(loc="upper center", bbox_to_anchor=(.5, -.19), ncol=2, frameon=False)
    axes[1].set(title="各顺序中最差的原软惩罚", ylabel="惩罚（越低越好）")
    axes[1].set_ylim(0, max(2, axes[1].get_ylim()[1] * 1.13))
    for ax in axes:
        ax.set_xticks(range(len(labels)), labels)
        ax.grid(axis="y", color="#dce2ea", linewidth=.6)
        ax.set_axisbelow(True)
    fig.suptitle("EXP003 · 同一冻结需求的六种首景点顺序", fontsize=12)
    fig.savefig(args.out, dpi=165)


if __name__ == "__main__":
    main()
