"""
Tests for NeighborTable and NeighborEntry
==========================================

Unit tests for neighbor tracking, LQI calculation, and table management.
"""

import time
import sys
import os

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Test whether we're in MicroPython or CPython environment
try:
    import micropython
    IS_MICROPYTHON = True
except ImportError:
    IS_MICROPYTHON = False

from umesh.neighbor import NeighborTable, NeighborEntry
from umesh.const import NEIGHBOR_TIMEOUT, HELLO_INTERVAL


def test_neighbor_entry_creation():
    """Test NeighborEntry initialization."""
    print("Testing NeighborEntry creation...", end=" ")

    mac = b'\xaa\xbb\xcc\xdd\xee\xff'
    node_id = 0x1234
    rssi = -65
    now_ms = time.ticks_ms()

    entry = NeighborEntry(mac, node_id, rssi, now_ms, hop_count=0)

    assert entry.mac == mac, "MAC mismatch"
    assert entry.node_id == node_id, "Node ID mismatch"
    assert entry.rssi_avg == (-65 & 0xFF), "RSSI not stored as uint8"
    assert entry.last_seen == now_ms, "Timestamp mismatch"
    assert entry.hello_count == 1, "Initial hello_count should be 1"
    assert entry.expected_hellos == 1, "Initial expected_hellos should be 1"
    assert entry.hop_count == 0, "Hop count mismatch"

    print("PASS")


def test_neighbor_table_basic():
    """Test NeighborTable basic operations."""
    print("Testing NeighborTable basic operations...", end=" ")

    table = NeighborTable()

    # Initially empty
    assert table.count == 0, "Table should be empty"
    assert table.all_neighbors() == [], "Should have no neighbors"

    # Add first neighbor
    mac1 = b'\x01\x02\x03\x04\x05\x06'
    node_id1 = 0x0001
    is_new = table.update(mac1, node_id1, -70)
    assert is_new == True, "First update should return True (new neighbor)"
    assert table.count == 1, "Table should have 1 neighbor"
    assert table.is_neighbor(node_id1) == True, "Should be a known neighbor"

    # Update same neighbor
    is_new = table.update(mac1, node_id1, -68)
    assert is_new == False, "Second update should return False (existing neighbor)"
    assert table.count == 1, "Count should still be 1"

    # Get MAC address
    mac_out = table.get_mac(node_id1)
    assert mac_out == mac1, "MAC lookup failed"

    # Add second neighbor
    mac2 = b'\x0a\x0b\x0c\x0d\x0e\x0f'
    node_id2 = 0x0002
    table.update(mac2, node_id2, -75)
    assert table.count == 2, "Table should have 2 neighbors"

    # Check all_neighbors
    neighbors = table.all_neighbors()
    assert len(neighbors) == 2, "Should have 2 neighbor IDs"
    assert node_id1 in neighbors, "Node 1 missing"
    assert node_id2 in neighbors, "Node 2 missing"

    print("PASS")


def test_lqi_calculation():
    """Test Link Quality Index calculation."""
    print("Testing LQI calculation...", end=" ")

    table = NeighborTable()

    # Add neighbor with good RSSI
    mac = b'\xaa\xbb\xcc\xdd\xee\xff'
    node_id = 0x1234
    rssi_good = -40  # Strong signal
    table.update(mac, node_id, rssi_good)

    lqi = table.get_link_quality(node_id)
    assert lqi > 0, "LQI should be non-zero"
    assert lqi <= 255, "LQI should be <= 255"

    # Unknown neighbor should have LQI of 0
    lqi_unknown = table.get_link_quality(0x9999)
    assert lqi_unknown == 0, "Unknown neighbor should have LQI=0"

    print(f"PASS (LQI={lqi})")


def test_get_neighbor_info():
    """Test get_neighbor and get_by_mac."""
    print("Testing neighbor info retrieval...", end=" ")

    table = NeighborTable()

    mac = b'\x11\x22\x33\x44\x55\x66'
    node_id = 0xABCD
    rssi = -60

    table.update(mac, node_id, rssi)

    # Get by node_id
    info = table.get_neighbor(node_id)
    assert info is not None, "get_neighbor returned None"
    assert info['mac'] == mac, "MAC mismatch in info"
    assert info['rssi_avg'] == rssi, "RSSI mismatch in info"
    assert info['hop_count'] == 0, "Hop count mismatch"
    assert info['lqi'] > 0, "LQI should be positive"

    # Get by MAC
    info_mac = table.get_by_mac(mac)
    assert info_mac is not None, "get_by_mac returned None"
    assert info_mac['mac'] == mac, "MAC mismatch"

    # Unknown lookups
    assert table.get_neighbor(0x9999) is None, "Unknown node should return None"
    assert table.get_by_mac(b'\xff\xff\xff\xff\xff\xff') is None, "Unknown MAC should return None"

    print("PASS")


def test_best_neighbors():
    """Test get_best_neighbors sorting."""
    print("Testing get_best_neighbors...", end=" ")

    table = NeighborTable()

    # Add neighbors with varying RSSI
    neighbors = [
        (b'\x01\x00\x00\x00\x00\x00', 0x0001, -90),  # Weak
        (b'\x02\x00\x00\x00\x00\x00', 0x0002, -50),  # Strong
        (b'\x03\x00\x00\x00\x00\x00', 0x0003, -70),  # Medium
        (b'\x04\x00\x00\x00\x00\x00', 0x0004, -60),  # Good
    ]

    for mac, node_id, rssi in neighbors:
        table.update(mac, node_id, rssi)

    # Get top 2 neighbors
    best = table.get_best_neighbors(2)
    assert len(best) == 2, f"Expected 2 neighbors, got {len(best)}"

    # Should be sorted by LQI (higher is better)
    node_ids = [n[0] for n in best]
    lqis = [n[2] for n in best]

    # LQI should be in descending order
    assert lqis[0] >= lqis[1], "LQI not sorted correctly"

    # Best neighbor should be the one with strongest RSSI (-50)
    assert 0x0002 in node_ids, "Strongest RSSI neighbor not in top 2"

    print(f"PASS (best LQI: {lqis[0]})")


def test_rssi_smoothing():
    """Test RSSI EWMA smoothing."""
    print("Testing RSSI smoothing...", end=" ")

    table = NeighborTable()

    mac = b'\xaa\xbb\xcc\xdd\xee\xff'
    node_id = 0x1234

    # Initial RSSI
    table.update(mac, node_id, -70)
    info1 = table.get_neighbor(node_id)
    rssi1 = info1['rssi_avg']

    # Update with very different RSSI
    table.update(mac, node_id, -50)
    info2 = table.get_neighbor(node_id)
    rssi2 = info2['rssi_avg']

    # RSSI should have moved toward -50 but not reached it (due to EWMA)
    assert rssi2 > rssi1, "RSSI should have increased"
    assert rssi2 < -50, "RSSI should not immediately jump to new value (EWMA smoothing)"

    print(f"PASS (RSSI: {rssi1} -> {rssi2})")


def test_loss_rate_calculation():
    """Test packet loss rate calculation."""
    print("Testing loss rate calculation...", end=" ")

    table = NeighborTable(hello_interval=1000)  # 1 second for testing

    mac = b'\xaa\xbb\xcc\xdd\xee\xff'
    node_id = 0x1234

    # First update
    table.update(mac, node_id, -70)

    # Wait a bit to simulate passage of time
    time.sleep_ms(2500)  # 2.5 seconds

    # Second update (should expect ~2-3 HELLOs by now)
    table.update(mac, node_id, -70)

    info = table.get_neighbor(node_id)
    # We've sent 2 updates, but expected ~2-3, so loss should be low
    assert info['hello_count'] == 2, f"Expected 2 HELLOs, got {info['hello_count']}"
    assert info['expected_hellos'] >= 2, f"Expected >= 2 expected HELLOs, got {info['expected_hellos']}"
    assert info['loss_pct'] <= 50, f"Loss rate too high: {info['loss_pct']}%"

    print(f"PASS (loss: {info['loss_pct']}%)")


def test_prune_dead_neighbors():
    """Test removal of dead neighbors."""
    print("Testing dead neighbor pruning...", end=" ")

    # Use very short timeout for testing
    table = NeighborTable(timeout=100)  # 100ms timeout

    mac = b'\xaa\xbb\xcc\xdd\xee\xff'
    node_id = 0x1234

    table.update(mac, node_id, -70)
    assert table.count == 1, "Should have 1 neighbor"

    # Wait for timeout
    time.sleep_ms(150)

    # Prune dead neighbors
    pruned = table.prune_dead()
    assert len(pruned) == 1, "Should have pruned 1 neighbor"
    assert pruned[0] == node_id, "Wrong neighbor pruned"
    assert table.count == 0, "Table should be empty after pruning"

    print("PASS")


def test_max_neighbors_limit():
    """Test maximum neighbor limit enforcement."""
    print("Testing max_neighbors limit...", end=" ")

    table = NeighborTable(max_neighbors=3)

    # Add 3 neighbors (should succeed)
    for i in range(3):
        mac = bytes([i, 0, 0, 0, 0, 0])
        node_id = 0x1000 + i
        table.update(mac, node_id, -70)

    assert table.count == 3, "Should have 3 neighbors"

    # Try to add 4th neighbor (should raise MemoryError)
    try:
        mac4 = b'\x04\x00\x00\x00\x00\x00'
        table.update(mac4, 0x1004, -70)
        assert False, "Should have raised MemoryError"
    except MemoryError as e:
        assert "full" in str(e).lower(), "Error message should mention 'full'"

    print("PASS")


def run_all_tests():
    """Run all test cases."""
    print("=" * 60)
    print("NeighborTable Test Suite")
    print("=" * 60)
    print(f"Platform: {'MicroPython' if IS_MICROPYTHON else 'CPython'}")
    print()

    tests = [
        test_neighbor_entry_creation,
        test_neighbor_table_basic,
        test_lqi_calculation,
        test_get_neighbor_info,
        test_best_neighbors,
        test_rssi_smoothing,
        test_loss_rate_calculation,
        test_prune_dead_neighbors,
        test_max_neighbors_limit,
    ]

    passed = 0
    failed = 0

    for test in tests:
        try:
            test()
            passed += 1
        except Exception as e:
            print(f"FAIL: {e}")
            if hasattr(sys, 'print_exception'):
                sys.print_exception(e)
            else:
                import traceback
                traceback.print_exc()
            failed += 1

    print()
    print("=" * 60)
    print(f"Results: {passed} passed, {failed} failed")
    print("=" * 60)

    return failed == 0


if __name__ == '__main__':
    success = run_all_tests()
    sys.exit(0 if success else 1)
