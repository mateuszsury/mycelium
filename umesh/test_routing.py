"""
Unit tests for umesh/routing.py - Routing Table
================================================

Tests RouteEntry and RoutingTable classes with focus on:
- Sequence number wraparound handling
- AODV route update rules
- Route expiry management
- Memory bounds enforcement
"""

import time
import pytest


def test_imports():
    """Test that all required components can be imported."""
    from umesh.routing import RouteEntry, RoutingTable, _seq_newer


def test_seq_newer_basic():
    """Test sequence number comparison without wraparound."""
    from umesh.routing import _seq_newer

    # Basic cases
    assert _seq_newer(100, 50) is True      # 100 > 50
    assert _seq_newer(50, 100) is False     # 50 < 100
    assert _seq_newer(100, 100) is False    # Equal is not newer


def test_seq_newer_wraparound():
    """Test sequence number comparison with 16-bit wraparound."""
    from umesh.routing import _seq_newer

    # Wraparound cases (threshold = 0x8000 = 32768)
    assert _seq_newer(10, 65530) is True     # Wrapped: 10 is newer than 65530
    assert _seq_newer(65530, 10) is False    # 65530 is older than 10 (wrapped)

    # Edge cases around threshold
    assert _seq_newer(40000, 8000) is True   # diff=32000 < threshold
    assert _seq_newer(8000, 40000) is False  # diff=33464 > threshold


def test_seq_newer_boundary():
    """Test sequence number at exact wraparound boundary."""
    from umesh.routing import _seq_newer

    # At threshold (0x8000 = 32768)
    assert _seq_newer(32768, 0) is False     # diff=32768 = threshold (not <)
    assert _seq_newer(32767, 0) is True      # diff=32767 < threshold


def test_route_entry_creation():
    """Test RouteEntry initialization."""
    from umesh.routing import RouteEntry

    mac = b'\x01\x02\x03\x04\x05\x06'
    entry = RouteEntry(
        dest_id=100,
        next_hop_id=50,
        next_hop_mac=mac,
        metric=256,
        seq_num=10,
        hop_count=2,
        lifetime_ms=60000
    )

    assert entry.dest_id == 100
    assert entry.next_hop_id == 50
    assert entry.next_hop_mac == mac
    assert entry.metric == 256
    assert entry.seq_num == 10
    assert entry.hop_count == 2
    assert entry.flags == 0


def test_route_entry_seq_num_wraparound():
    """Test RouteEntry sequence number is masked to 16 bits."""
    from umesh.routing import RouteEntry

    mac = b'\xaa\xbb\xcc\xdd\xee\xff'
    entry = RouteEntry(
        dest_id=1,
        next_hop_id=2,
        next_hop_mac=mac,
        metric=100,
        seq_num=0x1FFFF,  # 17 bits - should be masked to 16
        hop_count=1,
        lifetime_ms=1000
    )

    assert entry.seq_num == 0xFFFF  # Masked to 16 bits


def test_route_entry_is_expired():
    """Test RouteEntry expiry detection."""
    from umesh.routing import RouteEntry

    mac = b'\x11\x22\x33\x44\x55\x66'

    # Create entry with short lifetime
    entry = RouteEntry(1, 2, mac, 100, 5, 1, lifetime_ms=100)

    # Should not be expired immediately
    assert entry.is_expired() is False

    # Wait and check again
    time.sleep(0.15)  # 150ms > 100ms lifetime
    assert entry.is_expired() is True


def test_route_entry_is_fresher():
    """Test RouteEntry freshness comparison."""
    from umesh.routing import RouteEntry

    mac = b'\xaa\xbb\xcc\xdd\xee\xff'
    entry = RouteEntry(1, 2, mac, 100, seq_num=50, hop_count=1, lifetime_ms=60000)

    # Test basic freshness
    assert entry.is_fresher(100) is True    # 100 > 50
    assert entry.is_fresher(50) is False    # 50 = 50
    assert entry.is_fresher(25) is False    # 25 < 50

    # Test wraparound
    entry.seq_num = 65530
    assert entry.is_fresher(10) is True     # Wrapped: 10 > 65530


def test_route_entry_touch():
    """Test RouteEntry touch updates timestamp."""
    from umesh.routing import RouteEntry

    mac = b'\x11\x22\x33\x44\x55\x66'
    entry = RouteEntry(1, 2, mac, 100, 5, 1, lifetime_ms=60000)

    initial_time = entry.last_used_ms
    time.sleep(0.01)  # Small delay

    entry.touch()
    assert entry.last_used_ms > initial_time


def test_routing_table_init():
    """Test RoutingTable initialization."""
    from umesh.routing import RoutingTable

    rt = RoutingTable()
    assert rt.count == 0
    assert rt.seq_num == 0

    # Custom parameters
    rt2 = RoutingTable(max_routes=32, route_timeout=30000, active_timeout=90000)
    assert rt2._max_routes == 32
    assert rt2._route_timeout == 30000
    assert rt2._active_timeout == 90000


def test_routing_table_update_new_route():
    """Test adding new route to empty table."""
    from umesh.routing import RoutingTable

    rt = RoutingTable()
    mac = b'\x01\x02\x03\x04\x05\x06'

    result = rt.update(
        dest_id=100,
        next_hop_id=50,
        next_hop_mac=mac,
        metric=256,
        seq_num=10,
        hop_count=2
    )

    assert result is True
    assert rt.count == 1

    entry = rt.lookup(100)
    assert entry is not None
    assert entry.dest_id == 100
    assert entry.next_hop_id == 50
    assert entry.metric == 256
    assert entry.seq_num == 10


def test_routing_table_update_fresher_seq():
    """Test updating route with fresher sequence number."""
    from umesh.routing import RoutingTable

    rt = RoutingTable()
    mac1 = b'\x01\x02\x03\x04\x05\x06'
    mac2 = b'\x0A\x0B\x0C\x0D\x0E\x0F'

    # Add initial route
    rt.update(100, 50, mac1, metric=256, seq_num=10, hop_count=2)

    # Update with fresher seq_num (higher metric, but fresher seq wins)
    result = rt.update(100, 60, mac2, metric=512, seq_num=20, hop_count=3)

    assert result is True
    entry = rt.lookup(100)
    assert entry.next_hop_id == 60
    assert entry.next_hop_mac == mac2
    assert entry.metric == 512
    assert entry.seq_num == 20


def test_routing_table_update_same_seq_better_metric():
    """Test updating route with same seq but better metric."""
    from umesh.routing import RoutingTable

    rt = RoutingTable()
    mac1 = b'\x01\x02\x03\x04\x05\x06'
    mac2 = b'\x0A\x0B\x0C\x0D\x0E\x0F'

    # Add initial route
    rt.update(100, 50, mac1, metric=256, seq_num=10, hop_count=2)

    # Update with same seq_num but better (lower) metric
    result = rt.update(100, 60, mac2, metric=128, seq_num=10, hop_count=1)

    assert result is True
    entry = rt.lookup(100)
    assert entry.metric == 128
    assert entry.next_hop_id == 60
    assert entry.seq_num == 10


def test_routing_table_update_reject_older_seq():
    """Test rejecting route update with older sequence number."""
    from umesh.routing import RoutingTable

    rt = RoutingTable()
    mac1 = b'\x01\x02\x03\x04\x05\x06'
    mac2 = b'\x0A\x0B\x0C\x0D\x0E\x0F'

    # Add initial route
    rt.update(100, 50, mac1, metric=256, seq_num=50, hop_count=2)

    # Try to update with older seq_num - should reject
    result = rt.update(100, 60, mac2, metric=64, seq_num=25, hop_count=1)

    assert result is False
    entry = rt.lookup(100)
    assert entry.seq_num == 50      # Unchanged
    assert entry.next_hop_id == 50   # Unchanged
    assert entry.metric == 256       # Unchanged


def test_routing_table_update_reject_same_seq_worse_metric():
    """Test rejecting route with same seq but worse metric."""
    from umesh.routing import RoutingTable

    rt = RoutingTable()
    mac1 = b'\x01\x02\x03\x04\x05\x06'
    mac2 = b'\x0A\x0B\x0C\x0D\x0E\x0F'

    # Add initial route
    rt.update(100, 50, mac1, metric=256, seq_num=10, hop_count=2)

    # Try to update with same seq but worse (higher) metric
    result = rt.update(100, 60, mac2, metric=512, seq_num=10, hop_count=3)

    assert result is False
    entry = rt.lookup(100)
    assert entry.metric == 256


def test_routing_table_lookup_nonexistent():
    """Test looking up route that doesn't exist."""
    from umesh.routing import RoutingTable

    rt = RoutingTable()
    entry = rt.lookup(999)
    assert entry is None


def test_routing_table_lookup_expired():
    """Test looking up expired route."""
    from umesh.routing import RoutingTable

    rt = RoutingTable()
    mac = b'\x01\x02\x03\x04\x05\x06'

    # Add route with short lifetime
    rt.update(100, 50, mac, metric=256, seq_num=10, hop_count=2, lifetime_ms=100)

    # Verify exists
    assert rt.lookup(100) is not None

    # Wait for expiry
    time.sleep(0.15)

    # Should return None and remove from table
    assert rt.lookup(100) is None
    assert rt.count == 0


def test_routing_table_invalidate():
    """Test invalidating route."""
    from umesh.routing import RoutingTable

    rt = RoutingTable()
    mac = b'\x01\x02\x03\x04\x05\x06'

    rt.update(100, 50, mac, metric=256, seq_num=10, hop_count=2)

    result = rt.invalidate(100)
    assert result is True

    entry = rt.lookup(100)
    assert entry is not None  # Still exists
    assert entry.metric == 65535  # Metric set to infinity


def test_routing_table_invalidate_nonexistent():
    """Test invalidating route that doesn't exist."""
    from umesh.routing import RoutingTable

    rt = RoutingTable()
    result = rt.invalidate(999)
    assert result is False


def test_routing_table_delete():
    """Test deleting route."""
    from umesh.routing import RoutingTable

    rt = RoutingTable()
    mac = b'\x01\x02\x03\x04\x05\x06'

    rt.update(100, 50, mac, metric=256, seq_num=10, hop_count=2)
    assert rt.count == 1

    result = rt.delete(100)
    assert result is True
    assert rt.count == 0
    assert rt.lookup(100) is None


def test_routing_table_delete_nonexistent():
    """Test deleting route that doesn't exist."""
    from umesh.routing import RoutingTable

    rt = RoutingTable()
    result = rt.delete(999)
    assert result is False


def test_routing_table_touch():
    """Test touching route to extend lifetime."""
    from umesh.routing import RoutingTable

    rt = RoutingTable(active_timeout=5000)
    mac = b'\x01\x02\x03\x04\x05\x06'

    rt.update(100, 50, mac, metric=256, seq_num=10, hop_count=2, lifetime_ms=1000)

    entry = rt.lookup(100)
    initial_expiry = entry.expiry_ms

    time.sleep(0.05)
    rt.touch(100)

    entry = rt.lookup(100)
    assert entry.expiry_ms > initial_expiry  # Expiry extended


def test_routing_table_get_all_routes():
    """Test getting all valid routes."""
    from umesh.routing import RoutingTable

    rt = RoutingTable()
    mac1 = b'\x01\x02\x03\x04\x05\x06'
    mac2 = b'\x0A\x0B\x0C\x0D\x0E\x0F'
    mac3 = b'\x11\x22\x33\x44\x55\x66'

    rt.update(100, 50, mac1, metric=256, seq_num=10, hop_count=2)
    rt.update(200, 60, mac2, metric=512, seq_num=20, hop_count=3)
    rt.update(300, 70, mac3, metric=128, seq_num=30, hop_count=1)

    routes = rt.get_all_routes()
    assert len(routes) == 3

    # Should be sorted by dest_id
    assert routes[0][0] == 100
    assert routes[1][0] == 200
    assert routes[2][0] == 300

    # Check tuple format: (dest_id, next_hop_id, metric, hop_count)
    assert routes[0] == (100, 50, 256, 2)


def test_routing_table_get_all_routes_excludes_invalid():
    """Test that get_all_routes excludes invalid/infinite metric routes."""
    from umesh.routing import RoutingTable

    rt = RoutingTable()
    mac1 = b'\x01\x02\x03\x04\x05\x06'
    mac2 = b'\x0A\x0B\x0C\x0D\x0E\x0F'

    rt.update(100, 50, mac1, metric=256, seq_num=10, hop_count=2)
    rt.update(200, 60, mac2, metric=512, seq_num=20, hop_count=3)

    # Invalidate one route
    rt.invalidate(100)

    routes = rt.get_all_routes()
    assert len(routes) == 1
    assert routes[0][0] == 200  # Only valid route


def test_routing_table_prune_expired():
    """Test pruning expired routes."""
    from umesh.routing import RoutingTable

    rt = RoutingTable()
    mac1 = b'\x01\x02\x03\x04\x05\x06'
    mac2 = b'\x0A\x0B\x0C\x0D\x0E\x0F'
    mac3 = b'\x11\x22\x33\x44\x55\x66'

    # Add routes with varying lifetimes
    rt.update(100, 50, mac1, metric=256, seq_num=10, hop_count=2, lifetime_ms=100)
    rt.update(200, 60, mac2, metric=512, seq_num=20, hop_count=3, lifetime_ms=5000)
    rt.update(300, 70, mac3, metric=128, seq_num=30, hop_count=1, lifetime_ms=100)

    assert rt.count == 3

    # Wait for short-lived routes to expire
    time.sleep(0.15)

    pruned = rt.prune_expired()
    assert len(pruned) == 2
    assert 100 in pruned
    assert 300 in pruned
    assert rt.count == 1
    assert rt.lookup(200) is not None


def test_routing_table_next_seq_num():
    """Test sequence number increment."""
    from umesh.routing import RoutingTable

    rt = RoutingTable()
    assert rt.seq_num == 0

    seq1 = rt.next_seq_num()
    assert seq1 == 1
    assert rt.seq_num == 1

    seq2 = rt.next_seq_num()
    assert seq2 == 2
    assert rt.seq_num == 2


def test_routing_table_seq_num_wraparound():
    """Test sequence number wraps at 16-bit boundary."""
    from umesh.routing import RoutingTable

    rt = RoutingTable()
    rt._seq_num = 0xFFFE  # Set to near max

    seq1 = rt.next_seq_num()
    assert seq1 == 0xFFFF

    seq2 = rt.next_seq_num()
    assert seq2 == 0  # Wrapped to 0


def test_routing_table_get_next_hop():
    """Test convenience method for getting next hop."""
    from umesh.routing import RoutingTable

    rt = RoutingTable()
    mac = b'\x01\x02\x03\x04\x05\x06'

    rt.update(100, 50, mac, metric=256, seq_num=10, hop_count=2)

    next_hop_id, next_hop_mac = rt.get_next_hop(100)
    assert next_hop_id == 50
    assert next_hop_mac == mac

    # Nonexistent route
    next_hop_id, next_hop_mac = rt.get_next_hop(999)
    assert next_hop_id is None
    assert next_hop_mac is None


def test_routing_table_has_route():
    """Test has_route convenience method."""
    from umesh.routing import RoutingTable

    rt = RoutingTable()
    mac = b'\x01\x02\x03\x04\x05\x06'

    assert rt.has_route(100) is False

    rt.update(100, 50, mac, metric=256, seq_num=10, hop_count=2)
    assert rt.has_route(100) is True

    rt.delete(100)
    assert rt.has_route(100) is False


def test_routing_table_max_routes():
    """Test memory bound enforcement."""
    from umesh.routing import RoutingTable

    rt = RoutingTable(max_routes=3)
    mac = b'\x01\x02\x03\x04\x05\x06'

    # Add max routes
    rt.update(100, 50, mac, metric=256, seq_num=10, hop_count=2)
    rt.update(200, 60, mac, metric=512, seq_num=20, hop_count=3)
    rt.update(300, 70, mac, metric=128, seq_num=30, hop_count=1)

    assert rt.count == 3

    # Try to add one more - should raise MemoryError
    with pytest.raises(MemoryError):
        rt.update(400, 80, mac, metric=64, seq_num=40, hop_count=1)

    assert rt.count == 3


def test_routing_table_max_routes_update_existing():
    """Test that updating existing route doesn't count toward limit."""
    from umesh.routing import RoutingTable

    rt = RoutingTable(max_routes=2)
    mac1 = b'\x01\x02\x03\x04\x05\x06'
    mac2 = b'\x0A\x0B\x0C\x0D\x0E\x0F'

    # Add max routes
    rt.update(100, 50, mac1, metric=256, seq_num=10, hop_count=2)
    rt.update(200, 60, mac2, metric=512, seq_num=20, hop_count=3)

    assert rt.count == 2

    # Update existing route should succeed
    result = rt.update(100, 50, mac1, metric=128, seq_num=15, hop_count=2)
    assert result is True
    assert rt.count == 2


if __name__ == '__main__':
    # Run all tests
    pytest.main([__file__, '-v'])
