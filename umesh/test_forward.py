"""
Tests for ForwardingEngine
===========================

Unit tests for packet forwarding, TTL handling, broadcast rebroadcast,
and pending queue management.
"""

import sys
import asyncio

# Add parent directory to path for imports
sys.path.insert(0, '..')

from umesh.packet import PacketCodec
from umesh.forward import ForwardingEngine, process_incoming_packet, PENDING_TIMEOUT, REBROADCAST_JITTER
from umesh.const import (
    PKT_DATA, PKT_HELLO, PKT_PING, PKT_PONG,
    BROADCAST_ID, BROADCAST_MAC,
    DEFAULT_TTL,
)


# =============================================================================
# MOCK CLASSES FOR TESTING
# =============================================================================

class MockAdapter:
    """Mock ESP-NOW adapter for testing."""

    def __init__(self):
        self.sent_packets = []  # List of (mac, data) tuples
        self.mac = b'\x11\x22\x33\x44\x55\x66'

    async def send(self, mac: bytes, data: bytes):
        """Record sent packets."""
        self.sent_packets.append((mac, data))

    async def send_broadcast(self, data: bytes):
        """Record broadcast packets."""
        self.sent_packets.append((BROADCAST_MAC, data))
        return True

    def clear(self):
        """Clear sent packets list."""
        self.sent_packets = []


class MockNeighborTable:
    """Mock neighbor table for testing."""

    def __init__(self):
        self.neighbors = {}  # node_id -> mac mapping

    def get_node_id(self, mac: bytes):
        """Get node_id for MAC address."""
        for node_id, neighbor_mac in self.neighbors.items():
            if neighbor_mac == mac:
                return node_id
        return None

    def add_neighbor(self, mac: bytes, node_id: int):
        """Add neighbor for testing."""
        self.neighbors[node_id] = mac


class MockPeerManager:
    """Mock peer manager for testing."""

    async def ensure_peer(self, mac: bytes):
        """Mock ensure_peer - always succeeds."""
        pass

    def get_node_id(self, mac: bytes):
        """Mock get_node_id."""
        return None

    def mark_on_route(self, mac: bytes, on_route: bool):
        """Mock mark_on_route."""
        pass

    def set_node_id(self, mac: bytes, node_id: int):
        """Mock set_node_id."""
        pass


class MockRouteEntry:
    """Mock route entry object (matches RouteEntry API)."""

    __slots__ = ('next_hop_mac', 'next_hop_id', 'metric', 'hop_count', 'seq_num')

    def __init__(self, next_hop_mac, hop_count=1):
        self.next_hop_mac = next_hop_mac
        self.next_hop_id = 0
        self.metric = 100
        self.hop_count = hop_count
        self.seq_num = 0


class MockRoutingTable:
    """Mock routing table for testing."""

    def __init__(self):
        self.routes = {}  # dest_id -> MockRouteEntry

    def lookup(self, dest_id: int):
        """Look up route to destination."""
        return self.routes.get(dest_id)

    def add_route(self, dest_id: int, next_hop_mac: bytes):
        """Add route for testing."""
        self.routes[dest_id] = MockRouteEntry(next_hop_mac)


class MockRouteDiscovery:
    """Mock route discovery for testing."""

    def __init__(self):
        self.discovery_started = []  # List of dest_ids

    async def discover(self, dest_id: int):
        """Record route discovery request."""
        self.discovery_started.append(dest_id)


class MockMesh:
    """Mock Mesh instance for testing."""

    def __init__(self, node_id=0x1234):
        self.node_id = node_id
        self._adapter = MockAdapter()
        self._neighbors = MockNeighborTable()
        self._peer_mgr = MockPeerManager()
        self._message_callback = None
        self._message_queue = asyncio.Queue(maxsize=16)
        self.delivered_messages = []  # Track delivered messages

    def _handle_hello(self, mac, parsed, rssi):
        """Mock hello handler."""
        pass

    async def _handle_data_or_forward(self, mac, parsed, raw, rssi):
        """Mock data handler."""
        self.delivered_messages.append((parsed['src_id'], parsed['payload']))

    async def _handle_ping(self, src_id, payload, rssi):
        """Mock ping handler."""
        pass

    def _handle_pong(self, src_id, payload, rssi):
        """Mock pong handler."""
        pass


# =============================================================================
# TEST CASES
# =============================================================================

def test_ttl_decrement():
    """Test that TTL is decremented and packets are dropped when TTL=0."""
    print("Testing TTL decrement...")

    mesh = MockMesh(node_id=0x1234)
    routes = MockRoutingTable()
    discovery = MockRouteDiscovery()
    engine = ForwardingEngine(mesh, routes, discovery)

    codec = PacketCodec()

    # Create packet with TTL=1 (should be dropped after decrement)
    packet_ttl1 = codec.encode(
        PKT_DATA,
        src_id=0xAAAA,
        dest_id=0xBBBB,
        payload=b'Test',
        ttl=1,
        pkt_id=100
    )

    # Forward should fail (TTL exhausted)
    result = asyncio.run(engine.forward(packet_ttl1, b'\xAA\xAA\xAA\xAA\xAA\xAA', -50))
    assert result == False, "Should drop packet with TTL=1"

    # Create packet with TTL=2 (should be forwarded with TTL=1)
    packet_ttl2 = codec.encode(
        PKT_DATA,
        src_id=0xAAAA,
        dest_id=BROADCAST_ID,
        payload=b'Test',
        ttl=2,
        pkt_id=101
    )

    # Should rebroadcast with TTL=1
    result = asyncio.run(engine.forward(packet_ttl2, b'\xAA\xAA\xAA\xAA\xAA\xAA', -50))
    assert result == True, "Should forward packet with TTL=2"
    assert len(mesh._adapter.sent_packets) == 1, "Should rebroadcast once"

    # Decode rebroadcast packet and check TTL
    rebroadcast_packet = mesh._adapter.sent_packets[0][1]
    parsed = codec.decode(rebroadcast_packet)
    assert parsed['ttl'] == 1, f"Rebroadcast TTL should be 1, got {parsed['ttl']}"
    assert parsed['hop_count'] == 1, f"Hop count should be 1, got {parsed['hop_count']}"

    print("  PASS: TTL decrement works correctly")


def test_local_delivery():
    """Test that packets destined for this node are delivered locally."""
    print("Testing local delivery...")

    mesh = MockMesh(node_id=0x1234)
    routes = MockRoutingTable()
    discovery = MockRouteDiscovery()
    engine = ForwardingEngine(mesh, routes, discovery)

    codec = PacketCodec()

    # Create packet for this node
    packet = codec.encode(
        PKT_DATA,
        src_id=0xAAAA,
        dest_id=0x1234,  # This node's ID
        payload=b'Hello local',
        ttl=5,
        pkt_id=200
    )

    # Should be delivered locally
    result = asyncio.run(engine.forward(packet, b'\xAA\xAA\xAA\xAA\xAA\xAA', -50))
    assert result == True, "Should deliver locally"

    # Should NOT be rebroadcast (unicast to this node)
    assert len(mesh._adapter.sent_packets) == 0, "Should not rebroadcast unicast to self"

    print("  PASS: Local delivery works")


def test_broadcast_rebroadcast():
    """Test that broadcast packets are delivered locally AND rebroadcast."""
    print("Testing broadcast rebroadcast...")

    mesh = MockMesh(node_id=0x1234)
    routes = MockRoutingTable()
    discovery = MockRouteDiscovery()
    engine = ForwardingEngine(mesh, routes, discovery)

    codec = PacketCodec()

    # Create broadcast packet
    packet = codec.encode(
        PKT_DATA,
        src_id=0xAAAA,
        dest_id=BROADCAST_ID,
        payload=b'Broadcast test',
        ttl=5,
        pkt_id=300
    )

    # Should be delivered locally AND rebroadcast
    result = asyncio.run(engine.forward(packet, b'\xAA\xAA\xAA\xAA\xAA\xAA', -50))
    assert result == True, "Should handle broadcast"

    # Should rebroadcast to BROADCAST_MAC
    assert len(mesh._adapter.sent_packets) == 1, "Should rebroadcast once"
    assert mesh._adapter.sent_packets[0][0] == BROADCAST_MAC, "Should use BROADCAST_MAC"

    # Check rebroadcast packet
    rebroadcast = codec.decode(mesh._adapter.sent_packets[0][1])
    assert rebroadcast['ttl'] == 4, "TTL should be decremented"
    assert rebroadcast['hop_count'] == 1, "Hop count should be incremented"
    assert rebroadcast['dest_id'] == BROADCAST_ID, "Should remain broadcast"

    print("  PASS: Broadcast rebroadcast works")


def test_unicast_forwarding():
    """Test unicast forwarding via routing table."""
    print("Testing unicast forwarding...")

    mesh = MockMesh(node_id=0x1234)
    routes = MockRoutingTable()
    discovery = MockRouteDiscovery()
    engine = ForwardingEngine(mesh, routes, discovery)

    codec = PacketCodec()

    # Add route to 0xBBBB via next hop 0xCCCC
    next_hop_mac = b'\xCC\xCC\xCC\xCC\xCC\xCC'
    routes.add_route(0xBBBB, next_hop_mac)
    mesh._neighbors.add_neighbor(next_hop_mac, 0xCCCC)

    # Create unicast packet to 0xBBBB
    packet = codec.encode(
        PKT_DATA,
        src_id=0xAAAA,
        dest_id=0xBBBB,
        payload=b'Unicast test',
        ttl=5,
        pkt_id=400
    )

    # Should forward to next hop
    result = asyncio.run(engine.forward(packet, b'\xAA\xAA\xAA\xAA\xAA\xAA', -50))
    assert result == True, "Should forward unicast"

    # Should send to next_hop_mac
    assert len(mesh._adapter.sent_packets) == 1, "Should forward once"
    assert mesh._adapter.sent_packets[0][0] == next_hop_mac, "Should use next hop MAC"

    # Check forwarded packet
    forwarded = codec.decode(mesh._adapter.sent_packets[0][1])
    assert forwarded['ttl'] == 4, "TTL should be decremented"
    assert forwarded['hop_count'] == 1, "Hop count should be incremented"
    assert forwarded['dest_id'] == 0xBBBB, "Dest should remain unchanged"

    print("  PASS: Unicast forwarding works")


def test_pending_queue():
    """Test pending queue and route discovery trigger."""
    print("Testing pending queue...")

    mesh = MockMesh(node_id=0x1234)
    routes = MockRoutingTable()
    discovery = MockRouteDiscovery()
    engine = ForwardingEngine(mesh, routes, discovery)

    codec = PacketCodec()

    # Create unicast packet with no route
    packet = codec.encode(
        PKT_DATA,
        src_id=0xAAAA,
        dest_id=0xDDDD,  # No route to this dest
        payload=b'Pending test',
        ttl=5,
        pkt_id=500
    )

    # Should queue and start route discovery
    result = asyncio.run(engine.forward(packet, b'\xAA\xAA\xAA\xAA\xAA\xAA', -50))
    assert result == True, "Should queue packet"

    # Should NOT forward (no route)
    assert len(mesh._adapter.sent_packets) == 0, "Should not forward without route"

    # Check pending queue
    assert len(engine._pending) == 1, "Should have 1 pending packet"
    assert engine._pending[0][0] == 0xDDDD, "Pending for correct dest"

    # Check route discovery started
    assert 0xDDDD in discovery.discovery_started, "Should start route discovery"

    print("  PASS: Pending queue works")


def test_flush_pending():
    """Test flushing pending packets after route found."""
    print("Testing flush pending...")

    mesh = MockMesh(node_id=0x1234)
    routes = MockRoutingTable()
    discovery = MockRouteDiscovery()
    engine = ForwardingEngine(mesh, routes, discovery)

    codec = PacketCodec()

    # Queue packet for 0xDDDD (no route)
    packet = codec.encode(
        PKT_DATA,
        src_id=0xAAAA,
        dest_id=0xDDDD,
        payload=b'Queued packet',
        ttl=5,
        pkt_id=600
    )

    asyncio.run(engine.forward(packet, b'\xAA\xAA\xAA\xAA\xAA\xAA', -50))
    assert len(engine._pending) == 1, "Should have pending packet"

    # Now add route to 0xDDDD
    next_hop_mac = b'\xDD\xDD\xDD\xDD\xDD\xDD'
    routes.add_route(0xDDDD, next_hop_mac)
    mesh._neighbors.add_neighbor(next_hop_mac, 0xDDDD)

    # Flush pending packets
    asyncio.run(engine.flush_pending(0xDDDD))

    # Pending queue should be empty
    assert len(engine._pending) == 0, "Pending queue should be empty"

    # Packet should be sent
    assert len(mesh._adapter.sent_packets) == 1, "Should send pending packet"
    assert mesh._adapter.sent_packets[0][0] == next_hop_mac, "Should use correct next hop"

    print("  PASS: Flush pending works")


def test_discard_pending():
    """Test discarding pending packets for unreachable destination."""
    print("Testing discard pending...")

    mesh = MockMesh(node_id=0x1234)
    routes = MockRoutingTable()
    discovery = MockRouteDiscovery()
    engine = ForwardingEngine(mesh, routes, discovery)

    codec = PacketCodec()

    # Queue packet for 0xEEEE
    packet = codec.encode(
        PKT_DATA,
        src_id=0xAAAA,
        dest_id=0xEEEE,
        payload=b'Unreachable',
        ttl=5,
        pkt_id=700
    )

    asyncio.run(engine.forward(packet, b'\xAA\xAA\xAA\xAA\xAA\xAA', -50))
    assert len(engine._pending) == 1, "Should have pending packet"

    # Discard pending for 0xEEEE (destination unreachable)
    engine.discard_pending(0xEEEE)

    # Pending queue should be empty
    assert len(engine._pending) == 0, "Should discard pending packets"

    print("  PASS: Discard pending works")


def test_prune_pending():
    """Test pruning old pending packets."""
    print("Testing prune pending...")

    mesh = MockMesh(node_id=0x1234)
    routes = MockRoutingTable()
    discovery = MockRouteDiscovery()
    engine = ForwardingEngine(mesh, routes, discovery)

    codec = PacketCodec()

    # Manually add old pending packet
    from time import monotonic
    ticks_ms = lambda: int(monotonic() * 1000)
    old_timestamp = ticks_ms() - (PENDING_TIMEOUT + 1000)  # Expired

    packet = codec.encode(
        PKT_DATA,
        src_id=0xAAAA,
        dest_id=0xFFFF,
        payload=b'Old packet',
        ttl=5,
        pkt_id=800
    )

    engine._pending.append((0xFFFF, packet, old_timestamp))
    assert len(engine._pending) == 1, "Should have old pending packet"

    # Prune expired packets
    engine.prune_pending()

    # Should be empty (old packet removed)
    assert len(engine._pending) == 0, "Should prune old packets"

    print("  PASS: Prune pending works")


def test_hop_count_increment():
    """Test that hop_count is incremented on forward."""
    print("Testing hop count increment...")

    mesh = MockMesh(node_id=0x1234)
    routes = MockRoutingTable()
    discovery = MockRouteDiscovery()
    engine = ForwardingEngine(mesh, routes, discovery)

    codec = PacketCodec()

    # Create packet with hop_count=0
    packet = codec.encode(
        PKT_DATA,
        src_id=0xAAAA,
        dest_id=BROADCAST_ID,
        payload=b'Hop test',
        ttl=5,
        hop_count=0,
        pkt_id=900
    )

    # Forward packet
    asyncio.run(engine.forward(packet, b'\xAA\xAA\xAA\xAA\xAA\xAA', -50))

    # Check rebroadcast has hop_count=1
    rebroadcast = codec.decode(mesh._adapter.sent_packets[0][1])
    assert rebroadcast['hop_count'] == 1, "Hop count should be 1"

    print("  PASS: Hop count increment works")


# =============================================================================
# RUN ALL TESTS
# =============================================================================

def run_all_tests():
    """Run all test cases."""
    print("=" * 60)
    print("ForwardingEngine Tests")
    print("=" * 60)

    test_ttl_decrement()
    test_local_delivery()
    test_broadcast_rebroadcast()
    test_unicast_forwarding()
    test_pending_queue()
    test_flush_pending()
    test_discard_pending()
    test_prune_pending()
    test_hop_count_increment()

    print("=" * 60)
    print("All tests passed!")
    print("=" * 60)


if __name__ == '__main__':
    run_all_tests()
