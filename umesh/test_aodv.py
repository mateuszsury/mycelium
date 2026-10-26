"""
Tests for AODV-lite Route Discovery
====================================

Unit tests for RoutingTable and RouteDiscovery classes.
Tests sequence number comparison, route updates, RREQ/RREP handling,
and expanding ring search.

Run with: python -m pytest test_aodv.py -v
Or directly: python test_aodv.py
"""

import struct
import asyncio
from time import monotonic

# Fallback time functions for testing
ticks_ms = lambda: int(monotonic() * 1000)
ticks_diff = lambda a, b: a - b


# =============================================================================
# MOCK MESH INSTANCE
# =============================================================================

class MockNeighborTable(dict):
    def get_neighbor(self, node_id):
        neighbor = self.get(node_id)
        return None if neighbor is None else {'lqi': neighbor.lqi, 'node_id': neighbor.node_id}


class MockMesh:
    """Mock Mesh instance for testing."""

    def __init__(self, node_id):
        self.node_id = node_id
        self._neighbors = MockNeighborTable()  # node_id -> MockNeighbor
        self._sent_packets = []  # Track sent packets

    async def _send_packet(self, pkt_type, dest_id, dest_mac, payload, ttl, hop_count):
        """Mock send packet - records to _sent_packets."""
        self._sent_packets.append({
            'pkt_type': pkt_type,
            'dest_id': dest_id,
            'dest_mac': dest_mac,
            'payload': payload,
            'ttl': ttl,
            'hop_count': hop_count,
        })

    def clear_sent(self):
        """Clear sent packets buffer."""
        self._sent_packets.clear()


class MockNeighbor:
    """Mock neighbor entry."""

    def __init__(self, node_id, lqi=200):
        self.node_id = node_id
        self.lqi = lqi


# =============================================================================
# ROUTING TABLE TESTS
# =============================================================================

def test_routing_table_basic():
    """Test basic routing table operations."""
    from .routing import RoutingTable

    rt = RoutingTable()

    # Initial state
    assert rt.seq_num == 0
    assert rt.lookup(0x2000) is None

    # Add route
    success = rt.update(
        dest_id=0x2000,
        next_hop_id=0x1001,
        next_hop_mac=b'\x00\x01\x02\x03\x04\x05',
        metric=100,
        seq_num=1,
        hop_count=1,
        lifetime_ms=10000,
    )
    assert success is True

    # Lookup route
    route = rt.lookup(0x2000)
    assert route is not None
    assert route.next_hop_id == 0x1001
    assert route.next_hop_mac == b'\x00\x01\x02\x03\x04\x05'
    assert route.metric == 100
    assert route.seq_num == 1
    assert route.hop_count == 1

    print("[OK] Routing table basic operations")


def test_routing_table_sequence_freshness():
    """Test sequence number freshness comparison."""
    from .routing import RoutingTable

    rt = RoutingTable()

    # Add initial route with seq=5
    rt.update(0x2000, 0x1001, b'\x01' * 6, 100, 5, 1)

    # Try updating with older sequence (should reject)
    success = rt.update(0x2000, 0x1002, b'\x02' * 6, 50, 3, 1)
    assert success is False
    route = rt.lookup(0x2000)
    assert route.next_hop_id == 0x1001  # Still old next hop
    assert route.seq_num == 5  # Still old sequence

    # Update with fresher sequence (should accept)
    success = rt.update(0x2000, 0x1003, b'\x03' * 6, 200, 10, 2)
    assert success is True
    route = rt.lookup(0x2000)
    assert route.next_hop_id == 0x1003  # New next hop
    assert route.seq_num == 10  # New sequence

    # Update with same sequence but better metric (should accept)
    success = rt.update(0x2000, 0x1004, b'\x04' * 6, 150, 10, 2)
    assert success is True
    route = rt.lookup(0x2000)
    assert route.next_hop_id == 0x1004  # New next hop (better metric)
    assert route.metric == 150  # Better metric

    print("[OK] Sequence number freshness comparison")


def test_routing_table_expiry():
    """Test route expiration."""
    from .routing import RoutingTable

    rt = RoutingTable()

    # Add route with very short lifetime
    rt.update(0x2000, 0x1001, b'\x01' * 6, 100, 1, 1, lifetime_ms=1)

    # Should exist immediately
    route = rt.lookup(0x2000)
    assert route is not None

    # Wait for expiry
    import time
    time.sleep(0.01)

    # Should be gone
    route = rt.lookup(0x2000)
    assert route is None

    print("[OK] Route expiration")


def test_routing_table_max_routes():
    """Test max routes limit and eviction."""
    from .routing import RoutingTable

    rt = RoutingTable(max_routes=3)

    # Add 3 routes
    rt.update(0x2001, 0x1001, b'\x01' * 6, 100, 1, 1, lifetime_ms=100000)
    rt.update(0x2002, 0x1002, b'\x02' * 6, 100, 1, 1, lifetime_ms=100000)
    rt.update(0x2003, 0x1003, b'\x03' * 6, 100, 1, 1, lifetime_ms=100000)

    # All should exist
    assert rt.lookup(0x2001) is not None
    assert rt.lookup(0x2002) is not None
    assert rt.lookup(0x2003) is not None

    # Add 4th route - should raise MemoryError (routing.py doesn't auto-evict)
    import time
    time.sleep(0.001)
    try:
        rt.update(0x2004, 0x1004, b'\x04' * 6, 100, 1, 1, lifetime_ms=100000)
        assert False, "Should have raised MemoryError"
    except MemoryError:
        pass  # Expected

    print("[OK] Max routes limit")


def test_routing_table_prune():
    """Test pruning expired routes."""
    from .routing import RoutingTable

    rt = RoutingTable()

    # Add routes with different lifetimes
    rt.update(0x2001, 0x1001, b'\x01' * 6, 100, 1, 1, lifetime_ms=1)
    rt.update(0x2002, 0x1002, b'\x02' * 6, 100, 1, 1, lifetime_ms=100000)

    import time
    time.sleep(0.01)

    # Prune expired
    pruned = rt.prune_expired()

    # Short-lived should be gone
    assert 0x2001 in pruned
    assert rt.lookup(0x2001) is None
    assert rt.lookup(0x2002) is not None

    print("[OK] Pruning expired routes")


# =============================================================================
# ROUTE DISCOVERY TESTS
# =============================================================================

def test_route_discovery_rreq_duplicate():
    """Test RREQ duplicate detection."""
    from .aodv import RouteDiscovery
    from .routing import RoutingTable

    mesh = MockMesh(node_id=0x1000)
    rt = RoutingTable()
    rd = RouteDiscovery(mesh, rt)

    # First RREQ
    assert rd._is_duplicate_rreq(0x2000, 42) is False
    rd._record_rreq(0x2000, 42)

    # Same RREQ - should be duplicate
    assert rd._is_duplicate_rreq(0x2000, 42) is True

    # Different RREQ ID - not duplicate
    assert rd._is_duplicate_rreq(0x2000, 43) is False

    # Different originator - not duplicate
    assert rd._is_duplicate_rreq(0x3000, 42) is False

    print("[OK] RREQ duplicate detection")


def test_route_discovery_prune_seen():
    """Test pruning old seen RREQs."""
    from .aodv import RouteDiscovery
    from .routing import RoutingTable

    mesh = MockMesh(node_id=0x1000)
    rt = RoutingTable()
    rd = RouteDiscovery(mesh, rt)

    # Record RREQ
    rd._record_rreq(0x2000, 42)
    assert rd._is_duplicate_rreq(0x2000, 42) is True

    # Prune with very short max age
    import time
    time.sleep(0.01)
    rd.prune_seen_rreq(max_age_ms=1)

    # Should be gone
    assert rd._is_duplicate_rreq(0x2000, 42) is False

    print("[OK] Pruning old seen RREQs")


async def test_route_discovery_send_rreq():
    """Test RREQ sending."""
    from .aodv import RouteDiscovery
    from .routing import RoutingTable
    from .const import PKT_RREQ, BROADCAST_ID

    mesh = MockMesh(node_id=0x1000)
    rt = RoutingTable()
    rd = RouteDiscovery(mesh, rt)

    # Send RREQ
    await rd.send_rreq(dest_id=0x2000, ttl=3, rreq_id=42)

    # Check sent packet
    assert len(mesh._sent_packets) == 1
    pkt = mesh._sent_packets[0]
    assert pkt['pkt_type'] == PKT_RREQ
    assert pkt['dest_id'] == BROADCAST_ID
    assert pkt['ttl'] == 3

    # Decode payload
    payload = pkt['payload']
    dest_id, src_seq, dest_seq, hop_count, originator_id, metric, rreq_id = \
        struct.unpack('!HHHBHHH', payload)

    assert dest_id == 0x2000
    assert originator_id == 0x1000
    assert hop_count == 0
    assert metric == 0
    assert rreq_id == 42

    print("[OK] RREQ sending")


async def test_route_discovery_handle_rreq_destination():
    """Test handling RREQ when we are the destination."""
    from .aodv import RouteDiscovery
    from .routing import RoutingTable
    from .const import PKT_RREP

    mesh = MockMesh(node_id=0x2000)  # We are the destination
    # Add mock neighbor for sender
    mesh._neighbors[0x1001] = MockNeighbor(0x1001, lqi=200)

    rt = RoutingTable()
    rd = RouteDiscovery(mesh, rt)

    # Build RREQ payload
    payload = struct.pack('!HHHBHHH',
        0x2000,  # dest_id (us)
        5,       # src_seq
        0,       # dest_seq
        1,       # hop_count
        0x1000,  # originator_id
        100,     # metric
        42       # rreq_id
    )

    header = {
        'src_id': 0x1001,  # Direct neighbor
        'ttl': 7,
    }

    # Handle RREQ
    await rd.handle_rreq(b'\x01' * 6, header, payload, rssi=-50)

    # Should create reverse route
    reverse = rt.lookup(0x1000)
    assert reverse is not None
    assert reverse.next_hop_id == 0x1001

    # Wait for async RREP send
    await asyncio.sleep(0.01)

    # Should send RREP
    sent = [p for p in mesh._sent_packets if p['pkt_type'] == PKT_RREP]
    assert len(sent) == 1
    rrep = sent[0]

    # Decode RREP
    dest_id, dest_seq, originator_id, hop_count, lifetime, metric = \
        struct.unpack('!HHHBHH', rrep['payload'][:11])
    assert dest_id == 0x2000
    assert originator_id == 0x1000

    print("[OK] RREQ handling (destination)")


async def test_route_discovery_handle_rrep_originator():
    """Test handling RREP when we are the originator."""
    from .aodv import RouteDiscovery, PendingDiscovery
    from .routing import RoutingTable

    mesh = MockMesh(node_id=0x1000)  # We are the originator
    # Add mock neighbor for sender
    mesh._neighbors[0x1001] = MockNeighbor(0x1001, lqi=200)

    rt = RoutingTable()
    rd = RouteDiscovery(mesh, rt)

    # Create pending discovery
    pending = PendingDiscovery(rreq_id=42)
    rd._pending[0x2000] = pending

    # Build RREP payload
    payload = struct.pack('!HHHBHH',
        0x2000,  # dest_id
        10,      # dest_seq
        0x1000,  # originator_id (us)
        2,       # hop_count
        1200,    # lifetime (centiseconds)
        200      # metric
    )

    header = {
        'src_id': 0x1001,  # Sender
    }

    # Handle RREP
    await rd.handle_rrep(b'\x01' * 6, header, payload, rssi=-60)

    # Should create forward route
    forward = rt.lookup(0x2000)
    assert forward is not None
    assert forward.next_hop_id == 0x1001
    assert forward.seq_num == 10

    # Pending discovery should be completed
    assert pending.found is True
    assert pending.event.is_set()

    print("[OK] RREP handling (originator)")


async def test_route_discovery_expanding_ring():
    """Test expanding ring search TTL sequence."""
    from .aodv import RouteDiscovery
    from .routing import RoutingTable
    from .const import PKT_RREQ

    mesh = MockMesh(node_id=0x1000)
    rt = RoutingTable()
    rd = RouteDiscovery(mesh, rt)

    # Override timeout for faster test
    rd.RREQ_TIMEOUT = 10  # 10ms

    # Start discovery (will timeout)
    task = asyncio.create_task(rd.discover(0x2000, timeout=500))

    # Wait a bit for RREQs to be sent
    await asyncio.sleep(0.1)

    # Cancel to avoid waiting full timeout
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    # Check sent RREQs
    rreqs = [p for p in mesh._sent_packets if p['pkt_type'] == PKT_RREQ]
    assert len(rreqs) >= 2  # Should have sent at least 2 RREQs with different TTLs

    # TTLs should follow EXPANDING_RING sequence
    ttls = [p['ttl'] for p in rreqs]
    assert 1 in ttls  # First attempt
    assert 3 in ttls or 7 in ttls  # Later attempts

    print("[OK] Expanding ring search")


# =============================================================================
# RUN ALL TESTS
# =============================================================================

async def run_async_tests():
    """Run all async tests."""
    await test_route_discovery_send_rreq()
    await test_route_discovery_handle_rreq_destination()
    await test_route_discovery_handle_rrep_originator()
    await test_route_discovery_expanding_ring()


def run_all_tests():
    """Run all tests."""
    print("\n=== Routing Table Tests ===")
    test_routing_table_basic()
    test_routing_table_sequence_freshness()
    test_routing_table_expiry()
    test_routing_table_max_routes()
    test_routing_table_prune()

    print("\n=== Route Discovery Tests ===")
    test_route_discovery_rreq_duplicate()
    test_route_discovery_prune_seen()

    print("\n=== Async Route Discovery Tests ===")
    asyncio.run(run_async_tests())

    print("\n[PASS] All AODV tests passed!")


if __name__ == '__main__':
    run_all_tests()
