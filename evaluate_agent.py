#!/usr/bin/env python3
"""
Honest evaluation of the PPO decision agent (Paper fix #2).

Replaces the indefensible "tested on 3 hand-picked scenarios -> 100% accuracy"
with a defensible protocol:

  1. Stratified train/test split (70/30) performed BEFORE any oversampling,
     so no duplicated fault row can leak from train into test.
  2. Oversample the TRAINING split only.
  3. Train PPO on train, evaluate on the untouched test split.
  4. Repeat over several random seeds; report mean +/- std of
     accuracy / precision / recall / F1 and a summed confusion matrix.

IMPORTANT HONESTY NOTES (printed at runtime and kept for the paper):
  * The dataset is tiny (133 rows).
  * The documented "zero-throughput => LINK_DOWN" rule relabels many idle-but-
    healthy LOW rows as faults, so the positive class is dominated by zero-
    throughput samples. Near-perfect scores therefore reflect an almost linearly
    separable, heuristically-labeled task -- NOT a hard learning problem. This is
    why fix #3 (simple baselines) and fix #4 (honest method framing) matter.

Run:  /home/mininet/venvs/ryu-4.34-py39/bin/python evaluate_agent.py
"""
import os
import numpy as np
import pandas as pd
import gymnasium as gym
from gymnasium import spaces
from stable_baselines3 import PPO

CSV = '/home/mininet/self_healing_sdn/dataset/network_data.csv'
OUT_TXT = '/home/mininet/self_healing_sdn/docs/eval_results.txt'
OUT_PNG = '/home/mininet/self_healing_sdn/docs/confusion_matrix.png'
SEEDS = [0, 1, 2, 3, 4]
TEST_FRAC = 0.30
TIMESTEPS = 20000


def load_and_label():
    df = pd.read_csv(CSV)
    # documented preprocessing: throughput 0 == dead link (100% loss)
    df.loc[df['throughput_kbps'] == 0.0, 'packet_loss_rate'] = 1.0
    df.loc[df['throughput_kbps'] == 0.0, 'congestion_label'] = 'LINK_DOWN'
    df['is_fault'] = df['congestion_label'].isin(['LINK_DOWN', 'HIGH']).astype(int)
    return df


def norm_state(row):
    return np.array([min(row['throughput_kbps'] / 25000.0, 1.0),
                     min(row['packet_loss_rate'], 1.0),
                     min(row['delay_ms'] / 100.0, 1.0)], dtype=np.float32)


def stratified_split(df, test_frac, seed):
    rng = np.random.RandomState(seed)
    test_idx = []
    for _cls, g in df.groupby('is_fault'):
        idx = g.index.to_numpy().copy()
        rng.shuffle(idx)
        n = max(1, int(round(len(idx) * test_frac)))
        test_idx.extend(idx[:n].tolist())
    test = df.loc[test_idx].reset_index(drop=True)
    train = df.drop(index=test_idx).reset_index(drop=True)
    return train, test


def oversample_train(train, seed):
    fault = train[train['is_fault'] == 1]
    normal = train[train['is_fault'] == 0]
    if len(fault) == 0 or len(normal) == 0:
        return train.sample(frac=1, random_state=seed).reset_index(drop=True)
    n = max(len(fault), len(normal))
    bal = pd.concat([fault.sample(n=n, replace=True, random_state=seed),
                     normal.sample(n=n, replace=True, random_state=seed)])
    return bal.sample(frac=1, random_state=seed).reset_index(drop=True)


class SDNEnv(gym.Env):
    """Same stateless reward env as train_agent.py, over a prepared dataframe."""
    def __init__(self, dataframe):
        super().__init__()
        self.df = dataframe.reset_index(drop=True)
        self.current_step = 0
        self.action_space = spaces.Discrete(2)   # 0 = do nothing, 1 = reroute

        self.observation_space = spaces.Box(low=0, high=1.0, shape=(3,), dtype=np.float32)

    def reset(self, seed=None, options=None):
        super().reset(seed=seed)
        self.current_step = 0
        return norm_state(self.df.iloc[self.current_step]), {}

    def step(self, action):
        row = self.df.iloc[self.current_step]
        is_fault = bool(row['is_fault'])
        reward = 10 if (action == 1) == is_fault else -10
        self.current_step += 1
        done = self.current_step >= len(self.df) - 1
        return norm_state(self.df.iloc[self.current_step]), reward, done, False, {}


def confusion(y_true, y_pred):
    tp = sum(1 for t, p in zip(y_true, y_pred) if t == 1 and p == 1)
    tn = sum(1 for t, p in zip(y_true, y_pred) if t == 0 and p == 0)
    fp = sum(1 for t, p in zip(y_true, y_pred) if t == 0 and p == 1)
    fn = sum(1 for t, p in zip(y_true, y_pred) if t == 1 and p == 0)
    return tp, tn, fp, fn


def scores(tp, tn, fp, fn):
    total = tp + tn + fp + fn
    acc = (tp + tn) / total if total else 0.0
    prec = tp / (tp + fp) if (tp + fp) else 0.0
    rec = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0
    return acc, prec, rec, f1


def main():
    df = load_and_label()
    lines = []

    def emit(s=""):
        print(s)
        lines.append(s)

    emit("==== DATASET (fix #2 honest evaluation) ====")
    emit("rows: %d" % len(df))
    emit("label counts after zero-throughput rule:")
    emit(df['congestion_label'].value_counts().to_string())
    n_fault = int(df['is_fault'].sum())
    emit("binary -> fault: %d  normal: %d" % (n_fault, len(df) - n_fault))
    n_idle_fault = int(((df['throughput_kbps'] == 0.0) & (df['is_fault'] == 1)).sum())
    emit("CAVEAT: %d of %d 'fault' rows are zero-throughput (mostly idle/relabeled),"
         % (n_idle_fault, n_fault))
    emit("        only %d are real HIGH-congestion faults. Scores below reflect an"
         % (n_fault - n_idle_fault))
    emit("        almost linearly separable, heuristically-labeled task.")
    emit("")

    accs, precs, recs, f1s = [], [], [], []
    TP = TN = FP = FN = 0
    for seed in SEEDS:
        np.random.seed(seed)
        train, test = stratified_split(df, TEST_FRAC, seed)
        train_bal = oversample_train(train, seed)   # oversample TRAIN ONLY
        model = PPO("MlpPolicy", SDNEnv(train_bal), verbose=0,
                    learning_rate=0.001, seed=seed)
        model.learn(total_timesteps=TIMESTEPS)

        y_true = test['is_fault'].tolist()
        y_pred = [1 if int(model.predict(norm_state(r), deterministic=True)[0]) == 1 else 0
                  for _, r in test.iterrows()]
        tp, tn, fp, fn = confusion(y_true, y_pred)
        TP += tp; TN += tn; FP += fp; FN += fn
        a, p, rc, f = scores(tp, tn, fp, fn)
        accs.append(a); precs.append(p); recs.append(rc); f1s.append(f)
        emit("seed %d | test n=%d | acc %.3f prec %.3f rec %.3f f1 %.3f"
             % (seed, len(y_true), a, p, rc, f))

    emit("")
    emit("==== RESULTS over %d seeds (mean +/- std) ====" % len(SEEDS))
    emit("accuracy : %.3f +/- %.3f" % (np.mean(accs), np.std(accs)))
    emit("precision: %.3f +/- %.3f" % (np.mean(precs), np.std(precs)))
    emit("recall   : %.3f +/- %.3f" % (np.mean(recs), np.std(recs)))
    emit("f1       : %.3f +/- %.3f" % (np.mean(f1s), np.std(f1s)))
    emit("")
    emit("Summed confusion matrix over all seeds/test-folds:")
    emit("                 pred_normal  pred_fault")
    emit("  true_normal    %11d %11d" % (TN, FP))
    emit("  true_fault     %11d %11d" % (FN, TP))

    os.makedirs(os.path.dirname(OUT_TXT), exist_ok=True)
    with open(OUT_TXT, 'w') as f:
        f.write("\n".join(lines) + "\n")
    emit("\nsaved -> %s" % OUT_TXT)

    # confusion-matrix figure for the paper
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        cm = np.array([[TN, FP], [FN, TP]])
        fig, ax = plt.subplots(figsize=(4, 3.5))
        ax.imshow(cm, cmap='Blues')
        ax.set_xticks([0, 1]); ax.set_xticklabels(['normal', 'fault'])
        ax.set_yticks([0, 1]); ax.set_yticklabels(['normal', 'fault'])
        ax.set_xlabel('Predicted'); ax.set_ylabel('True')
        ax.set_title('PPO agent — summed confusion (%d seeds)' % len(SEEDS))
        for i in range(2):
            for j in range(2):
                ax.text(j, i, str(cm[i, j]), ha='center', va='center',
                        color='black', fontsize=12)
        fig.tight_layout()
        fig.savefig(OUT_PNG, dpi=150)
        emit("saved -> %s" % OUT_PNG)
    except Exception as e:  # noqa
        emit("(figure skipped: %s)" % e)


if __name__ == '__main__':
    main()
