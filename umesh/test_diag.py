"""
Test suite for µMesh diagnostics module.

Tests MeshDiag, get_mesh_stats, and formatting functions.
"""

import sys
import asyncio
from unittest.mock import Mock, MagicMock


# Mock dependencies for testing
class MockAdapter:
    """Mock ESP-NOW adapter."""

    def __init__(self):
        self.mac = b'\xaa\xbb\xcc\xdd\xee\xff'
        self._packets = []

    def send_broadcast(self, packet):
        """Synchronous send for testing (no await needed)."""
        self._packets.append(packet)
        return True


class MockNeighborTable:
    """Mock neighbor table."""

    def __init__(self):
        self._neighbors = {}

    def get_neighbor(self, node_id):
        return self._neighbors.get(node_id)

    def add_neighbor(self, node_id, mac, rssi, hop_count=0):
        self._neighbors[node_id] = {
            'mac': mac,
            'rssi_avg': rssi,
            'lqi': 85,
            'hop_count': hop_count,
            'last_seen': 0,
        }


class MockMesh:
    """Mock mesh for testing diagnostics."""

    def __init__(self):
        self.node_id = 0x1234
        self.mac = b'\xaa\xbb\xcc\xdd\xee\xff'
        self._adapter = MockAdapter()
        self._neighbors = MockNeighborTable()
        self._packet_id = 0
        self._stats = {
            'tx_packets': 100,
            'rx_packets': 200,
            'tx_bytes': 10000,
            'rx_bytes': 20000,
            'dropped_duplicates': 5,
            'dropped_crc_fail': 2,
            'hello_sent': 50,
            'hello_received': 75,
        }
        self._diag = None

        # Mock codec
        self._codec = Mock()
        self._codec.encode = Mock(return_value=b'\x00' * 20)

    def _next_packet_id(self):
        pkt_id = self._packet_id
        self._packet_id = (self._packet_id + 1) & 0xFFFF
        return pkt_id

    @property
    def neighbors(self):
        """Mock neighbors property."""
        result = []
        for node_id, info in self._neighbors._neighbors.items():
            result.append((node_id, info['mac'], info['rssi_avg'], info['lqi']))
        result.sort(key=lambda x: x[3], reverse=True)
        return result

    @property
    def stats(self):
        return self._stats.copy()


def test_mesh_diag_init():
    """Test MeshDiag initialization."""
    from umesh.diag import MeshDiag

    mesh = MockMesh()
    diag = MeshDiag(mesh)

    assert diag._mesh is mesh
    assert diag._ping_events == {}
    assert diag._trace_results == {}
    assert diag._seq_counter == 0

    print("PASS MeshDiag initialization")


def test_get_mesh_stats():
    """Test get_mesh_stats function."""
    from umesh.diag import get_mesh_stats

    mesh = MockMesh()

    # Add some neighbors
    mesh._neighbors.add_neighbor(0x0042, b'\xaa\xbb\xcc\xdd\xee\x01', -45, 1)
    mesh._neighbors.add_neighbor(0x0043, b'\xaa\xbb\xcc\xdd\xee\x02', -60, 2)

    stats = get_mesh_stats(mesh)

    assert stats['node_id'] == '0x1234'
    assert stats['mac'] == 'aa:bb:cc:dd:ee:ff'
    assert stats['tx_packets'] == 100
    assert stats['rx_packets'] == 200
    assert stats['tx_bytes'] == 10000
    assert stats['rx_bytes'] == 20000
    assert stats['dropped_duplicates'] == 5
    assert stats['dropped_crc_fail'] == 2
    assert stats['hello_sent'] == 50
    assert stats['hello_received'] == 75
    assert stats['neighbor_count'] == 2
    assert stats['route_count'] == 0  # No routing in Phase 1
    assert stats['pending_routes'] == 0

    print("PASS get_mesh_stats")


def test_format_stats():
    """Test format_stats function."""
    from umesh.diag import get_mesh_stats, format_stats

    mesh = MockMesh()
    stats = get_mesh_stats(mesh)

    formatted = format_stats(stats)

    assert 'µMesh Network Statistics' in formatted
    assert '0x1234' in formatted
    assert 'aa:bb:cc:dd:ee:ff' in formatted
    assert '100 packets' in formatted
    assert '200 packets' in formatted

    print("PASS format_stats")
    print("\nFormatted stats sample:")
    print(formatted[:400])


def test_format_neighbors():
    """Test format_neighbors function."""
    from umesh.diag import format_neighbors

    mesh = MockMesh()

    # Add some neighbors
    mesh._neighbors.add_neighbor(0x0042, b'\xaa\xbb\xcc\xdd\xee\x01', -45, 1)
    mesh._neighbors.add_neighbor(0x0043, b'\xaa\xbb\xcc\xdd\xee\x02', -60, 2)

    formatted = format_neighbors(mesh)

    assert 'Neighbor Table' in formatted
    assert '0042' in formatted
    assert '0043' in formatted
    assert 'aa:bb:cc:dd:ee:01' in formatted

    print("PASS format_neighbors")
    print("\nFormatted neighbors sample:")
    print(formatted)


def test_format_routes():
    """Test format_routes function."""
    from umesh.diag import format_routes

    mesh = MockMesh()

    formatted = format_routes(mesh)

    assert 'Routing Table' in formatted
    assert 'routing not enabled' in formatted  # Phase 1 has no routing

    print("PASS format_routes")


def test_ping_payload_format():
    """Test PING payload encoding."""
    import struct
    from umesh.diag import MeshDiag

    mesh = MockMesh()
    diag = MeshDiag(mesh)

    # Test payload format
    seq = 42
    timestamp = 12345
    payload = struct.pack('>HI', seq, timestamp)

    assert len(payload) == 6
    decoded_seq, decoded_ts = struct.unpack('>HI', payload)
    assert decoded_seq == seq
    assert decoded_ts == timestamp

    print("PASS PING payload format")


def test_pong_payload_format():
    """Test PONG payload encoding."""
    import struct
    from umesh.diag import MeshDiag

    mesh = MockMesh()
    diag = MeshDiag(mesh)

    # Test payload format
    seq = 42
    timestamp = 12345
    rssi = -55
    hop_count = 3
    payload = struct.pack('>HIbB', seq, timestamp, rssi, hop_count)

    assert len(payload) == 8
    decoded_seq, decoded_ts, decoded_rssi, decoded_hops = struct.unpack('>HIbB', payload)
    assert decoded_seq == seq
    assert decoded_ts == timestamp
    assert decoded_rssi == rssi
    assert decoded_hops == hop_count

    print("PASS PONG payload format")


def test_handle_ping():
    """Test PING handling."""
    import struct
    from umesh.diag import MeshDiag

    mesh = MockMesh()
    diag = MeshDiag(mesh)

    # Add neighbor for hop count
    mesh._neighbors.add_neighbor(0x5678, b'\xaa\xbb\xcc\xdd\xee\x01', -50, 1)

    # Simulate PING payload
    seq = 10
    timestamp = 1000
    payload = struct.pack('>HI', seq, timestamp)

    # Handle PING
    diag.handle_ping(0x5678, payload, -50)

    # Check that PONG was sent
    assert len(mesh._adapter._packets) == 1
    assert mesh._codec.encode.called

    print("PASS handle_ping")


def test_handle_pong():
    """Test PONG handling."""
    import struct
    from umesh.diag import MeshDiag

    mesh = MockMesh()
    diag = MeshDiag(mesh)

    # Add neighbor
    mesh._neighbors.add_neighbor(0x5678, b'\xaa\xbb\xcc\xdd\xee\x01', -50, 1)

    # Create pending ping
    seq = 10
    event = asyncio.Event()
    result = {}
    diag._ping_events[seq] = (event, result)

    # Simulate PONG payload
    timestamp = 1000
    pong_rssi = -55
    hop_count = 2
    payload = struct.pack('>HIbB', seq, timestamp, pong_rssi, hop_count)

    # Handle PONG
    diag.handle_pong(0x5678, payload, -50)

    # Check results
    assert event.is_set()
    assert result['node_id'] == 0x5678
    assert result['rssi'] == -50
    assert result['pong_rssi'] == pong_rssi
    assert result['hop_count'] == hop_count
    assert 'rtt' in result

    print("PASS handle_pong")


def test_ping_sequence():
    """Test ping sequence number generation."""
    from umesh.diag import MeshDiag

    mesh = MockMesh()
    diag = MeshDiag(mesh)

    # Check initial sequence
    assert diag._seq_counter == 0

    # Manually increment
    for i in range(5):
        seq = diag._seq_counter
        diag._seq_counter = (diag._seq_counter + 1) & 0xFFFF
        assert seq == i

    print("PASS ping sequence")


def test_ping_event_management():
    """Test ping event management."""
    from umesh.diag import MeshDiag

    mesh = MockMesh()
    diag = MeshDiag(mesh)

    # Create ping event
    seq = 42
    event = asyncio.Event()
    result = {}
    diag._ping_events[seq] = (event, result)

    # Verify stored
    assert seq in diag._ping_events
    assert diag._ping_events[seq] == (event, result)

    # Cleanup
    del diag._ping_events[seq]
    assert seq not in diag._ping_events

    print("PASS ping event management")


def run_tests():
    """Run all tests."""
    print("=" * 60)
    print("µMesh Diagnostics Tests")
    print("=" * 60)
    print()

    # Synchronous tests
    test_mesh_diag_init()
    test_get_mesh_stats()
    test_format_stats()
    test_format_neighbors()
    test_format_routes()
    test_ping_payload_format()
    test_pong_payload_format()
    test_handle_ping()
    test_handle_pong()

    # Additional tests
    print()
    test_ping_sequence()
    test_ping_event_management()

    print()
    print("=" * 60)
    print("All tests passed!")
    print("=" * 60)


if __name__ == '__main__':
    run_tests()
