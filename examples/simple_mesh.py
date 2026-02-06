"""
Simple Mesh Example - Phase 1 MVP
==================================

Demonstrates basic µMesh usage:
- Auto-configuration from MAC address
- HELLO beacon exchange for neighbor discovery
- Broadcast messaging
- Async message reception
- Link quality monitoring

This example shows the simplest possible mesh network setup.

Hardware required:
- 2+ ESP32 boards on the same WiFi channel
- No WiFi connection needed (ESP-NOW operates independently)

Upload this script to multiple ESP32 boards and run simultaneously.
Each node will:
1. Auto-configure node_id from MAC
2. Send periodic HELLO beacons
3. Receive and display messages from neighbors
4. Send periodic broadcast messages
"""

import uasyncio as asyncio
import umesh


async def message_handler(mesh):
    """Handle incoming messages using async iteration."""
    async for src_id, data in mesh:
        try:
            msg = data.decode('utf-8')
            print(f"[MSG] From node {src_id:04x}: {msg}")
        except UnicodeDecodeError:
            print(f"[MSG] From node {src_id:04x}: {data.hex()}")


async def send_periodic(mesh):
    """Send periodic broadcast messages."""
    counter = 0
    while True:
        await asyncio.sleep(15)  # Send every 15 seconds

        msg = f"Hello from {mesh.node_id:04x}, count={counter}"
        success = await mesh.send(0xFFFF, msg.encode('utf-8'))

        if success:
            print(f"[TX] Broadcast sent: {msg}")
        else:
            print(f"[TX] Failed to send broadcast")

        counter += 1


async def print_stats(mesh):
    """Print statistics and neighbor info periodically."""
    while True:
        await asyncio.sleep(30)  # Print every 30 seconds

        print("\n" + "="*60)
        print(f"Node ID: {mesh.node_id:04x}")
        print(f"MAC: {mesh.mac.hex()}")

        # Print statistics
        stats = mesh.stats
        print(f"\nStatistics:")
        print(f"  TX: {stats['tx_packets']} packets, {stats['tx_bytes']} bytes")
        print(f"  RX: {stats['rx_packets']} packets, {stats['rx_bytes']} bytes")
        print(f"  HELLO sent: {stats['hello_sent']}, received: {stats['hello_received']}")
        print(f"  Dropped: {stats['dropped_duplicates']} dups, {stats['dropped_crc_fail']} CRC fails")

        # Print neighbors
        neighbors = mesh.neighbors
        print(f"\nNeighbors ({len(neighbors)}):")
        if neighbors:
            for node_id, mac, rssi, lqi in neighbors:
                print(f"  {node_id:04x} @ {mac.hex()}: RSSI={rssi:4d} dBm, LQI={lqi:3d}/255")
        else:
            print("  (no neighbors discovered yet)")

        print("="*60 + "\n")


async def main():
    """Main entry point."""
    print("="*60)
    print("µMesh Simple Example - Phase 1 MVP")
    print("="*60)

    # Create mesh with auto-configuration
    mesh = umesh.Mesh()

    print(f"Initialized with node_id={mesh.node_id:04x}, MAC={mesh.mac.hex()}")
    print(f"Starting mesh networking...")

    # Start mesh networking
    await mesh.start()

    print("Mesh started. Waiting for neighbors...")
    print("Press Ctrl+C to stop.\n")

    # Create background tasks
    tasks = [
        asyncio.create_task(message_handler(mesh)),
        asyncio.create_task(send_periodic(mesh)),
        asyncio.create_task(print_stats(mesh)),
    ]

    try:
        # Run forever
        await asyncio.gather(*tasks)
    except KeyboardInterrupt:
        print("\nStopping mesh...")
        await mesh.stop()
        print("Stopped.")


# Run the example
if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nExiting.")
