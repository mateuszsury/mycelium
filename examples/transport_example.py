"""
Transport Layer Example
=======================

Demonstrates fragmentation, reassembly, and reliable delivery in µMesh.

This example shows:
1. Sending large messages with automatic fragmentation
2. Reliable delivery with ACK and retransmission
3. Reassembling received fragments
4. Fire-and-forget unreliable send for low-priority data

Hardware: ESP32 with MicroPython
"""

import sys
sys.path.insert(0, '../')

try:
    import uasyncio as asyncio
except ImportError:
    import asyncio

from umesh import Mesh, MeshConfig
from umesh.transport import ReliableTransport, Reassembler
from umesh.const import PKT_DATA_FRAG


async def sender_example():
    """
    Sender node - demonstrates reliable and unreliable sends.
    """
    print("=== TRANSPORT SENDER EXAMPLE ===\n")

    # Create mesh
    config = MeshConfig(
        channel=6,
        hello_interval=5000,  # 5s hello interval
    )
    mesh = Mesh(config)
    await mesh.start()

    print(f"Node started: {mesh.node_id:04x}")
    print(f"MAC: {mesh.mac.hex(':')}\n")

    # Create reliable transport
    transport = ReliableTransport(mesh)

    # Wait for neighbors to discover each other
    print("Waiting 10s for neighbor discovery...")
    await asyncio.sleep(10)

    # Get neighbors
    neighbors = mesh.neighbors
    if not neighbors:
        print("No neighbors found! Start another node.")
        return

    print(f"Found {len(neighbors)} neighbors:")
    for node_id, mac, rssi, lqi in neighbors:
        print(f"  - {node_id:04x}: RSSI={rssi} dBm, LQI={lqi}")

    # Pick first neighbor as destination
    dest_id = neighbors[0][0]
    print(f"\nSending to neighbor: {dest_id:04x}\n")

    # Example 1: Small message with reliable delivery
    print("Example 1: Small reliable message")
    data = b"Hello from reliable transport!"
    success = await transport.send_reliable(dest_id, data, timeout=1000)
    if success:
        print(f"  ✓ Delivered (ACK received)")
    else:
        print(f"  ✗ Failed after retries")

    await asyncio.sleep(2)

    # Example 2: Large message requiring fragmentation
    print("\nExample 2: Large message (1000 bytes)")
    large_data = b"X" * 1000  # Requires 5 fragments
    success = await transport.send_reliable(dest_id, large_data, timeout=2000)
    if success:
        print(f"  ✓ Delivered (fragmented into ~5 packets)")
    else:
        print(f"  ✗ Failed after retries")

    await asyncio.sleep(2)

    # Example 3: Very large message (near max)
    print("\nExample 3: Maximum size message (3540 bytes)")
    max_data = b"M" * 3540  # Requires 15 fragments
    success = await transport.send_reliable(dest_id, max_data, timeout=3000)
    if success:
        print(f"  ✓ Delivered (15 fragments)")
    else:
        print(f"  ✗ Failed after retries")

    await asyncio.sleep(2)

    # Example 4: Unreliable send (fire and forget)
    print("\nExample 4: Unreliable send (no ACK)")
    data = b"Fire and forget message"
    success = await transport.send_unreliable(dest_id, data)
    print(f"  → Sent ({success})")

    # Keep running
    print("\nPress Ctrl+C to exit...")
    try:
        while True:
            await asyncio.sleep(10)
    except KeyboardInterrupt:
        await mesh.stop()
        print("\nStopped.")


async def receiver_example():
    """
    Receiver node - demonstrates reassembly and ACK handling.
    """
    print("=== TRANSPORT RECEIVER EXAMPLE ===\n")

    # Create mesh
    config = MeshConfig(
        channel=6,
        hello_interval=5000,
    )
    mesh = Mesh(config)
    await mesh.start()

    print(f"Node started: {mesh.node_id:04x}")
    print(f"MAC: {mesh.mac.hex(':')}\n")

    # Create reassembler and transport
    reassembler = Reassembler()
    transport = ReliableTransport(mesh)

    # Message counter
    message_count = 0

    # Register message handler
    def on_message(src_id, data):
        nonlocal message_count
        message_count += 1
        print(f"\n[{message_count}] From {src_id:04x}: {len(data)} bytes")

        # Show first 50 bytes
        preview = data[:50]
        print(f"    Data: {preview}")
        if len(data) > 50:
            print(f"    ... (+ {len(data) - 50} more bytes)")

    mesh.on_message(on_message)

    print("Listening for messages...")
    print("This node will automatically:")
    print("  - Reassemble fragmented messages")
    print("  - Send ACKs for reliable delivery")
    print("  - Handle out-of-order fragments\n")

    # Maintenance loop
    async def maintenance():
        while True:
            await asyncio.sleep(30)

            # Prune stale reassemblies
            pruned = reassembler.prune_stale()
            if pruned > 0:
                print(f"[Maintenance] Pruned {pruned} stale reassemblies")

            # Show stats
            stats = mesh.stats
            pending = reassembler.pending_count
            print(f"[Stats] RX: {stats['rx_packets']} pkts, "
                  f"{stats['rx_bytes']} bytes, "
                  f"Pending reassemblies: {pending}")

    # Start maintenance task
    maint_task = asyncio.create_task(maintenance())

    # Keep running
    print("Press Ctrl+C to exit...\n")
    try:
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        maint_task.cancel()
        await mesh.stop()
        print("\nStopped.")


async def interactive_example():
    """
    Interactive example - send messages via console input.
    """
    print("=== INTERACTIVE TRANSPORT EXAMPLE ===\n")

    # Create mesh
    mesh = Mesh()
    await mesh.start()

    print(f"Node ID: {mesh.node_id:04x}")
    print(f"MAC: {mesh.mac.hex(':')}\n")

    transport = ReliableTransport(mesh)

    # Wait for neighbors
    print("Waiting for neighbors...")
    await asyncio.sleep(5)

    neighbors = mesh.neighbors
    if not neighbors:
        print("No neighbors found!")
        return

    print(f"Found {len(neighbors)} neighbors:")
    for i, (node_id, mac, rssi, lqi) in enumerate(neighbors):
        print(f"  [{i}] {node_id:04x}: RSSI={rssi}, LQI={lqi}")

    print("\nUsage:")
    print("  /send <index> <message>  - Send reliable")
    print("  /blast <index> <message> - Send unreliable")
    print("  /neighbors               - Show neighbors")
    print("  /stats                   - Show statistics")
    print("  /quit                    - Exit")
    print()

    try:
        while True:
            # Simulate console input (in real use, read from REPL)
            cmd = "/send 0 Test message"  # Example command

            if cmd.startswith("/send "):
                parts = cmd.split(" ", 2)
                idx = int(parts[1])
                msg = parts[2].encode()

                dest_id = neighbors[idx][0]
                print(f"Sending to {dest_id:04x}...")
                success = await transport.send_reliable(dest_id, msg)
                print(f"  {'✓' if success else '✗'}")

            elif cmd.startswith("/blast "):
                parts = cmd.split(" ", 2)
                idx = int(parts[1])
                msg = parts[2].encode()

                dest_id = neighbors[idx][0]
                await transport.send_unreliable(dest_id, msg)
                print(f"  → Sent")

            await asyncio.sleep(5)  # Simulate periodic input

    except KeyboardInterrupt:
        await mesh.stop()
        print("\nStopped.")


# =============================================================================
# MAIN ENTRY POINT
# =============================================================================

def main():
    """
    Main entry point - choose sender or receiver mode.

    Usage on ESP32:
        import examples.transport_example as te

        # On first node:
        te.run_receiver()

        # On second node:
        te.run_sender()
    """
    import sys

    if len(sys.argv) > 1:
        mode = sys.argv[1]
    else:
        print("Usage: python transport_example.py [sender|receiver|interactive]")
        print("Defaulting to receiver mode...")
        mode = "receiver"

    if mode == "sender":
        asyncio.run(sender_example())
    elif mode == "receiver":
        asyncio.run(receiver_example())
    elif mode == "interactive":
        asyncio.run(interactive_example())
    else:
        print(f"Unknown mode: {mode}")


def run_sender():
    """Helper for ESP32 MicroPython REPL."""
    asyncio.run(sender_example())


def run_receiver():
    """Helper for ESP32 MicroPython REPL."""
    asyncio.run(receiver_example())


if __name__ == "__main__":
    main()
