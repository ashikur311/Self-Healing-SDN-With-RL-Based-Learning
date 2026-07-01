#!/usr/bin/env python3
"""
Baseline comparison (Paper fix #3).

Evaluates the PPO agent against simple baselines on the SAME stratified splits
and seeds as evaluate_agent.py (fix #2), so the numbers are directly comparable.

Baselines (implemented in pure NumPy — no scikit-learn, which would not fit on
this VM's disk; documented in PAPER_ROADMAP.md):
  * Loss-threshold rule : predict fault if normalized packet-loss >= 0.5.
                          The trivial "static rule" the paper claims to beat.
  * Logistic Regression : a simple learned linear classifier (GD).
  * k-NN (k=5)          : a simple nonlinear learned classifier.
  * PPO agent           : the paper's proposed method.

Expected takeaway (and an honest, publishable finding): because the task is
almost linearly separable under the zero-throughput labeling heuristic (see
fix #2), the simple baselines essentially TIE the PPO agent. That means the
contribution is the closed-loop self-healing SYSTEM (fix #1), not the RL
algorithm — which the paper should state plainly.

Run:  /home/mininet/venvs/ryu-4.34-py39/bin/python compare_methods.py
"""
import os
import numpy as np
from stable_baselines3 import PPO

from evaluate_agent import (load_and_label, norm_state, stratified_split,
                            oversample_train, confusion, scores,
                            SDNEnv, SEEDS, TEST_FRAC, TIMESTEPS)

OUT_TXT = '/home/mininet/self_healing_sdn/docs/baseline_comparison.txt'
OUT_PNG = '/home/mininet/self_healing_sdn/docs/baseline_comparison.png'


def to_matrix(df):
    X = np.array([norm_state(r) for _, r in df.iterrows()], dtype=np.float64)
    y = df['is_fault'].to_numpy().astype(int)
    return X, y


def loss_threshold(Xte, thr=0.5):
    return (Xte[:, 1] >= thr).astype(int)          # feature 1 = normalized loss


def logistic_regression(Xtr, ytr, Xte, epochs=800, lr=0.5):
    Xtr1 = np.hstack([Xtr, np.ones((len(Xtr), 1))])
    Xte1 = np.hstack([Xte, np.ones((len(Xte), 1))])
    w = np.zeros(Xtr1.shape[1])
    for _ in range(epochs):
        z = np.clip(Xtr1 @ w, -30, 30)
        p = 1.0 / (1.0 + np.exp(-z))
        w -= lr * (Xtr1.T @ (p - ytr) / len(ytr))
    pte = 1.0 / (1.0 + np.exp(-np.clip(Xte1 @ w, -30, 30)))
    return (pte >= 0.5).astype(int)


def knn(Xtr, ytr, Xte, k=5):
    preds = []
    for x in Xte:
        d = np.sum((Xtr - x) ** 2, axis=1)
        idx = np.argsort(d)[:k]
        preds.append(int(round(ytr[idx].mean())))
    return np.array(preds)


def ppo(train_bal, Xte, seed):
    model = PPO("MlpPolicy", SDNEnv(train_bal), verbose=0,
                learning_rate=0.001, seed=seed)
    model.learn(total_timesteps=TIMESTEPS)
    return np.array([1 if int(model.predict(x.astype(np.float32),
                                            deterministic=True)[0]) == 1 else 0
                     for x in Xte])


def main():
    df = load_and_label()
    methods = ['Loss-threshold rule', 'Logistic Regression', 'k-NN (k=5)', 'PPO agent']
    agg = {m: {'acc': [], 'prec': [], 'rec': [], 'f1': []} for m in methods}

    for seed in SEEDS:
        np.random.seed(seed)
        train, test = stratified_split(df, TEST_FRAC, seed)
        train_bal = oversample_train(train, seed)      # oversample TRAIN only
        Xtr, ytr = to_matrix(train_bal)
        Xte, yte = to_matrix(test)

        preds = {
            'Loss-threshold rule': loss_threshold(Xte),
            'Logistic Regression': logistic_regression(Xtr, ytr, Xte),
            'k-NN (k=5)': knn(Xtr, ytr, Xte),
            'PPO agent': ppo(train_bal, Xte, seed),
        }
        for m, yp in preds.items():
            tp, tn, fp, fn = confusion(yte.tolist(), yp.tolist())
            a, p, rc, f = scores(tp, tn, fp, fn)
            agg[m]['acc'].append(a); agg[m]['prec'].append(p)
            agg[m]['rec'].append(rc); agg[m]['f1'].append(f)

    lines = []

    def emit(s=""):
        print(s); lines.append(s)

    emit("==== METHOD COMPARISON (fix #3) — same splits/seeds as fix #2 ====")
    emit("%d seeds, stratified 70/30 split BEFORE oversampling.\n" % len(SEEDS))
    emit("%-22s %-14s %-14s %-14s %-14s" % ("method", "accuracy", "precision", "recall", "f1"))
    emit("-" * 80)
    for m in methods:
        emit("%-22s %-14s %-14s %-14s %-14s" % (
            m,
            "%.3f±%.3f" % (np.mean(agg[m]['acc']), np.std(agg[m]['acc'])),
            "%.3f±%.3f" % (np.mean(agg[m]['prec']), np.std(agg[m]['prec'])),
            "%.3f±%.3f" % (np.mean(agg[m]['rec']), np.std(agg[m]['rec'])),
            "%.3f±%.3f" % (np.mean(agg[m]['f1']), np.std(agg[m]['f1'])),
        ))
    emit("")
    emit("Reading: if the simple baselines tie the PPO agent, the RL algorithm is")
    emit("NOT the contribution — the closed-loop self-healing system is. State this.")

    os.makedirs(os.path.dirname(OUT_TXT), exist_ok=True)
    with open(OUT_TXT, 'w') as f:
        f.write("\n".join(lines) + "\n")
    emit("\nsaved -> %s" % OUT_TXT)

    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        f1_means = [np.mean(agg[m]['f1']) for m in methods]
        f1_std = [np.std(agg[m]['f1']) for m in methods]
        fig, ax = plt.subplots(figsize=(6, 3.6))
        colors = ['#888', '#888', '#888', '#1f77b4']
        ax.bar(range(len(methods)), f1_means, yerr=f1_std, capsize=4, color=colors)
        ax.set_xticks(range(len(methods)))
        ax.set_xticklabels(methods, rotation=20, ha='right', fontsize=8)
        ax.set_ylabel('F1 (held-out, mean±std)')
        ax.set_ylim(0, 1.05)
        ax.set_title('Fault detection: PPO vs simple baselines')
        fig.tight_layout()
        fig.savefig(OUT_PNG, dpi=150)
        emit("saved -> %s" % OUT_PNG)
    except Exception as e:  # noqa
        emit("(figure skipped: %s)" % e)


if __name__ == '__main__':
    main()
