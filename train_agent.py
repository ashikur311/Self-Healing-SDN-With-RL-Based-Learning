import pandas as pd
import numpy as np
import gymnasium as gym
from gymnasium import spaces
from stable_baselines3 import PPO
import os

print("--- STEP 1: CLEANING THE DATASET ---")
csv_path = '/home/mininet/self_healing_sdn/dataset/network_data.csv'
df = pd.read_csv(csv_path)

# THE FIX: If throughput is 0, the link is dead. Force packet loss to 100% (1.0).
df.loc[df['throughput_kbps'] == 0.0, 'packet_loss_rate'] = 1.0
df.loc[df['throughput_kbps'] == 0.0, 'congestion_label'] = 'LINK_DOWN'

class SDNSimulationEnv(gym.Env):
    def __init__(self, dataframe):
        super(SDNSimulationEnv, self).__init__()

        # NOTE (fix #2): this trains the DEPLOYED model on all available data.
        # Reported paper metrics must come from evaluate_agent.py, which splits
        # train/test BEFORE oversampling (no leakage) and reports held-out
        # accuracy/precision/recall/F1 over multiple seeds. Do NOT quote the
        # sanity check at the bottom of this file as an evaluation.

        # OVERSAMPLING: Balance the dataset 50/50 so the AI cannot cheat
        faults = dataframe[(dataframe['congestion_label'] == 'LINK_DOWN') | (dataframe['congestion_label'] == 'HIGH')]
        normals = dataframe[(dataframe['congestion_label'] == 'LOW') | (dataframe['congestion_label'] == 'MEDIUM')]
        
        if len(faults) > 0:
            faults_oversampled = faults.sample(n=len(normals), replace=True)
            self.df = pd.concat([normals, faults_oversampled]).sample(frac=1).reset_index(drop=True)
        else:
            self.df = dataframe

        self.current_step = 0
        self.action_space = spaces.Discrete(2)   # 0 = do nothing, 1 = reroute

        self.observation_space = spaces.Box(low=0, high=1.0, shape=(3,), dtype=np.float32)

    def reset(self, seed=None, options=None):
        super().reset(seed=seed)
        self.current_step = 0
        return self._get_observation(), {}

    def _get_observation(self):
        row = self.df.iloc[self.current_step]
        norm_throughput = min(row['throughput_kbps'] / 25000.0, 1.0) 
        norm_loss = min(row['packet_loss_rate'], 1.0)
        norm_delay = min(row['delay_ms'] / 100.0, 1.0)
        return np.array([norm_throughput, norm_loss, norm_delay], dtype=np.float32)

    def step(self, action):
        row = self.df.iloc[self.current_step]
        is_fault = (row['congestion_label'] == 'LINK_DOWN') or (row['congestion_label'] == 'HIGH')
        
        # Perfect Symmetrical Rewards
        reward = 0
        if is_fault:
            if action == 1: reward = 10  # Correct Action!
            else: reward = -10           # Wrong Action!
        else:
            if action == 0: reward = 10  # Correct Action!
            else: reward = -10           # Wrong Action!
                
        self.current_step += 1
        done = self.current_step >= len(self.df) - 1
        return self._get_observation(), reward, done, False, {}

print("--- STEP 2: TRAINING THE AI BRAIN ---")
env = SDNSimulationEnv(df)
model = PPO("MlpPolicy", env, verbose=0, learning_rate=0.001)
model.learn(total_timesteps=20000) 
model.save("/home/mininet/self_healing_sdn/sdn_recovery_agent")
print("AI Brain Trained and Saved!\n")

print("--- STEP 3: QUICK SANITY CHECK (NOT an evaluation) ---")
print("    For real, held-out metrics run:  python evaluate_agent.py")
actions = {0: "Do Nothing (Maintain Path)", 1: "Reroute Traffic (Recovery)"}
scenarios = {
    "NORMAL TRAFFIC": np.array([1500.0 / 25000.0, 0.0, 1.5 / 100.0], dtype=np.float32),
    "MEDIUM CONGESTION": np.array([4500.0 / 25000.0, 0.02, 5.0 / 100.0], dtype=np.float32),
    "SEVERE FAULT (Link Down)": np.array([0.0 / 25000.0, 1.0, 15.0 / 100.0], dtype=np.float32)
}

for name, state in scenarios.items():
    action, _ = model.predict(state, deterministic=True)
    print(f"Network State: {name}")
    print(f"Agent Action: --> {actions[int(action)]} <--\n")