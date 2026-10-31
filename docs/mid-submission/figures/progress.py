"""Stories per epic by state at the end of 2026-10-30. Run: uv run --with matplotlib python progress.py"""

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

EPICS = [
    ("E1 Foundations", 7, 0, 0),
    ("E2 Evaluation harness", 8, 0, 0),
    ("E3 Dataset and problem pool", 5, 2, 6),
    ("E4 Retrieval", 1, 1, 5),
    ("E5 Planner", 0, 1, 4),
    ("E6 Baselines", 1, 0, 4),
    ("E7 Gate fine-tuning", 0, 0, 4),
    ("E8 Execution, rollback", 1, 0, 2),
    ("E9 Experiments", 3, 0, 8),
    ("E10 Reporting", 0, 1, 5),
]

fig, ax = plt.subplots(figsize=(6, 3.2))
names = [e[0] for e in EPICS][::-1]
done = [e[1] for e in EPICS][::-1]
pr = [e[2] for e in EPICS][::-1]
rest = [e[3] for e in EPICS][::-1]
ax.barh(names, done, color="#3c78aa", label="done")
ax.barh(names, pr, left=done, color="#96bee1", label="open PR")
ax.barh(names, rest, left=[d + p for d, p in zip(done, pr)], color="#ebebeb", edgecolor="#777777", label="remaining")
ax.set_xlabel("stories")
ax.legend(loc="upper center", bbox_to_anchor=(0.45, -0.2), ncol=3, frameon=False)
for spine in ("top", "right"):
    ax.spines[spine].set_visible(False)
fig.tight_layout()
fig.savefig("progress.png", dpi=200)
