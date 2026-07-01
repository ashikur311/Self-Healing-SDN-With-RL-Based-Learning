import numpy as np
from stable_baselines3 import PPO # Upgraded to PPO here as well!

print("Loading the PPO Agent 2...")
model = PPO.load("/home/mininet/self_healing_sdn/sdn_recovery_agent")

actions = {0: "Do Nothing (Maintain Path)", 1: "Reroute Traffic (Recovery)", 2: "Block User"}

scenarios = {
    "NORMAL TRAFFIC": np.array([1500.0 / 25000.0, 0.0, 1.5 / 100.0], dtype=np.float32),
    "MEDIUM CONGESTION": np.array([4500.0 / 25000.0, 0.02, 5.0 / 100.0], dtype=np.float32),
    "SEVERE FAULT (Link Down)": np.array([0.0 / 25000.0, 1.0, 15.0 / 100.0], dtype=np.float32)
}

print("\n--- Testing Agent 2 Decisions ---")
for scenario_name, state in scenarios.items():
    action, _states = model.predict(state, deterministic=True)
    
    if scenario_name == "NORMAL TRAFFIC": raw = "[1500.0, 0.0, 1.5]"
    elif scenario_name == "MEDIUM CONGESTION": raw = "[4500.0, 0.02, 5.0]"
    else: raw = "[0.0, 1.0, 15.0]"
    
    print(f"Network State: {scenario_name}")
    print(f"Metrics: {raw}")
    print(f"Agent Action: --> {actions[int(action)]} <--\n")