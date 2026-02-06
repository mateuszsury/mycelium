"""
Example: Using µMesh optimized hot-path functions

This demonstrates how to use the performance-critical functions
from umesh._fast in a realistic packet processing scenario.
"""

try:
    import utime as time
    from array import array
except ImportError:
    import time
    from array import array

import sys
import os
# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Import directly from _fast to avoid adapter dependencies
if os.path.exists(os.path.join(os.path.dirname(os.path.dirname(__file__)), 'umesh', '_fast.py')):
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), 'umesh'))
    from _fast import (
        _parse_header,
        _build_header,
        _dup_check,
        _rssi_ewma,
        _calc_lqi,
        _route_lookup,
        _accumulate_metric,
        VIPER_AVAILABLE,
    )
else:
    from umesh._fast import (
    # Viper functions
    _parse_header,
    _build_header,
    _dup_check,
    _rssi_ewma,
    # Native functions
    _calc_lqi,
    _route_lookup,
    _accumulate_metric,
    # Info
    VIPER_AVAILABLE,
)


class PacketProcessor:
    """Example packet processor using optimized functions."""

    def __init__(self, dup_buffer_size=256):
        """Initialize with pre-allocated buffers."""
        # Pre-allocate buffers (zero-allocation hot path)
        self.parsed = array('i', [0] * 10)
        self.header_buf = bytearray(14)
        self.dup_buffer = bytearray(dup_buffer_size)
        self.dup_index = 0

        # Mock neighbor and routing tables
        self.neighbors = {}
        self.routing_table = {}

        print(f"PacketProcessor initialized (viper={VIPER_AVAILABLE})")
        print(f"  Duplicate buffer: {dup_buffer_size} bytes ({dup_buffer_size//4} entries)")

    def process_incoming(self, raw_packet, mac):
        """Process an incoming packet (hot path)."""

        # 1. Parse header with inline CRC check (VIPER: ~4µs)
        if not _parse_header(raw_packet, self.parsed):
            print("  [REJECT] CRC mismatch")
            return None

        # Extract parsed fields
        version = self.parsed[0]
        pkt_type = self.parsed[1]
        src_id = self.parsed[6]
        dest_id = self.parsed[7]
        pkt_id = self.parsed[3]
        ttl = self.parsed[4]

        print(f"  [PARSED] v{version} type={pkt_type} {src_id}->{dest_id} id={pkt_id} ttl={ttl}")

        # 2. Duplicate check (VIPER: ~5µs)
        if _dup_check(self.dup_buffer, len(self.dup_buffer), src_id, pkt_id):
            print("  [REJECT] Duplicate packet")
            return None

        # 3. Add to duplicate buffer
        self._add_to_dup_buffer(src_id, pkt_id)

        # 4. Update neighbor RSSI if known (VIPER: ~2µs)
        self._update_neighbor_rssi(mac, src_id)

        # 5. Calculate link quality (NATIVE: ~10µs)
        if mac in self.neighbors:
            lqi = self._get_link_quality(mac)
            print(f"  [LQI] Link to {mac.hex()}: {lqi}")

        return {
            'src_id': src_id,
            'dest_id': dest_id,
            'pkt_id': pkt_id,
            'ttl': ttl,
            'payload': raw_packet[14:]
        }

    def build_outgoing(self, pkt_type, dest_id, src_id, pkt_id, ttl, payload=b''):
        """Build an outgoing packet (VIPER: ~3µs)."""

        # Build header with automatic CRC
        _build_header(
            self.header_buf,
            version=1,
            pkt_type=pkt_type,
            flags=0,
            pkt_id=pkt_id,
            ttl=ttl,
            hop_count=0,
            src_id=src_id,
            dest_id=dest_id,
            frag_info=0
        )

        # Combine header + payload
        return bytes(self.header_buf) + payload

    def forward_packet(self, dest_id):
        """Look up route for forwarding (NATIVE)."""
        now_ms = time.ticks_ms() if hasattr(time, 'ticks_ms') else int(time.time() * 1000)

        route = _route_lookup(self.routing_table, dest_id, now_ms)
        if route:
            next_hop_id, next_hop_mac, metric = route
            print(f"  [FORWARD] Route to {dest_id} via {next_hop_id} (metric={metric})")
            return next_hop_mac
        else:
            print(f"  [FORWARD] No route to {dest_id}")
            return None

    def _add_to_dup_buffer(self, src_id, pkt_id):
        """Add (src_id, pkt_id) to circular duplicate buffer."""
        self.dup_buffer[self.dup_index] = (src_id >> 8) & 0xFF
        self.dup_buffer[self.dup_index + 1] = src_id & 0xFF
        self.dup_buffer[self.dup_index + 2] = (pkt_id >> 8) & 0xFF
        self.dup_buffer[self.dup_index + 3] = pkt_id & 0xFF
        self.dup_index = (self.dup_index + 4) % len(self.dup_buffer)

    def _update_neighbor_rssi(self, mac, src_id):
        """Update neighbor RSSI using EWMA."""
        if mac not in self.neighbors:
            # New neighbor - initialize
            self.neighbors[mac] = {
                'node_id': src_id,
                'rssi': 200,  # -56 dBm as uint8
                'loss_pct': 0,
                'last_seen': time.ticks_ms() if hasattr(time, 'ticks_ms') else int(time.time() * 1000),
            }
        else:
            # Update RSSI with EWMA (alpha = 3/8 = 0.375)
            old_rssi = self.neighbors[mac]['rssi']
            new_rssi = 190  # Simulated: -66 dBm
            self.neighbors[mac]['rssi'] = _rssi_ewma(old_rssi, new_rssi, 3, 8)
            self.neighbors[mac]['last_seen'] = time.ticks_ms() if hasattr(time, 'ticks_ms') else int(time.time() * 1000)

    def _get_link_quality(self, mac):
        """Calculate Link Quality Index for a neighbor."""
        neighbor = self.neighbors[mac]
        now_ms = time.ticks_ms() if hasattr(time, 'ticks_ms') else int(time.time() * 1000)

        # Convert uint8 RSSI back to signed
        rssi_u8 = neighbor['rssi']
        rssi_signed = rssi_u8 - 256 if rssi_u8 > 127 else rssi_u8

        lqi = _calc_lqi(
            rssi_avg=rssi_signed,
            loss_pct=neighbor['loss_pct'],
            age_ms=now_ms - neighbor['last_seen'],
            timeout_ms=30000
        )
        return lqi

    def add_route(self, dest_id, next_hop_id, next_hop_mac, metric, ttl_ms=60000):
        """Add a route to the routing table."""
        now_ms = time.ticks_ms() if hasattr(time, 'ticks_ms') else int(time.time() * 1000)
        expiry_ms = now_ms + ttl_ms

        self.routing_table[dest_id] = [
            next_hop_id,
            next_hop_mac,
            metric,
            None,  # Reserved
            expiry_ms
        ]
        print(f"  [ROUTE] Added: {dest_id} via {next_hop_id} (metric={metric}, expires in {ttl_ms}ms)")


def demo():
    """Demonstrate packet processing with optimized functions."""
    print("=" * 60)
    print("µMesh Fast Functions Example")
    print("=" * 60)

    processor = PacketProcessor(dup_buffer_size=256)

    # Build a test packet
    print("\n1. Building outgoing packet...")
    packet = processor.build_outgoing(
        pkt_type=0,      # DATA
        dest_id=42,
        src_id=1,
        pkt_id=1234,
        ttl=15,
        payload=b"Hello mesh!"
    )
    print(f"  Packet ({len(packet)} bytes): {packet.hex()}")

    # Process the same packet as incoming
    print("\n2. Processing incoming packet...")
    mac = b'\xAA\xBB\xCC\xDD\xEE\xFF'
    result = processor.process_incoming(packet, mac)
    if result:
        print(f"  Accepted: {result}")

    # Try to process duplicate
    print("\n3. Processing duplicate packet...")
    result = processor.process_incoming(packet, mac)
    if not result:
        print("  Correctly rejected duplicate")

    # Add route and test forwarding
    print("\n4. Adding route and forwarding...")
    processor.add_route(
        dest_id=42,
        next_hop_id=5,
        next_hop_mac=b'\x11\x22\x33\x44\x55\x66',
        metric=256
    )
    next_hop = processor.forward_packet(dest_id=42)
    if next_hop:
        print(f"  Forward to: {next_hop.hex()}")

    # Test metric accumulation for multi-hop path
    print("\n5. Calculating multi-hop path metric...")
    metric = 0
    hops = [
        (250, "Node A"),
        (240, "Node B"),
        (230, "Node C"),
        (220, "Node D"),
    ]
    print(f"  Initial metric: {metric}")
    for lqi, name in hops:
        metric = _accumulate_metric(metric, lqi)
        print(f"    Via {name} (LQI={lqi}): metric = {metric}")

    print("\n" + "=" * 60)
    print("Demo completed successfully!")
    print("=" * 60)


if __name__ == '__main__':
    demo()
