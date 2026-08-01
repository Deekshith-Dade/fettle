"""Probe whether this execution context can send UDP to the LAN + multicast.
Writes results to netprobe_result.txt next to this file.
"""
import json
import socket
from pathlib import Path

OUT = Path(__file__).parent / "netprobe_result.txt"
results = {}

s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
try:
    s.sendto(b"probe", ("192.168.0.244", 5540))
    results["unicast_udp_lan"] = "OK"
except OSError as e:
    results["unicast_udp_lan"] = f"FAIL errno={e.errno} {e.strerror}"

m = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
try:
    m.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
    m.sendto(b"probe", ("224.0.0.251", 5353))
    results["multicast_udp"] = "OK"
except OSError as e:
    results["multicast_udp"] = f"FAIL errno={e.errno} {e.strerror}"

OUT.write_text(json.dumps(results) + "\n")
