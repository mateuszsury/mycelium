"""
NeighborTable Usage Example
============================

Demonstrates how to use NeighborTable for tracking direct neighbors,
calculating Link Quality Index (LQI), and selecting best neighbors.

This example shows typical patterns used in mesh routing:
1. Updating neighbor info from HELLO beacons
2. Querying link quality for route selection
3. Pruning dead neighbors
4. Finding best neighbors for multi-path routing
"""

import time
import sys
import os

# Add parent directory to path for imports
parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, parent_dir)

from umesh.neighbor import NeighborTable
from umesh.const import NEIGHBOR_TIMEOUT, HELLO_INTERVAL


def simulate_neighbor_discovery():
    """Simulate discovering neighbors via HELLO beacons."""
    print("=" * 60)
    print("Neighbor Discovery Example")
    print("=" * 60)
    print()

    # Create neighbor table with default settings
    table = NeighborTable(
        timeout=NEIGHBOR_TIMEOUT,      # 60 seconds
        hello_interval=HELLO_INTERVAL, # 10 seconds
        max_neighbors=64
    )

    print(f"Initial state: {table.count} neighbors")
    print()

    # Simulate receiving HELLO beacons from various neighbors
    neighbors = [
        # (MAC, node_id, RSSI, hop_count)
        (b'\xaa\xbb\xcc\xdd\xee\x01', 0x0001, -45, 0),  # Very close, strong signal
        (b'\xaa\xbb\xcc\xdd\xee\x02', 0x0002, -60, 0),  # Good signal
        (b'\xaa\xbb\xcc\xdd\xee\x03', 0x0003, -75, 0),  # Medium signal
        (b'\xaa\xbb\xcc\xdd\xee\x04', 0x0004, -90, 0),  # Weak signal
        (b'\xaa\xbb\xcc\xdd\xee\x05', 0x0005, -55, 1),  # 2-hop neighbor
    ]

    print("Receiving HELLO beacons from neighbors:")
    for mac, node_id, rssi, hop_count in neighbors:
        is_new = table.update(mac, node_id, rssi, hop_count)
        status = "NEW" if is_new else "UPDATE"
        print(f"  [{status}] Node 0x{node_id:04X}: RSSI={rssi}dBm, hops={hop_count}")

    print()
    print(f"Total neighbors: {table.count}")
    print()


def demonstrate_lqi_calculation(table):
    """Show Link Quality Index calculation for all neighbors."""
    print("=" * 60)
    print("Link Quality Index (LQI) Calculation")
    print("=" * 60)
    print()

    print("LQI formula: 60% RSSI + 30% reliability + 10% freshness")
    print()

    all_neighbors = table.all_neighbors()

    for node_id in all_neighbors:
        info = table.get_neighbor(node_id)
        print(f"Node 0x{node_id:04X}:")
        print(f"  RSSI avg:  {info['rssi_avg']:4d} dBm")
        print(f"  LQI:       {info['lqi']:3d}/255")
        print(f"  Loss rate: {info['loss_pct']:3d}%")
        print(f"  Hop count: {info['hop_count']}")
        print(f"  Age:       {info['age_ms']:6d} ms")
        print()


def demonstrate_best_neighbor_selection(table):
    """Show selection of best neighbors for routing."""
    print("=" * 60)
    print("Best Neighbor Selection")
    print("=" * 60)
    print()

    # Get top 3 neighbors by LQI
    best = table.get_best_neighbors(3)

    print("Top 3 neighbors for routing (sorted by LQI):")
    for i, (node_id, mac, lqi) in enumerate(best, 1):
        info = table.get_neighbor(node_id)
        print(f"{i}. Node 0x{node_id:04X}")
        print(f"   MAC:       {mac.hex(':')}")
        print(f"   LQI:       {lqi}/255")
        print(f"   RSSI:      {info['rssi_avg']} dBm")
        print(f"   Hop count: {info['hop_count']}")
        print()


def demonstrate_rssi_smoothing(table):
    """Show RSSI smoothing using EWMA."""
    print("=" * 60)
    print("RSSI Smoothing (EWMA)")
    print("=" * 60)
    print()

    # Get node with medium signal
    node_id = 0x0003
    mac = table.get_mac(node_id)

    print(f"Node 0x{node_id:04X} - Simulating RSSI fluctuations:")
    print()

    # Simulate fluctuating RSSI readings
    rssi_readings = [-75, -65, -80, -70, -72, -68, -74]

    for i, new_rssi in enumerate(rssi_readings):
        info_before = table.get_neighbor(node_id)
        rssi_before = info_before['rssi_avg']

        table.update(mac, node_id, new_rssi)

        info_after = table.get_neighbor(node_id)
        rssi_after = info_after['rssi_avg']

        print(f"  Reading {i+1}: new={new_rssi:4d} dBm, "
              f"avg_before={rssi_before:4d} dBm, avg_after={rssi_after:4d} dBm")

    print()
    print("Notice how the average RSSI smooths out sudden fluctuations")
    print("Alpha=0.375 means 37.5% new value, 62.5% old average")
    print()


def demonstrate_dead_neighbor_pruning():
    """Show pruning of dead neighbors."""
    print("=" * 60)
    print("Dead Neighbor Pruning")
    print("=" * 60)
    print()

    # Create table with short timeout for demonstration
    table = NeighborTable(timeout=2000, hello_interval=1000)  # 2 second timeout

    # Add some neighbors
    mac1 = b'\xaa\xbb\xcc\xdd\xee\x01'
    mac2 = b'\xaa\xbb\xcc\xdd\xee\x02'
    table.update(mac1, 0x0001, -60)
    table.update(mac2, 0x0002, -70)

    print(f"Added 2 neighbors: {table.count} total")

    # Update only one neighbor
    print("Waiting 1 second...")
    time.sleep(1)
    table.update(mac1, 0x0001, -62)
    print("Updated node 0x0001 (keeping it alive)")

    # Wait for second neighbor to timeout
    print("Waiting 2 more seconds...")
    time.sleep(2)

    # Prune dead neighbors
    pruned = table.prune_dead()

    print(f"Pruned {len(pruned)} dead neighbors: {['0x{:04X}'.format(n) for n in pruned]}")
    print(f"Remaining neighbors: {table.count}")
    print()


def demonstrate_mac_lookup(table):
    """Show MAC address lookups."""
    print("=" * 60)
    print("MAC Address Lookups")
    print("=" * 60)
    print()

    # Look up by node ID
    node_id = 0x0001
    mac = table.get_mac(node_id)
    print(f"Node 0x{node_id:04X} has MAC: {mac.hex(':')}")

    # Look up by MAC
    info = table.get_by_mac(mac)
    print(f"MAC {mac.hex(':')} is node 0x{info['rssi_avg']:04X} with LQI={info['lqi']}")
    print()


def main():
    """Run all demonstration examples."""
    print("\n")
    print("*" * 60)
    print("*" + " " * 58 + "*")
    print("*" + "  NeighborTable - Mesh Neighbor Management".center(58) + "*")
    print("*" + " " * 58 + "*")
    print("*" * 60)
    print("\n")

    # Example 1: Neighbor discovery
    simulate_neighbor_discovery()

    # Re-create table for other examples
    table = NeighborTable()
    neighbors = [
        (b'\xaa\xbb\xcc\xdd\xee\x01', 0x0001, -45, 0),
        (b'\xaa\xbb\xcc\xdd\xee\x02', 0x0002, -60, 0),
        (b'\xaa\xbb\xcc\xdd\xee\x03', 0x0003, -75, 0),
        (b'\xaa\xbb\xcc\xdd\xee\x04', 0x0004, -90, 0),
        (b'\xaa\xbb\xcc\xdd\xee\x05', 0x0005, -55, 1),
    ]
    for mac, node_id, rssi, hop_count in neighbors:
        table.update(mac, node_id, rssi, hop_count)

    # Example 2: LQI calculation
    demonstrate_lqi_calculation(table)

    # Example 3: Best neighbor selection
    demonstrate_best_neighbor_selection(table)

    # Example 4: RSSI smoothing
    demonstrate_rssi_smoothing(table)

    # Example 5: MAC lookup
    demonstrate_mac_lookup(table)

    # Example 6: Dead neighbor pruning (separate table with short timeout)
    demonstrate_dead_neighbor_pruning()

    print("=" * 60)
    print("All examples completed successfully!")
    print("=" * 60)
    print()


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrupted by user")
        sys.exit(0)
