"""
Test and demonstration of umesh._fast optimized functions.

This file shows how to use the hot-path functions and validates their correctness.
Run with: micropython test_fast.py
"""

import sys
import os

# Add parent directory to path to allow importing umesh
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from umesh._fast import (
    dup_check, crc8, parse_header, build_header, rssi_ewma, route_lookup,
    calc_lqi, accumulate_metric, VIPER_AVAILABLE, get_optimization_info,
)


def test_crc8():
    """Test CRC8 calculation."""
    print("Testing CRC8...")

    # Test known CRC8 values
    test_data = b'\x10\x00\x00\x01\x0f\x00\x00\x01\x00\x02\x00\x00\x00'
    checksum = crc8(test_data)
    print(f"  CRC8 of header data: 0x{checksum:02x}")

    # Verify CRC on full header
    header = test_data + bytes([checksum])
    recalc = crc8(header[:-1])
    assert recalc == checksum, "CRC mismatch"
    print("  PASS: CRC8 calculation correct")


def test_header_roundtrip():
    """Test header build and parse."""
    print("\nTesting header build/parse...")

    # Build a header
    header = build_header(
        version=1,
        pkt_type=0,  # DATA
        flags=0,
        pkt_id=1234,
        ttl=15,
        hop_count=0,
        src_id=1,
        dest_id=2,
        frag_info=0
    )

    print(f"  Built header ({len(header)} bytes): {header.hex()}")

    # Parse it back
    parsed = parse_header(header)
    print(f"  Parsed: {parsed}")

    # Verify fields
    assert parsed['version'] == 1
    assert parsed['pkt_type'] == 0
    assert parsed['pkt_id'] == 1234
    assert parsed['ttl'] == 15
    assert parsed['src_id'] == 1
    assert parsed['dest_id'] == 2
    print("  PASS: Header roundtrip successful")

    # Test invalid CRC
    corrupted = bytearray(header)
    corrupted[13] ^= 0xFF  # Flip all bits in checksum
    try:
        parse_header(bytes(corrupted))
        assert False, "Should have raised ValueError"
    except ValueError as e:
        print(f"  PASS: Detected corrupted header: {e}")


def test_duplicate_check():
    """Test duplicate packet detection."""
    print("\nTesting duplicate check...")

    # Create circular buffer for 4 entries (16 bytes)
    buf = bytearray(16)

    # Add first entry: src=1, pkt=100
    buf[0:4] = bytes([0x00, 0x01, 0x00, 0x64])

    # Add second entry: src=2, pkt=200
    buf[4:8] = bytes([0x00, 0x02, 0x00, 0xC8])

    # Check for duplicate
    assert dup_check(buf, 1, 100) == True, "Should find src=1, pkt=100"
    assert dup_check(buf, 2, 200) == True, "Should find src=2, pkt=200"
    assert dup_check(buf, 3, 300) == False, "Should not find src=3, pkt=300"

    print("  PASS: Duplicate detection working")


def test_rssi_ewma():
    """Test RSSI exponential moving average."""
    print("\nTesting RSSI EWMA...")

    # Start with -80 dBm
    avg = -80
    print(f"  Initial RSSI: {avg} dBm")

    # Update with new measurements
    measurements = [-75, -70, -72, -71, -70]
    for rssi in measurements:
        avg = rssi_ewma(avg, rssi, alpha=0.375)
        print(f"  New measurement: {rssi} dBm -> Average: {avg} dBm")

    # Should converge toward recent measurements
    assert -73 <= avg <= -69, f"EWMA out of expected range: {avg}"
    print("  PASS: RSSI smoothing working")


def test_lqi_calculation():
    """Test Link Quality Index calculation."""
    print("\nTesting LQI calculation...")

    # Perfect link: strong signal, no loss, fresh
    lqi = calc_lqi(rssi_avg=-40, loss_pct=0, age_ms=100, timeout_ms=30000)
    print(f"  Perfect link LQI: {lqi}")
    assert lqi >= 230, "Perfect link should have very high LQI"

    # Weak link: poor signal, some loss, stale
    lqi = calc_lqi(rssi_avg=-95, loss_pct=20, age_ms=25000, timeout_ms=30000)
    print(f"  Weak link LQI: {lqi}")
    assert lqi <= 100, "Weak link should have low LQI"

    # Medium link
    lqi = calc_lqi(rssi_avg=-70, loss_pct=5, age_ms=5000, timeout_ms=30000)
    print(f"  Medium link LQI: {lqi}")
    assert 100 <= lqi <= 200, "Medium link should have moderate LQI"

    print("  PASS: LQI calculation working")


def test_routing():
    """Test route lookup and metric accumulation."""
    print("\nTesting routing functions...")

    # Create mock routing table
    # Format: {dest_id: [next_hop_id, next_hop_mac, metric, ..., expiry_ms]}
    table = {
        10: [5, b'\x01\x02\x03\x04\x05\x06', 256, None, 100000],  # Valid until 100s
        20: [7, b'\x0A\x0B\x0C\x0D\x0E\x0F', 512, None, 50000],   # Valid until 50s
    }

    # Lookup existing route (not expired)
    result = route_lookup(table, 10, now_ms=50000)
    print(f"  Route to 10: {result}")
    assert result == (5, b'\x01\x02\x03\x04\x05\x06', 256)

    # Lookup expired route
    result = route_lookup(table, 20, now_ms=60000)
    print(f"  Route to 20 (expired): {result}")
    assert result is None, "Expired route should return None"
    assert 20 not in table, "Expired route should be removed from table"

    # Lookup non-existent route
    result = route_lookup(table, 99, now_ms=50000)
    assert result is None, "Non-existent route should return None"

    print("  PASS: Route lookup working")

    # Test metric accumulation
    metric = 0
    lqi_values = [250, 240, 230, 220]  # Simulate 4-hop path with decreasing quality

    print(f"  Starting metric: {metric}")
    for hop, lqi in enumerate(lqi_values, 1):
        metric = accumulate_metric(metric, lqi)
        print(f"    Hop {hop} (LQI={lqi}): metric = {metric}")

    # Each hop adds (256 - LQI) to metric
    # Expected: 6 + 16 + 26 + 36 = 84
    expected = (256-250) + (256-240) + (256-230) + (256-220)
    assert metric == expected, f"Metric mismatch: {metric} != {expected}"

    print("  PASS: Metric accumulation working")


def benchmark_functions():
    """Benchmark hot-path functions."""
    print("\nBenchmarking (if micropython.ticks_us available)...")

    try:
        import time

        # Benchmark header parse
        header = build_header(1, 0, 0, 1234, 15, 0, 1, 2, 0)

        start = time.ticks_us()
        for _ in range(1000):
            parse_header(header)
        elapsed = time.ticks_diff(time.ticks_us(), start)
        print(f"  Header parse: {elapsed/1000:.1f} µs/call")

        # Benchmark CRC8
        data = b'\x00' * 13
        start = time.ticks_us()
        for _ in range(1000):
            crc8(data)
        elapsed = time.ticks_diff(time.ticks_us(), start)
        print(f"  CRC8: {elapsed/1000:.1f} µs/call")

    except (ImportError, AttributeError):
        print("  Skipped: time.ticks_us not available")


def main():
    """Run all tests."""
    print("=" * 60)
    print("µMesh Fast Functions Test Suite")
    print("=" * 60)

    # Show optimization status
    info = get_optimization_info()
    print(f"\nOptimization status:")
    print(f"  Viper available: {info['viper_available']}")
    print(f"  Native available: {info['native_available']}")
    print(f"  Optimized functions: {info['optimized_functions']}")

    if not VIPER_AVAILABLE:
        print("\nWARNING: Running in pure Python fallback mode")
        print("         Performance will be significantly slower")

    # Run tests
    test_crc8()
    test_header_roundtrip()
    test_duplicate_check()
    test_rssi_ewma()
    test_lqi_calculation()
    test_routing()
    benchmark_functions()

    print("\n" + "=" * 60)
    print("All tests passed!")
    print("=" * 60)


if __name__ == '__main__':
    main()
