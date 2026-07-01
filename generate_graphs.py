import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns
import os

# 1. Load the data
csv_path = '/home/mininet/self_healing_sdn/dataset/network_data.csv'
if not os.path.exists(csv_path):
    print("Error: CSV not found!")
    exit()

df = pd.read_csv(csv_path)

# Ensure data is sorted by timestamp just in case
df = df.sort_values(by='timestamp').reset_index(drop=True)

# Focus on Switch 1, Port 2 (The link that breaks)
df_s1p2 = df[(df['datapath_id'] == 1) & (df['port_no'] == 2)].copy()

# Fix the zero-packet bug for the graph
df_s1p2.loc[df_s1p2['throughput_kbps'] == 0.0, 'packet_loss_rate'] = 1.0

# Create the time axis (Start from 0 seconds)
df_s1p2['Time_Seconds'] = df_s1p2['timestamp'] - df_s1p2['timestamp'].min()

# 2. Plotting the Graph
sns.set_theme(style="whitegrid")
fig, ax1 = plt.subplots(figsize=(12, 6))

# Plot Throughput on the left Y-axis
color = 'tab:blue'
ax1.set_xlabel('Time (Seconds)', fontsize=12, fontweight='bold')
ax1.set_ylabel('Throughput (kbps)', color=color, fontsize=12, fontweight='bold')
ax1.plot(df_s1p2['Time_Seconds'], df_s1p2['throughput_kbps'], color=color, linewidth=2, label='Throughput')
ax1.tick_params(axis='y', labelcolor=color)
ax1.fill_between(df_s1p2['Time_Seconds'], df_s1p2['throughput_kbps'], alpha=0.1, color=color)

# Create a right Y-axis for Packet Loss
ax2 = ax1.twinx()  
color = 'tab:red'
ax2.set_ylabel('Packet Loss Rate (0.0 to 1.0)', color=color, fontsize=12, fontweight='bold')
ax2.plot(df_s1p2['Time_Seconds'], df_s1p2['packet_loss_rate'], color=color, linewidth=2, linestyle='--', label='Packet Loss')
ax2.tick_params(axis='y', labelcolor=color)

# Add Phase Annotations (Adjust time values based on your typical run)
plt.axvline(x=5, color='gray', linestyle=':', alpha=0.7)
plt.text(6, 0.8, 'Medium Traffic', rotation=90, color='gray')

plt.axvline(x=20, color='orange', linestyle=':', alpha=0.7)
plt.text(21, 0.8, 'High Traffic', rotation=90, color='orange')

plt.axvline(x=38, color='red', linestyle=':', alpha=0.7)
plt.text(39, 0.8, 'LINK FAILURE', rotation=90, color='red', fontweight='bold')

plt.title('Network Telemetry: Throughput vs Packet Loss over Time (Switch 1, Port 2)', fontsize=14, fontweight='bold')
fig.tight_layout()  

# Save the image for your paper
plt.savefig('/home/mininet/self_healing_sdn/Telemetry_Graph.png', dpi=300)
print("Graph saved as 'Telemetry_Graph.png'! You can now insert this into your Word document.")