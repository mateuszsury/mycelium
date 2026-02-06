"""
Custom Configuration Example
=============================

Demonstrates advanced µMesh configuration:
- Custom node_id assignment
- Channel selection
- Long Range mode (if supported)
- Message callbacks instead of async iteration
- Custom hello intervals

This example shows how to configure mesh parameters explicitly.

Hardware required:
- 2+ ESP32 boards
- ESP32-S2, S3, or C3 recommended for Long Range mode support
"""

import uasyncio as asyncio
import umesh


# Message callback function
def on_message(src_id, data):
    """Called when a DATA packet is received."""
    try:
        msg = data.decode('utf-8')
        print(f"[CALLBACK] From {src_id:04x}: {msg}")
    except UnicodeDecodeError:
        print(f"[CALLBACK] From {src_id:04x}: {data.hex()}")


async def send_loop(mesh):
    """Send messages periodically."""
    counter = 0

    while True:
        await asyncio.sleep(10)

        # Send broadcast
        msg = f"Message #{counter} from {mesh.node_id:04x}"
        success = await mesh.send(0xFFFF, msg.encode('utf-8'))

        if success:
            print(f"[TX] Sent: {msg}")

        counter += 1


async def monitor_neighbors(mesh):
    """Monitor neighbor changes."""
    previous_neighbors = set()

    while True:
        await asyncio.sleep(5)

        # Get current neighbors
        current = {n[0] for n in mesh.neighbors}  # Set of node_ids

        # Check for new neighbors
        new_neighbors = current - previous_neighbors
        for node_id in new_neighbors:
            print(f"[NEIGHBOR] New neighbor discovered: {node_id:04x}")

        # Check for lost neighbors
        lost_neighbors = previous_neighbors - current
        for node_id in lost_neighbors:
            print(f"[NEIGHBOR] Neighbor lost: {node_id:04x}")

        previous_neighbors = current


async def main():
    """Main entry point."""
    print("="*60)
    print("µMesh Custom Configuration Example")
    print("="*60)

    # Create custom configuration
    config = umesh.MeshConfig(
        node_id=0x0042,           # Explicit node ID
        channel=6,                # WiFi channel 6
        lr_mode=True,             # Enable Long Range mode (if supported)
        hello_interval=5000,      # HELLO every 5 seconds (faster discovery)
        route_timeout=60000,      # Routes expire after 60 seconds
        max_ttl=10,               # Allow up to 10 hops
        peer_slots=19,            # Max peer slots
        rx_buf=4096,              # Larger RX buffer
        duplicate_window=64,      # Larger duplicate detection window
        enable_encryption=False,  # No encryption (for simplicity)
    )

    print(f"Configuration: {config}")

    # Create mesh with custom config
    mesh = umesh.Mesh(config)

    print(f"\nNode ID: {mesh.node_id:04x}")
    print(f"MAC: {mesh.mac.hex()}")

    # Register message callback
    mesh.on_message(on_message)
    print("Message callback registered")

    # Start mesh
    print("\nStarting mesh networking...")
    await mesh.start()
    print("Mesh started!\n")

    # Create background tasks
    tasks = [
        asyncio.create_task(send_loop(mesh)),
        asyncio.create_task(monitor_neighbors(mesh)),
    ]

    try:
        # Run forever
        await asyncio.gather(*tasks)
    except KeyboardInterrupt:
        print("\nStopping...")
        await mesh.stop()
        print("Stopped.")


# Run the example
if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nExiting.")
