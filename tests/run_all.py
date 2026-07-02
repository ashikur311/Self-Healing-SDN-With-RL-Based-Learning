#!/usr/bin/env python3
"""
S4.2 -- one-command offline test runner for the SHIELD framework.

Runs every offline unit suite (no Mininet/Ryu needed) and reports a green/red
summary; exit code is non-zero if any suite fails, so CI / `make test` can gate on
it. Live experiments (which need sudo + Mininet) are intentionally excluded and
listed in REPRODUCE.md with their exact CLIs and PASS gates.

Run:  python3 tests/run_all.py
"""
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))

# Offline unit suites, in dependency order (each prints "... PASS" and exits 0).
SUITES = [
    'test_k_paths.py',        # S2.1 candidate paths
    'test_resolver.py',       # S2.2 scoring/selection
    'test_baselines.py',      # S2.3 baselines
    'test_shield_core.py',    # S2.4 closed-loop brain
    'test_localizer.py',      # S1.6 fault localization
    'test_experience.py',     # S3.1 store + reward
    'test_learner.py',        # S3.2 learner loop
    'test_nonstationary.py',  # S3.3 non-stationary schedule
]

# Needs sudo + Mininet (or a live Ryu controller) -- run manually, see REPRODUCE.md.
LIVE_ONLY = ['s1_2_telemetry_live.py']


def main():
    print('==== SHIELD offline test suite (%d) ====' % len(SUITES))
    results = []
    t0 = time.time()
    for name in SUITES:
        path = os.path.join(HERE, name)
        if not os.path.exists(path):
            results.append((name, 'MISSING', 0.0))
            continue
        st = time.time()
        proc = subprocess.run([sys.executable, path], capture_output=True, text=True)
        dt = time.time() - st
        ok = proc.returncode == 0 and 'PASS' in proc.stdout
        results.append((name, 'PASS' if ok else 'FAIL', dt))
        print('  %-24s %s  (%.2fs)' % (name, 'PASS' if ok else 'FAIL', dt))
        if not ok:
            sys.stdout.write(proc.stdout[-800:])
            sys.stderr.write(proc.stderr[-800:])

    passed = sum(1 for _, s, _ in results if s == 'PASS')
    print('\n---- %d/%d suites PASS in %.1fs ----' % (passed, len(SUITES), time.time() - t0))
    print('live-only (run manually, see REPRODUCE.md): %s' % ', '.join(LIVE_ONLY))
    ok = passed == len(SUITES)
    print('*** OFFLINE TEST SUITE: %s' % ('GREEN' if ok else 'RED'))
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
