#!/usr/bin/env python3
"""
Experiment orchestration script.

Per-round steps:
  1. Cleanup previous state via launch_nodes.py -c
  2. Start launch_nodes.py -t {simTime+20} in the background
  3. Wait launch_wait seconds for containers/TAP devices to initialize
  4. Run: sudo nice -n 20 taskset -c 0-10 ./ns3 run "nr-mec-3gpp-calibration ..."
  5. Wait for ns-3 to finish, then wait for launch_nodes.py to self-terminate
  6. Pause round_pause seconds, then start the next round
"""
import argparse
import os
import signal
import subprocess
import sys
import time

import json5

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LAUNCH_SCRIPT = os.path.join(SCRIPT_DIR, 'launch_nodes.py')
NS3_DIR = os.path.expanduser('~/dev/ns-3-dev')

# Kept at module level so the signal handler can reach them
_launch_proc = None
_ns3_proc = None


def _signal_handler(sig, frame):
    print(f"\nReceived signal {sig}, cleaning up...")
    if _ns3_proc and _ns3_proc.poll() is None:
        _ns3_proc.terminate()
    if _launch_proc and _launch_proc.poll() is None:
        _launch_proc.terminate()
    subprocess.run([sys.executable, LAUNCH_SCRIPT, '-c'], cwd=SCRIPT_DIR)
    sys.exit(0)


def _build_ns3_args(ns3_config: dict) -> str:
    """Convert config dict to a flat argument string for the ns3 run command."""
    parts = []
    for key, value in ns3_config.items():
        if isinstance(value, bool):
            parts.append(f'--{key}={str(value).lower()}')
        else:
            parts.append(f'--{key}={value}')
    return ' '.join(parts)


def run_round(round_num: int, config: dict) -> bool:
    global _launch_proc, _ns3_proc

    ns3_config = config['ns3']
    ns3_dir = config.get('ns3_dir', NS3_DIR)
    launch_wait = config.get('launch_wait', 12)
    sim_time = ns3_config['simTime']
    auto_terminate = int(sim_time) + 20

    print(f"\n{'='*50}")
    print(f"Round {round_num}: cleanup")
    print(f"{'='*50}")
    subprocess.run([sys.executable, LAUNCH_SCRIPT, '-c'], cwd=SCRIPT_DIR, check=True)

    print(f"\nRound {round_num}: launching nodes (auto-terminate in {auto_terminate}s)...")
    _launch_proc = subprocess.Popen(
        [sys.executable, LAUNCH_SCRIPT, '-t', str(auto_terminate)],
        cwd=SCRIPT_DIR,
    )

    print(f"Round {round_num}: waiting {launch_wait}s for containers to initialize...")
    time.sleep(launch_wait)

    if _launch_proc.poll() is not None:
        print(f"Round {round_num}: ERROR — launch_nodes.py exited early (code {_launch_proc.returncode})")
        return False

    ns3_args = _build_ns3_args(ns3_config)
    ns3_cmd = f'sudo nice -n 20 taskset -c 0-10 ./ns3 run "nr-mec-3gpp-calibration {ns3_args}"'
    print(f"\nRound {round_num}: starting ns-3")
    print(f"  {ns3_cmd}\n")
    _ns3_proc = subprocess.Popen(ns3_cmd, shell=True, cwd=ns3_dir)

    ns3_exit = _ns3_proc.wait()
    if ns3_exit != 0:
        print(f"Round {round_num}: WARNING — ns-3 exited with code {ns3_exit}")

    print(f"Round {round_num}: waiting for launch_nodes.py to terminate...")
    _launch_proc.wait()

    print(f"Round {round_num}: done.")
    return True


def main():
    parser = argparse.ArgumentParser(
        description='Run N rounds of Zenoh + ns-3 experiment'
    )
    parser.add_argument(
        '-c', '--config',
        default=os.path.join(SCRIPT_DIR, 'EXPERIMENT_CONFIG.json5'),
        help='Path to experiment config file (default: EXPERIMENT_CONFIG.json5)',
    )
    parser.add_argument(
        '--rounds', '--round', type=int, default=None,
        help='Override number of rounds from config',
    )
    args = parser.parse_args()

    with open(args.config) as f:
        config = json5.load(f)

    rounds = args.rounds if args.rounds is not None else config.get('rounds', 1)
    round_pause = config.get('round_pause', 5)

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    # Cache sudo credentials upfront so background subprocesses in launch_nodes.py
    # (ip tuntap, ip link, iptables, ...) don't block waiting for a password prompt
    # print("Caching sudo credentials...")
    # subprocess.run(['sudo', '-v'], check=True)

    print(f"Starting experiment: {rounds} round(s)")
    print(f"  simTime={config['ns3']['simTime']}s, launch_wait={config.get('launch_wait', 12)}s")

    for i in range(1, rounds + 1):
        success = run_round(i, config)
        if not success:
            print(f"Round {i} failed — aborting.")
            sys.exit(1)
        if i < rounds:
            print(f"\nWaiting {round_pause}s before round {i + 1}...")
            time.sleep(round_pause)

    print(f"\nAll {rounds} round(s) complete.")


if __name__ == '__main__':
    main()
