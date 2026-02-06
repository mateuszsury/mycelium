"""
Example: Duplicate Packet Detection

Demonstrates the DuplicateFilter class for detecting duplicate packets
in the µMesh protocol. This is critical for preventing packet loops in
mesh networks where packets may be received multiple times via different
routes.

Run with: micropython duplicate_filter_example.py
"""

import sys
sys.path.insert(0, '..')

from umesh.duplicate import DuplicateFilter


def example_basic_usage():
    """Basic usage of DuplicateFilter."""
    print("=" * 60)
    print("Example 1: Basic Usage")
    print("=" * 60)

    dup = DuplicateFilter()

    # Simulate receiving packets
    packets = [
        (0x1234, 0x0001),  # First packet from node 0x1234
        (0x1234, 0x0001),  # Duplicate - same packet received again
        (0x1234, 0x0002),  # New packet from same node
        (0x5678, 0x0001),  # Same packet ID but different source
        (0x1234, 0x0001),  # Duplicate again
    ]

    for src_id, pkt_id in packets:
        is_dup = dup.is_duplicate(src_id, pkt_id)

        if is_dup:
            print(f"  [DROP] Duplicate packet from {src_id:#06x}, pkt_id={pkt_id:#06x}")
        else:
            print(f"  [ACCEPT] New packet from {src_id:#06x}, pkt_id={pkt_id:#06x}")
            dup.record(src_id, pkt_id)

    print()


def example_mesh_simulation():
    """Simulate mesh network with multiple routes."""
    print("=" * 60)
    print("Example 2: Mesh Network Simulation")
    print("=" * 60)
    print("Node topology: A -> B -> C")
    print("              |    |")
    print("              +--> C (alternate path)")
    print()

    dup = DuplicateFilter()

    # Node C receives same packet via two routes
    # Route 1: A -> B -> C (direct)
    # Route 2: A -> C (direct)

    print("Node C receives packet 0x0042 from source A (0xAAAA):")

    # First reception via route 1
    src = 0xAAAA
    pkt = 0x0042

    if not dup.is_duplicate(src, pkt):
        print(f"  Route 1 (A->B->C): ACCEPTED - First time seeing this packet")
        dup.record(src, pkt)

    # Second reception via route 2 (duplicate)
    if dup.is_duplicate(src, pkt):
        print(f"  Route 2 (A->C):    DROPPED - Duplicate packet")

    print()


def example_window_overflow():
    """Demonstrate window size overflow."""
    print("=" * 60)
    print("Example 3: Window Size Overflow")
    print("=" * 60)

    # Small window for demonstration
    dup = DuplicateFilter(window_size=4)

    src = 0x1234
    print(f"Window size: 4 packets per source")
    print(f"Sending 6 packets from node {src:#06x}:")
    print()

    # Send 6 packets (window size is only 4)
    for i in range(6):
        dup.record(src, i)
        print(f"  Sent packet {i}")

    print()
    print("Checking which packets are still in window:")

    for i in range(6):
        is_dup = dup.is_duplicate(src, i)
        status = "IN WINDOW" if is_dup else "EVICTED"
        print(f"  Packet {i}: {status}")

    print()


def example_statistics():
    """Show filter statistics."""
    print("=" * 60)
    print("Example 4: Statistics")
    print("=" * 60)

    dup = DuplicateFilter(window_size=16, max_sources=32)

    # Add some traffic
    for src in range(10):
        for pkt in range(5):
            dup.record(0x1000 + src, pkt)

    stats = dup.get_stats()

    print(f"Filter Statistics:")
    print(f"  Sources tracked:   {stats['source_count']}/{stats['max_sources']}")
    print(f"  Window size:       {stats['window_size']} packets/source")
    print(f"  Memory usage:      {stats['memory_bytes']} bytes")
    print(f"  Oldest source age: {stats['oldest_age_ms']} ms")
    print()


def example_pruning():
    """Demonstrate source pruning."""
    print("=" * 60)
    print("Example 5: Source Pruning")
    print("=" * 60)

    dup = DuplicateFilter()

    # Add some sources
    for src in range(5):
        dup.record(0x1000 + src, 0x0001)

    print(f"Sources before pruning: {dup.source_count}")

    # Prune sources older than 0ms (all of them)
    pruned = dup.prune(max_age_ms=0)

    print(f"Sources pruned: {pruned}")
    print(f"Sources after pruning: {dup.source_count}")
    print()


def example_lru_eviction():
    """Demonstrate LRU eviction at capacity."""
    print("=" * 60)
    print("Example 6: LRU Eviction at Capacity")
    print("=" * 60)

    # Small max_sources for demonstration
    dup = DuplicateFilter(window_size=4, max_sources=3)

    print(f"Max sources: 3")
    print()

    # Fill to capacity
    for src in range(3):
        node_id = 0x1000 + src
        dup.record(node_id, 0x0001)
        print(f"  Added source {node_id:#06x} ({dup.source_count}/3)")

    print()
    print("Adding one more source (triggers LRU eviction):")

    new_node = 0x2000
    dup.record(new_node, 0x0001)
    print(f"  Added source {new_node:#06x} ({dup.source_count}/3)")

    print()
    print("Checking which source was evicted:")

    # First source should be evicted
    if not dup.is_duplicate(0x1000, 0x0001):
        print(f"  Source 0x1000: EVICTED (LRU)")

    if dup.is_duplicate(new_node, 0x0001):
        print(f"  Source {new_node:#06x}: PRESENT")

    print()


def main():
    """Run all examples."""
    print()
    print("#" * 60)
    print("# DuplicateFilter Examples")
    print("#" * 60)
    print()

    example_basic_usage()
    example_mesh_simulation()
    example_window_overflow()
    example_statistics()
    example_pruning()
    example_lru_eviction()

    print("=" * 60)
    print("All examples completed!")
    print("=" * 60)
    print()


if __name__ == '__main__':
    main()
