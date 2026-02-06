"""
Example 02: Unicast Communication with ESP-NOW Adapter

Demonstrates peer registration and unicast (point-to-point) communication.
Shows the difference between broadcast and unicast send behavior.

Hardware: 2x ESP32 boards
MicroPython: >= v1.20

Setup:
    1. Set TARGET_MAC below to the MAC address of the other board
    2. Upload to both boards (swap the TARGET_MAC for each board)
    3. Run on both boards simultaneously
"""

import uasyncio as asyncio
from umesh.adapter import ESPNowAdapter

# CONFIGURE THIS: Set to the MAC address of the peer ESP32
# Get MAC from the other board by running 01_adapter_basic.py
TARGET_MAC = b'\xaa\xbb\xcc\xdd\xee\xff'  # Example - REPLACE THIS


async def send_unicast_loop(adapter, target_mac):
    """Send unicast messages to specific peer."""
    # First, register the peer
    success = adapter.add_peer(target_mac)
    if not success:
        print(f"[ERROR] Failed to register peer {target_mac.hex()}")
        print("        Peer table might be full (max 19 unicast peers)")
        return

    print(f"[PEER] Registered peer: {target_mac.hex()}")
    print(f"[PEER] Total peers: {adapter.peer_count()}")

    count = 0
    while True:
        # Unicast message
        msg = f"Unicast #{count} from {adapter.mac.hex()}".encode()
        success = await adapter.send_unicast(target_mac, msg)

        if success:
            print(f"[TX-UNI] Sent to {target_mac.hex()[:12]}...: {msg}")
            print(f"         ACK received from next hop")
        else:
            print(f"[TX-UNI] Failed to {target_mac.hex()[:12]}... (no ACK)")

        count += 1
        await asyncio.sleep(3)


async def send_broadcast_loop(adapter):
    """Also send some broadcasts for comparison."""
    await asyncio.sleep(1)  # Offset from unicast

    count = 0
    while True:
        msg = f"Broadcast #{count}".encode()
        await adapter.send_broadcast(msg)
        print(f"[TX-BRD] Broadcast sent: {msg}")
        print(f"         (no ACK - fire and forget)")

        count += 1
        await asyncio.sleep(10)


async def recv_loop(adapter):
    """Receive all messages (both broadcast and unicast)."""
    print(f"[RX] My MAC: {adapter.mac.hex()}")
    print(f"[RX] Waiting for messages...")
    print()

    async for mac, data, rssi in adapter:
        sender = mac.hex()
        msg = data.decode('utf-8', errors='ignore')

        # Determine if this was unicast or broadcast
        msg_type = "BROADCAST" if "Broadcast" in msg else "UNICAST"

        print(f"[RX-{msg_type}] From {sender[:12]}...")
        print(f"               Message: {msg}")
        print(f"               RSSI: {rssi} dBm")
        print()


async def main():
    """Main entry point."""
    print("=" * 60)
    print("µMesh Layer 1 - Unicast Communication Demo")
    print("=" * 60)

    # Check if TARGET_MAC is configured
    if TARGET_MAC == b'\xaa\xbb\xcc\xdd\xee\xff':
        print("ERROR: Please configure TARGET_MAC with your peer's MAC address!")
        print()
        print("Steps:")
        print("1. Run 01_adapter_basic.py on both boards to get their MACs")
        print("2. Update TARGET_MAC in this file for each board")
        print("3. Re-run this example")
        return

    # Initialize adapter
    adapter = ESPNowAdapter(channel=6)

    print(f"My MAC: {adapter.mac.hex()}")
    print(f"Target: {TARGET_MAC.hex()}")
    print()

    # Run all tasks concurrently
    try:
        await asyncio.gather(
            send_unicast_loop(adapter, TARGET_MAC),
            send_broadcast_loop(adapter),
            recv_loop(adapter)
        )
    except KeyboardInterrupt:
        print("\nShutting down...")

        # Cleanup: remove peer before exit
        adapter.del_peer(TARGET_MAC)
        adapter.deinit()


if __name__ == "__main__":
    asyncio.run(main())
