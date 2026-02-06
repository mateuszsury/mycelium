"""
Example: Using µMesh Diagnostics

Demonstrates how to use the diagnostics module for network monitoring,
troubleshooting, and performance measurement.

Features:
- Ping remote nodes to measure RTT
- Traceroute to discover paths
- Collect and display network statistics
- Monitor neighbors and routes

Usage:
    # On MicroPython/ESP32
    import umesh
    from examples.example_diagnostics import run_diagnostics

    mesh = umesh.Mesh()
    await mesh.start()
    await run_diagnostics(mesh)
"""

try:
    import uasyncio as asyncio
except ImportError:
    import asyncio

import umesh
from umesh.diag import get_mesh_stats, format_stats, format_neighbors, format_routes


async def run_ping_demo(mesh, target_id):
    """Demonstrate ping functionality.

    Args:
        mesh: Mesh instance
        target_id: Target node ID (16-bit)
    """
    print("=" * 60)
    print(f"Pinging node 0x{target_id:04x}...")
    print("=" * 60)

    # Ping with callback
    def on_ping(seq, rtt):
        if rtt is not None:
            print(f"  seq={seq} rtt={rtt}ms")
        else:
            print(f"  seq={seq} timeout")

    rtts = await mesh.diag.ping(
        target_id,
        count=5,
        timeout=2000,
        callback=on_ping
    )

    # Calculate statistics
    valid = [r for r in rtts if r is not None]
    if valid:
        avg_rtt = sum(valid) / len(valid)
        min_rtt = min(valid)
        max_rtt = max(valid)
        loss = ((len(rtts) - len(valid)) / len(rtts)) * 100

        print()
        print(f"Ping statistics for 0x{target_id:04x}:")
        print(f"  Packets: {len(rtts)} sent, {len(valid)} received, {loss:.0f}% loss")
        print(f"  RTT: min={min_rtt}ms, avg={avg_rtt:.1f}ms, max={max_rtt}ms")
    else:
        print()
        print(f"No response from 0x{target_id:04x} (100% packet loss)")

    print()


async def run_traceroute_demo(mesh, target_id):
    """Demonstrate traceroute functionality.

    Args:
        mesh: Mesh instance
        target_id: Target node ID (16-bit)
    """
    print("=" * 60)
    print(f"Traceroute to node 0x{target_id:04x}...")
    print("=" * 60)

    # Traceroute with callback
    def on_hop(hop, node_id, rtt):
        print(f"  {hop}: 0x{node_id:04x} ({rtt}ms)")

    path = await mesh.diag.traceroute(
        target_id,
        max_hops=10,
        timeout=3000,
        callback=on_hop
    )

    if path:
        print()
        print(f"Route to 0x{target_id:04x}: {len(path)} hops")
        total_rtt = sum(rtt for _, _, rtt in path)
        print(f"Total RTT: {total_rtt}ms")
    else:
        print()
        print(f"No route found to 0x{target_id:04x}")

    print()


def show_statistics(mesh):
    """Show comprehensive mesh statistics.

    Args:
        mesh: Mesh instance
    """
    print("=" * 60)
    print("Mesh Statistics")
    print("=" * 60)

    stats = get_mesh_stats(mesh)
    print(format_stats(stats))


def show_neighbors(mesh):
    """Show neighbor table.

    Args:
        mesh: Mesh instance
    """
    print(format_neighbors(mesh))


def show_routes(mesh):
    """Show routing table.

    Args:
        mesh: Mesh instance
    """
    print(format_routes(mesh))


async def monitor_loop(mesh, interval=30):
    """Continuous monitoring loop.

    Periodically displays statistics, neighbors, and routes.

    Args:
        mesh: Mesh instance
        interval: Update interval in seconds (default: 30)
    """
    print("=" * 60)
    print("Starting continuous monitoring...")
    print(f"Update interval: {interval}s")
    print("Press Ctrl+C to stop")
    print("=" * 60)
    print()

    try:
        iteration = 0
        while True:
            iteration += 1

            print(f"\n{'='*60}")
            print(f"Update #{iteration}")
            print(f"{'='*60}\n")

            # Show statistics
            show_statistics(mesh)

            # Show neighbors
            show_neighbors(mesh)

            # Show routes (if routing enabled)
            if hasattr(mesh, '_routes') and mesh._routes:
                show_routes(mesh)

            # Wait for next update
            await asyncio.sleep(interval)

    except KeyboardInterrupt:
        print("\nMonitoring stopped.")


async def run_diagnostics(mesh, target_id=None):
    """Run full diagnostic suite.

    Args:
        mesh: Mesh instance
        target_id: Optional target node ID for ping/traceroute tests
    """
    print()
    print("=" * 60)
    print("µMesh Diagnostics Demo")
    print("=" * 60)
    print()

    # Show initial statistics
    show_statistics(mesh)

    # Show neighbors
    show_neighbors(mesh)

    # Show routes (if routing enabled)
    if hasattr(mesh, '_routes') and mesh._routes:
        show_routes(mesh)

    # Run ping test if target specified
    if target_id is not None:
        await run_ping_demo(mesh, target_id)
        await run_traceroute_demo(mesh, target_id)

    print("=" * 60)
    print("Diagnostics complete")
    print("=" * 60)
    print()


# Example usage in main application
async def main():
    """Example main application with diagnostics."""

    # Create and start mesh
    mesh = umesh.Mesh()
    await mesh.start()

    print(f"Mesh started - Node ID: 0x{mesh.node_id:04x}")
    print()

    # Wait for neighbors to appear
    print("Waiting for neighbors... (10 seconds)")
    await asyncio.sleep(10)

    # Run diagnostics
    neighbors = mesh.neighbors
    if neighbors:
        # Ping first neighbor
        target_id = neighbors[0][0]
        await run_diagnostics(mesh, target_id)
    else:
        # No neighbors, just show stats
        await run_diagnostics(mesh)

    # Optional: Start continuous monitoring
    # await monitor_loop(mesh, interval=30)

    # Keep running
    try:
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        print("\nShutting down...")
        await mesh.stop()


if __name__ == '__main__':
    # This example is for MicroPython/ESP32
    # On desktop Python, it will fail due to missing ESP-NOW hardware
    try:
        asyncio.run(main())
    except Exception as e:
        print(f"Error: {e}")
        print("This example requires MicroPython on ESP32 hardware.")
