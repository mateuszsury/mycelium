"""
Forwarding Engine Example
==========================

Demonstrates multi-hop packet forwarding with the µMesh protocol.

This example shows:
1. Setting up a mesh node with forwarding engine
2. Processing forwarded packets
3. Monitoring forwarding statistics
4. Integration with routing table (Phase 2)

Requirements:
- MicroPython ESP32 with ESP-NOW support
- umesh package installed
"""

try:
    import uasyncio as asyncio
except ImportError:
    import asyncio

from umesh import Mesh, MeshConfig
from umesh.forward import ForwardingEngine
from umesh.const import BROADCAST_ID


async def main():
    """
    Main example demonstrating forwarding engine.
    """

    # Create mesh with custom config
    config = MeshConfig(
        channel=6,
        ttl=7,  # Allow up to 7 hops
        hello_interval=10_000,  # 10 seconds
    )

    mesh = Mesh(config)

    print(f"Node ID: {mesh.node_id:04x}")
    print(f"MAC: {mesh.mac.hex(':')}")

    # Create forwarding engine (Phase 1: no routing yet)
    # In Phase 2+, pass routing_table and route_discovery
    forward_engine = ForwardingEngine(
        mesh,
        routing_table=None,  # TODO: Add RoutingTable in Phase 2
        route_discovery=None  # TODO: Add RouteDiscovery in Phase 2
    )

    # Register message callback
    def on_message(src_id, data):
        """Handle received messages."""
        print(f"\n[MSG] From {src_id:04x}: {data.decode()}")

    mesh.on_message(on_message)

    # Start mesh networking
    print("\nStarting mesh...")
    await mesh.start()
    print("Mesh started!")

    # Send some test messages
    print("\nSending broadcast messages...")

    for i in range(5):
        msg = f"Broadcast {i} from {mesh.node_id:04x}"
        await mesh.send(BROADCAST_ID, msg.encode())
        print(f"  Sent: {msg}")
        await asyncio.sleep(2)

    # Monitor neighbors and statistics
    print("\n" + "=" * 60)
    print("Network Status")
    print("=" * 60)

    # Display neighbors
    neighbors = mesh.neighbors
    print(f"\nNeighbors ({len(neighbors)}):")
    for node_id, mac, rssi, lqi in neighbors:
        print(f"  {node_id:04x} | MAC: {mac.hex(':')} | RSSI: {rssi:4d} dBm | LQI: {lqi:3d}")

    # Display statistics
    stats = mesh.stats
    print(f"\nStatistics:")
    print(f"  TX Packets: {stats['tx_packets']}")
    print(f"  RX Packets: {stats['rx_packets']}")
    print(f"  TX Bytes:   {stats['tx_bytes']}")
    print(f"  RX Bytes:   {stats['rx_bytes']}")
    print(f"  HELLO Sent: {stats['hello_sent']}")
    print(f"  HELLO Recv: {stats['hello_received']}")
    print(f"  Duplicates: {stats['dropped_duplicates']}")
    print(f"  CRC Fails:  {stats['dropped_crc_fail']}")

    # Run for 60 seconds
    print("\nMonitoring network for 60 seconds...")
    await asyncio.sleep(60)

    # Stop mesh
    print("\nStopping mesh...")
    await mesh.stop()
    print("Mesh stopped.")


# Example: Custom packet processing with forwarding engine
async def example_custom_forwarding():
    """
    Example showing manual packet processing with forwarding engine.
    """

    mesh = Mesh()
    forward_engine = ForwardingEngine(mesh, None, None)

    await mesh.start()

    # Simulate receiving a packet and forwarding it
    # (In real usage, this is called from mesh._rx_loop)

    # This is just for demonstration - in production,
    # the forwarding engine is integrated into mesh._process_packet()

    print("Custom forwarding example - see forward.py for integration")

    await mesh.stop()


# Example: Pending queue management
def example_pending_queue():
    """
    Example showing pending queue operations.
    """

    print("\nPending Queue Example:")
    print("=" * 60)

    mesh = Mesh()
    forward_engine = ForwardingEngine(mesh, None, None)

    # Queue would be populated when:
    # 1. Unicast packet received for unknown destination
    # 2. No route available in routing table
    # 3. Route discovery triggered

    # Check pending queue size
    print(f"Pending packets: {len(forward_engine._pending)}")

    # Prune old pending packets (called by maintenance loop)
    forward_engine.prune_pending()
    print(f"After prune:     {len(forward_engine._pending)}")

    # Discard pending for specific destination
    # (called when route discovery fails)
    dest_id = 0x1234
    forward_engine.discard_pending(dest_id)
    print(f"Discarded pending for {dest_id:04x}")

    # Note: flush_pending() is async and called when route is found
    # await forward_engine.flush_pending(dest_id)


if __name__ == '__main__':
    try:
        # Run main example
        asyncio.run(main())

        # Uncomment to run other examples:
        # asyncio.run(example_custom_forwarding())
        # example_pending_queue()

    except KeyboardInterrupt:
        print("\n\nInterrupted by user")
    except Exception as e:
        print(f"\nError: {e}")
        import sys
        import traceback
        traceback.print_exc()
        sys.exit(1)
