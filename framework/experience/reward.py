#!/usr/bin/env python3
"""
SHIELD recovery reward (step S3.1).

Turns a measured recovery OUTCOME into a scalar the learner (S3.2) maximises when
it updates the Resolver day-by-day. Per the plan:

    r = beta * throughput  -  alpha * post_loss  -  recovery_time

Sign convention: higher is better. Throughput (Mb/s) is the payoff; post-recovery
loss (percent) and the time spent recovering (seconds) are costs. alpha/beta are
fixed a priori so rewards are comparable across days; they are the ONLY tunables
and are documented here so every stored reward traces to this formula.

Outcome dict keys (all optional, default 0.0):
    throughput     -- post-recovery goodput, Mb/s
    post_loss      -- post-recovery packet loss, percent (0..100)
    recovery_time  -- seconds from fault to restored service
"""

# Cost weights (a priori, documented). throughput dominates; loss penalised per %.
REWARD_ALPHA = 0.5      # penalty per percent post-recovery loss
REWARD_BETA = 1.0       # payoff per Mb/s throughput


def compute_reward(outcome, alpha=REWARD_ALPHA, beta=REWARD_BETA):
    """Scalar reward from a measured recovery outcome (higher = better)."""
    tput = float(outcome.get('throughput', 0.0))
    loss = float(outcome.get('post_loss', 0.0))
    rt = float(outcome.get('recovery_time', 0.0))
    return beta * tput - alpha * loss - rt


if __name__ == '__main__':
    demo = {'throughput': 17.4, 'post_loss': 0.0, 'recovery_time': 0.25}
    print("reward(good C recovery) =", compute_reward(demo))
    bad = {'throughput': 0.8, 'post_loss': 50.0, 'recovery_time': 0.25}
    print("reward(bad B recovery)  =", compute_reward(bad))
