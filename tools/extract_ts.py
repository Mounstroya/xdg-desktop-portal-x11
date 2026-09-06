#!/usr/bin/env python3
"""Extrae el MPEG-TS crudo de los paquetes RTP capturados hacia el Roku."""
import sys
from scapy.all import rdpcap, UDP

pcap_path = sys.argv[1]
out_path = sys.argv[2]

packets = rdpcap(pcap_path)
print(f"Total paquetes en el pcap: {len(packets)}")

out = open(out_path, "wb")
count = 0
ports_seen = {}
for pkt in packets:
    if UDP not in pkt:
        continue
    payload = bytes(pkt[UDP].payload)
    dport = pkt[UDP].dport
    ports_seen[dport] = ports_seen.get(dport, 0) + 1
    if len(payload) < 12:
        continue
    # RTP header: version(2) P(1) X(1) CC(4) | M(1) PT(7) | seq(16) | ts(32) | ssrc(32)
    b0 = payload[0]
    version = b0 >> 6
    if version != 2:
        continue
    cc = b0 & 0x0F
    has_ext = (b0 >> 4) & 0x1
    header_len = 12 + cc * 4
    if has_ext:
        if len(payload) < header_len + 4:
            continue
        ext_len_words = int.from_bytes(payload[header_len+2:header_len+4], "big")
        header_len += 4 + ext_len_words * 4
    ts_payload = payload[header_len:]
    out.write(ts_payload)
    count += 1

out.close()
print(f"Paquetes RTP con datos escritos: {count}")
print(f"Puertos UDP destino vistos: {ports_seen}")
