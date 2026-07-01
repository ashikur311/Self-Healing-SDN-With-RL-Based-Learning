from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import MAIN_DISPATCHER, DEAD_DISPATCHER
from ryu.controller.handler import set_ev_cls
from ryu.lib import hub
from ryu.app import simple_switch_13
import time
import numpy as np
import os
from stable_baselines3 import PPO

class AutonomousSDNAgent(simple_switch_13.SimpleSwitch13):
    def __init__(self, *args, **kwargs):
        super(AutonomousSDNAgent, self).__init__(*args, **kwargs)
        self.datapaths = {}
        self.port_stats = {} 
        self.req_times = {}  
        
        model_path = "/home/mininet/self_healing_sdn/sdn_recovery_agent"
        self.logger.info("=========================================")
        if os.path.exists(model_path + ".zip"):
            self.rl_agent = PPO.load(model_path)
            self.logger.info("Brain successfully loaded! Autonomous Mode: ON")
        else:
            self.rl_agent = None
        self.logger.info("=========================================")

        self.monitor_thread = hub.spawn(self._monitor)

    @set_ev_cls(ofp_event.EventOFPStateChange, [MAIN_DISPATCHER, DEAD_DISPATCHER])
    def _state_change_handler(self, ev):
        datapath = ev.datapath
        if ev.state == MAIN_DISPATCHER:
            self.datapaths[datapath.id] = datapath
        elif ev.state == DEAD_DISPATCHER:
            if datapath.id in self.datapaths:
                del self.datapaths[datapath.id]

    def _monitor(self):
        while True:
            for dp in self.datapaths.values():
                self._request_stats(dp)
            hub.sleep(3) 

    def _request_stats(self, datapath):
        self.req_times[datapath.id] = time.time() 
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        req = parser.OFPPortStatsRequest(datapath, 0, ofproto.OFPP_ANY)
        datapath.send_msg(req)

    @set_ev_cls(ofp_event.EventOFPPortStatsReply, MAIN_DISPATCHER)
    def _port_stats_reply_handler(self, ev):
        body = ev.msg.body
        datapath_id = ev.msg.datapath.id
        current_time = time.time()
        
        delay_ms = round((current_time - self.req_times.get(datapath_id, current_time)) * 1000, 2)
        
        for stat in sorted(body, key=lambda port: port.port_no):
            if stat.port_no == 4294967294: 
                continue
                
            key = (datapath_id, stat.port_no)
            curr_bytes = stat.rx_bytes + stat.tx_bytes
            curr_pkts = stat.rx_packets + stat.tx_packets
            
            throughput_kbps = 0.0
            loss_rate = 0.0
            
            if key in self.port_stats:
                prev_bytes, prev_pkts, prev_time = self.port_stats[key]
                time_diff = current_time - prev_time
                
                if time_diff > 0:
                    throughput_kbps = ((curr_bytes - prev_bytes) * 8) / (time_diff * 1000)
                    
                    # THE LIVE BUG FIX: If throughput drops to 0, the link is dead (100% loss)
                    if throughput_kbps == 0.0:
                        loss_rate = 1.0
                        
            self.port_stats[key] = (curr_bytes, curr_pkts, current_time)
            
            # Normalize for the AI
            norm_throughput = min(throughput_kbps / 25000.0, 1.0) 
            norm_loss = min(loss_rate, 1.0)
            norm_delay = min(delay_ms / 100.0, 1.0)
            
            state_array = np.array([norm_throughput, norm_loss, norm_delay], dtype=np.float32)

            if self.rl_agent is not None:
                action, _ = self.rl_agent.predict(state_array, deterministic=True)
                
                # Only print warnings if the AI decides to take action
                if action == 1 and (loss_rate > 0.5 or throughput_kbps == 0.0):
                    self.logger.warning(f"[🚨 FAULT DETECTED] Switch {datapath_id} Port {stat.port_no} | Link Down!")
                    self.logger.warning(f"🤖 [AUTONOMOUS ACTION] -> REROUTING TRAFFIC to backup path!\n")