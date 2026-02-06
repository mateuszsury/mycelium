"""
Tests for duplicate packet detection

Run with: micropython test_duplicate.py
Or with pytest: pytest test_duplicate.py
"""

try:
    from utime import sleep_ms
except ImportError:
    from time import sleep
    def sleep_ms(ms):
        sleep(ms / 1000.0)

from duplicate import DuplicateFilter, WINDOW_SIZE, MAX_SOURCES


def test_basic_duplicate_detection():
    """Test basic duplicate detection."""
    print("Test: Basic duplicate detection...")

    dup = DuplicateFilter()

    # First packet from source 0x1234
    assert not dup.is_duplicate(0x1234, 0x0001), "First packet should not be duplicate"
    dup.record(0x1234, 0x0001)
    assert dup.is_duplicate(0x1234, 0x0001), "Same packet should be duplicate"

    # Different packet from same source
    assert not dup.is_duplicate(0x1234, 0x0002), "Different packet should not be duplicate"
    dup.record(0x1234, 0x0002)
    assert dup.is_duplicate(0x1234, 0x0002), "Newly recorded packet should be duplicate"

    # Same packet ID from different source
    assert not dup.is_duplicate(0x5678, 0x0001), "Same packet ID from different source should not be duplicate"

    print("  [PASS] Basic duplicate detection works")


def test_window_size():
    """Test that window size is respected."""
    print("Test: Window size...")

    window_size = 4
    dup = DuplicateFilter(window_size=window_size, max_sources=10)

    src_id = 0x1234

    # Record window_size packets
    for i in range(window_size):
        assert not dup.is_duplicate(src_id, i)
        dup.record(src_id, i)
        assert dup.is_duplicate(src_id, i)

    # All should still be in window
    for i in range(window_size):
        assert dup.is_duplicate(src_id, i), f"Packet {i} should still be in window"

    # Record one more - should evict oldest (packet 0)
    dup.record(src_id, window_size)

    # Packet 0 should now be evicted (circular buffer wrapped)
    # But we need to fill entire window again to fully evict
    for i in range(window_size, window_size * 2):
        dup.record(src_id, i)

    # Now packet 0 should definitely be gone
    assert not dup.is_duplicate(src_id, 0), "Oldest packet should be evicted from window"

    print("  [PASS] Window size respected")


def test_multiple_sources():
    """Test tracking multiple sources simultaneously."""
    print("Test: Multiple sources...")

    dup = DuplicateFilter()

    # Record packets from 10 different sources
    for src in range(10):
        for pkt in range(5):
            node_id = 0x1000 + src
            pkt_id = 0x0100 + pkt
            assert not dup.is_duplicate(node_id, pkt_id)
            dup.record(node_id, pkt_id)
            assert dup.is_duplicate(node_id, pkt_id)

    # Verify all are still tracked
    assert dup.source_count == 10

    # Verify cross-contamination doesn't occur
    assert dup.is_duplicate(0x1000, 0x0100)  # Should be present
    assert not dup.is_duplicate(0x1000, 0x0200)  # Different packet (0x200 > 0x104)
    assert dup.is_duplicate(0x1009, 0x0100)  # This WAS recorded (src=9, pkt=0)
    assert not dup.is_duplicate(0x1009, 0x0200)  # Different packet

    print("  [PASS] Multiple sources tracked correctly")


def test_max_sources_eviction():
    """Test LRU eviction when MAX_SOURCES reached."""
    print("Test: MAX_SOURCES eviction...")

    max_sources = 5
    dup = DuplicateFilter(window_size=4, max_sources=max_sources)

    # Fill to capacity
    for src in range(max_sources):
        dup.record(0x1000 + src, 0x0001)

    assert dup.source_count == max_sources

    # Add one more source - should evict LRU (first source)
    sleep_ms(10)  # Small delay to ensure timestamp differs
    dup.record(0x2000, 0x0001)

    assert dup.source_count == max_sources, "Should stay at max_sources"

    # First source (0x1000) should be evicted
    assert not dup.is_duplicate(0x1000, 0x0001), "LRU source should be evicted"
    assert dup.is_duplicate(0x2000, 0x0001), "New source should be present"

    print("  [PASS] LRU eviction works")


def test_prune():
    """Test pruning of old sources."""
    print("Test: Pruning...")

    dup = DuplicateFilter()

    # Record some packets
    dup.record(0x1234, 0x0001)
    dup.record(0x5678, 0x0002)

    assert dup.source_count == 2

    # Prune with very short age - nothing should be removed
    pruned = dup.prune(max_age_ms=100_000)
    assert pruned == 0
    assert dup.source_count == 2

    # Prune with age=0 - everything should be removed
    sleep_ms(10)
    pruned = dup.prune(max_age_ms=0)
    assert pruned == 2
    assert dup.source_count == 0

    print("  [PASS] Pruning works")


def test_clear():
    """Test clearing all entries."""
    print("Test: Clear...")

    dup = DuplicateFilter()

    # Add some entries
    for src in range(5):
        for pkt in range(3):
            dup.record(0x1000 + src, pkt)

    assert dup.source_count == 5

    # Clear
    dup.clear()
    assert dup.source_count == 0

    # Everything should be fresh again
    assert not dup.is_duplicate(0x1000, 0)

    print("  [PASS] Clear works")


def test_stats():
    """Test statistics reporting."""
    print("Test: Statistics...")

    dup = DuplicateFilter(window_size=16, max_sources=32)

    # Empty stats
    stats = dup.get_stats()
    assert stats['source_count'] == 0
    assert stats['max_sources'] == 32
    assert stats['window_size'] == 16
    assert stats['memory_bytes'] == 0

    # Add some sources
    for src in range(5):
        dup.record(0x1000 + src, 0x0001)

    stats = dup.get_stats()
    assert stats['source_count'] == 5
    assert stats['memory_bytes'] == 5 * 16 * 4  # 5 sources * 16 window * 4 bytes

    print("  [PASS] Statistics correct")


def test_edge_cases():
    """Test edge cases."""
    print("Test: Edge cases...")

    dup = DuplicateFilter()

    # Node ID 0
    assert not dup.is_duplicate(0, 0)
    dup.record(0, 0)
    assert dup.is_duplicate(0, 0)

    # Max node ID (0xFFFF)
    assert not dup.is_duplicate(0xFFFF, 0xFFFF)
    dup.record(0xFFFF, 0xFFFF)
    assert dup.is_duplicate(0xFFFF, 0xFFFF)

    # Same source, incrementing packet IDs
    for pkt_id in range(100):
        result = dup.is_duplicate(0x1234, pkt_id)
        dup.record(0x1234, pkt_id)
        # After recording once, should be duplicate if checked again
        assert dup.is_duplicate(0x1234, pkt_id)

    print("  [PASS] Edge cases handled")


def test_circular_buffer():
    """Test that circular buffer works correctly."""
    print("Test: Circular buffer...")

    window_size = 8
    dup = DuplicateFilter(window_size=window_size)

    src_id = 0xABCD

    # Fill buffer exactly once
    for i in range(window_size):
        dup.record(src_id, i)

    # All should be present
    for i in range(window_size):
        assert dup.is_duplicate(src_id, i)

    # Now wrap around - add window_size more packets
    for i in range(window_size, window_size * 2):
        dup.record(src_id, i)

    # First window_size packets should be gone
    for i in range(window_size):
        assert not dup.is_duplicate(src_id, i), f"Packet {i} should be evicted"

    # Second window_size packets should be present
    for i in range(window_size, window_size * 2):
        assert dup.is_duplicate(src_id, i), f"Packet {i} should be in buffer"

    print("  [PASS] Circular buffer works correctly")


def test_performance():
    """Test performance characteristics."""
    print("Test: Performance...")

    dup = DuplicateFilter()

    # Simulate realistic usage: 50 packets/sec for 10 seconds
    # From 10 different sources
    num_sources = 10
    packets_per_source = 50

    for src in range(num_sources):
        src_id = 0x1000 + src
        for pkt in range(packets_per_source):
            # Check for duplicate (should be fast)
            is_dup = dup.is_duplicate(src_id, pkt)
            # Record
            dup.record(src_id, pkt)

    # Verify state
    assert dup.source_count == num_sources

    stats = dup.get_stats()
    print(f"  Stats after {num_sources * packets_per_source} packets:")
    print(f"    Sources: {stats['source_count']}")
    print(f"    Memory: {stats['memory_bytes']} bytes")

    print("  [PASS] Performance test completed")


def run_all_tests():
    """Run all tests."""
    print("\n" + "="*60)
    print("Running DuplicateFilter Tests")
    print("="*60 + "\n")

    test_basic_duplicate_detection()
    test_window_size()
    test_multiple_sources()
    test_max_sources_eviction()
    test_prune()
    test_clear()
    test_stats()
    test_edge_cases()
    test_circular_buffer()
    test_performance()

    print("\n" + "="*60)
    print("All tests passed! [PASS]")
    print("="*60 + "\n")


if __name__ == '__main__':
    run_all_tests()
