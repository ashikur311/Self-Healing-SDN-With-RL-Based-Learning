#!/usr/bin/env python3
"""
SHIELD fault detector with localization (step S1.6).

Per-port classification: each telemetry row (one switch port at one poll) is
classified fault / normal from [tput_n, loss_n, delay_n]. Because rows are
per-port, a positive prediction IS the localization -- the flagged (dpid,port)
is the failed element. So one model does BOTH "is there a fault" and "where".

Classifiers are pure NumPy (no scikit-learn on this VM), mirroring the paper's
baseline protocol (compare_methods.py): the lightweight LR / k-NN beat PPO there,
so they are the default here. Evaluation protocol (stratified split BEFORE
oversampling, oversample TRAIN only, mean±std over seeds) matches evaluate_agent.py
and is applied rigorously in the S1.7 experiment; this file also runs a quick
single-split self-check.

Run (venv):  /home/mininet/venvs/ryu-4.34-py39/bin/python framework/agents/detector.py
"""
import os
import sys

import numpy as np
import pandas as pd

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
DATASET = os.path.join(ROOT, 'data', 'datasets', 'telemetry_v2.csv')
# drop_ratio (recent-peak-relative throughput fall) is the feature that separates
# a failed active element from a merely idle one; see S1.6 notes.
FEATURES = ['tput_n', 'loss_n', 'delay_n', 'drop_ratio']


# --- evaluation protocol helpers (mirror evaluate_agent.py, SB3-free) ---------
def stratified_split(df, test_frac, seed):
    rng = np.random.RandomState(seed)
    tr, te = [], []
    for _cls, g in df.groupby('is_fault'):
        idx = rng.permutation(len(g))
        n_te = int(round(len(g) * test_frac))
        te.append(g.iloc[idx[:n_te]])
        tr.append(g.iloc[idx[n_te:]])
    return (pd.concat(tr).sample(frac=1, random_state=seed).reset_index(drop=True),
            pd.concat(te).sample(frac=1, random_state=seed).reset_index(drop=True))


def oversample_train(train, seed):
    """Balance is_fault in the TRAIN split only (never touch test)."""
    fault = train[train['is_fault'] == 1]
    normal = train[train['is_fault'] == 0]
    if len(fault) == 0 or len(normal) == 0:
        return train
    if len(fault) < len(normal):
        fault = fault.sample(len(normal), replace=True, random_state=seed)
    else:
        normal = normal.sample(len(fault), replace=True, random_state=seed)
    return pd.concat([fault, normal]).sample(frac=1, random_state=seed).reset_index(drop=True)


def confusion(y_true, y_pred):
    tp = int(np.sum((y_true == 1) & (y_pred == 1)))
    tn = int(np.sum((y_true == 0) & (y_pred == 0)))
    fp = int(np.sum((y_true == 0) & (y_pred == 1)))
    fn = int(np.sum((y_true == 1) & (y_pred == 0)))
    return tp, tn, fp, fn


def scores(tp, tn, fp, fn):
    acc = (tp + tn) / max(tp + tn + fp + fn, 1)
    prec = tp / max(tp + fp, 1)
    rec = tp / max(tp + fn, 1)
    f1 = 2 * prec * rec / max(prec + rec, 1e-9)
    return acc, prec, rec, f1


# --- classifiers --------------------------------------------------------------
class Detector:
    def __init__(self, method='knn', k=5, epochs=800, lr=0.5):
        self.method = method
        self.k = k
        self.epochs = epochs
        self.lr = lr
        self._w = None
        self._Xtr = self._ytr = None

    def fit(self, X, y):
        X = np.asarray(X, dtype=np.float64)
        y = np.asarray(y, dtype=int)
        if self.method == 'lr':
            X1 = np.hstack([X, np.ones((len(X), 1))])
            w = np.zeros(X1.shape[1])
            for _ in range(self.epochs):
                p = 1.0 / (1.0 + np.exp(-np.clip(X1 @ w, -30, 30)))
                w -= self.lr * (X1.T @ (p - y) / len(y))
            self._w = w
        else:  # knn (default) — store training set
            self._Xtr, self._ytr = X, y
        return self

    def predict(self, X):
        X = np.asarray(X, dtype=np.float64)
        if self.method == 'loss':
            return (X[:, 1] >= 0.5).astype(int)      # loss-threshold baseline
        if self.method == 'lr':
            X1 = np.hstack([X, np.ones((len(X), 1))])
            p = 1.0 / (1.0 + np.exp(-np.clip(X1 @ self._w, -30, 30)))
            return (p >= 0.5).astype(int)
        preds = np.empty(len(X), dtype=int)          # knn
        for i, x in enumerate(X):
            d = np.sum((self._Xtr - x) ** 2, axis=1)
            idx = np.argpartition(d, self.k)[:self.k]
            preds[i] = int(round(self._ytr[idx].mean()))
        return preds

    # convenience wrappers on DataFrames
    def fit_df(self, df):
        return self.fit(df[FEATURES].to_numpy(), df['is_fault'].to_numpy())

    def predict_df(self, df):
        return self.predict(df[FEATURES].to_numpy())


def localize(test_df, preds):
    """Return the (topology,dpid,port) rows the detector flagged as failed."""
    flagged = test_df[np.asarray(preds) == 1]
    return list(zip(flagged['topology'], flagged['dpid'], flagged['port']))


def _self_check():
    df = pd.read_csv(DATASET)
    train, test = stratified_split(df, 0.30, seed=0)
    train_bal = oversample_train(train, seed=0)
    yte = test['is_fault'].to_numpy()
    print('rows: train %d (bal %d) / test %d | test faults %d'
          % (len(train), len(train_bal), len(test), int(yte.sum())))
    print('%-8s %6s %6s %6s %6s' % ('method', 'acc', 'prec', 'rec', 'f1'))
    for m in ('loss', 'lr', 'knn'):
        det = Detector(method=m).fit_df(train_bal)
        yp = det.predict_df(test)
        a, p, r, f = scores(*confusion(yte, yp))
        print('%-8s %6.3f %6.3f %6.3f %6.3f' % (m, a, p, r, f))
    # localization demo with the default detector
    det = Detector('knn').fit_df(train_bal)
    yp = det.predict_df(test)
    flagged = localize(test, yp)
    print('localized fault ports (first 8):', flagged[:8])


if __name__ == '__main__':
    _self_check()
