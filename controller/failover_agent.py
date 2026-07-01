#!/usr/bin/env python3
"""
OpenFlow fast-failover baseline controller (Paper reviewer fix M1).

This is the NETWORKING baseline the self-healing reroute must beat: instead of
detecting a fault in the controller and reactively pushing OFPFlowMod rules
(controller/reroute_agent.py), this controller proactively installs an OpenFlow
1.3 fast-failover GROUP (OFPGT_FF) at s1 and s3. The switch itself watches the
liveness of the primary output port and, the instant that port goes down, moves
traffic to the backup bucket -- entirely in the data plane, with NO controller
round-trip.

Pairs with the same triangle topology + fixed ports + static ARP used by
reroute_agent.py, so recovery time is directly comparable.

    PRIMARY  bucket: watch = port to s2, output = port to s2   (h1<->h3 via s2)
    BACKUP   bucket: watch = port to s3/s1 (direct), output = that direct port

IMPORTANT (and the interesting finding for the paper): a fast-failover group only
reacts to the failure of a port on THIS switch. On the triangle, a single mid-path
link failure (s2-s3 only) is local to s3 but NOT to s1, so s1 keeps sending toward
s2 and the forward path stays broken -- pure fast-failover cannot recover a
downstream link failure. It DOES fully recover a node failure of s2 (both primary
links go down, so both s1 and s3 fail over locally). The controller-based reroute,
having a global view, recovers the single-link case too. This is the trade-off:
fast-failover is faster but only for locally-observable faults.

Run:  ryu-manager controller/failover_agent.py
"""
from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER
from ryu.controller.handler import set_ev_cls
from ryu.ofproto import ofproto_v1_3

H1_MAC = '00:00:00:00:00:01'
H3_MAC = '00:00:00:00:00:03'

# Port map (must match reroute_topo.py / measure_recovery.py)
S1, S2, S3 = 1, 2, 3
S1_TO_H1, S1_TO_S2, S1_TO_S3 = 1, 2, 3
S2_TO_S1, S2_TO_S3 = 1, 2
S3_TO_H3, S3_TO_S2, S3_TO_S1 = 1, 2, 3

PRIO = 10
FF_GROUP = 1


class FastFailover(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super(FastFailover, self).__init__(*args, **kwargs)

    # ---- helpers -----------------------------------------------------------
    def _ff_group(self, dp, primary_port, backup_port):
        """Install a fast-failover group: bucket 1 watches/uses the primary
        port, bucket 2 watches/uses the backup port. The switch selects the
        first bucket whose watch_port is live."""
        parser = dp.ofproto_parser
        ofp = dp.ofproto
        buckets = [
            parser.OFPBucket(watch_port=primary_port, watch_group=ofp.OFPG_ANY,
                             actions=[parser.OFPActionOutput(primary_port)]),
            parser.OFPBucket(watch_port=backup_port, watch_group=ofp.OFPG_ANY,
                             actions=[parser.OFPActionOutput(backup_port)]),
        ]
        dp.send_msg(parser.OFPGroupMod(dp, ofp.OFPGC_ADD, ofp.OFPGT_FF,
                                       FF_GROUP, buckets))

    def _flow_to_group(self, dp, eth_dst, group_id):
        parser = dp.ofproto_parser
        match = parser.OFPMatch(eth_dst=eth_dst)
        actions = [parser.OFPActionGroup(group_id)]
        inst = [parser.OFPInstructionActions(dp.ofproto.OFPIT_APPLY_ACTIONS, actions)]
        dp.send_msg(parser.OFPFlowMod(datapath=dp, priority=PRIO, match=match,
                                      instructions=inst))

    def _flow_to_port(self, dp, eth_dst, out_port):
        parser = dp.ofproto_parser
        match = parser.OFPMatch(eth_dst=eth_dst)
        actions = [parser.OFPActionOutput(out_port)]
        inst = [parser.OFPInstructionActions(dp.ofproto.OFPIT_APPLY_ACTIONS, actions)]
        dp.send_msg(parser.OFPFlowMod(datapath=dp, priority=PRIO, match=match,
                                      instructions=inst))

    # ---- events ------------------------------------------------------------
    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def _switch_features_handler(self, ev):
        dp = ev.msg.datapath
        did = dp.id
        if did == S1:
            # to h3: fast-failover (primary via s2, backup via direct s1-s3)
            self._ff_group(dp, S1_TO_S2, S1_TO_S3)
            self._flow_to_group(dp, H3_MAC, FF_GROUP)
            self._flow_to_port(dp, H1_MAC, S1_TO_H1)          # to h1: local
        elif did == S2:
            # s2 just forwards along the primary path (no backup here)
            self._flow_to_port(dp, H3_MAC, S2_TO_S3)
            self._flow_to_port(dp, H1_MAC, S2_TO_S1)
        elif did == S3:
            # to h1: fast-failover (primary via s2, backup via direct s3-s1)
            self._ff_group(dp, S3_TO_S2, S3_TO_S1)
            self._flow_to_group(dp, H1_MAC, FF_GROUP)
            self._flow_to_port(dp, H3_MAC, S3_TO_H3)          # to h3: local
        self.logger.warning("[FF-INIT] s%s: fast-failover group + flows installed", did)
