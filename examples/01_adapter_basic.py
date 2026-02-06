"""
Example 01: Basic ESP-NOW Adapter Usage

Demonstrates basic send/receive with the ESPNowAdapter.
This is the foundation Layer 1 (Physical) of the µMesh protocol.

Hardware: ESP32 (any variant with ESP-NOW support)
MicroPython: >= v1.20

Usage:
    1. Flash two ESP32 boards with MicroPython
    2. Upload the umesh package to both boards
    3. Run this script on both boards
    4. They will discover each other and exchange messages
"""

import uasyncio as asyncio
from umesh.adapter import ESPNowAdapter


async def send_loop(adapter):
    """Periodically broadcast hello messages."""
    count = 0
    while True:
        msg = f"Hello from {adapter.mac.hex()}, count={count}".encode()
        success = await adapter.send_broadcast(msg)
        if success:
            print(f"[TX] Broadcast sent: {msg}")
        else:
            print("[TX] Broadcast failed")

        count += 1
        await asyncio.sleep(5)


async def recv_loop(adapter):
    """Receive and print all incoming messages."""
    print(f"[RX] Listening on channel {adapter.channel}")
    print(f"[RX] My MAC: {adapter.mac.hex()}")

    async for mac, data, rssi in adapter:
        print(f"[RX] From {mac.hex()}: {data.decode('utf-8', errors='ignore')}")
        print(f"     RSSI: {rssi} dBm")


async def main():
    """Main entry point."""
    print("=" * 50)
    print("µMesh Layer 1 - ESP-NOW Adapter Demo")
    print("=" * 50)

    # Initialize adapter on channel 6
    # All nodes must use the same channel
    adapter = ESPNowAdapter(
        channel=6,
        lr_mode=False,  # Set to True for Long Range mode if supported
        rx_buf=2048     # Receive buffer size
    )

    print(f"Adapter initialized")
    print(f"  MAC: {adapter.mac.hex()}")
    print(f"  Channel: {adapter.channel}")
    print(f"  LR Mode: {adapter.lr_mode}")
    print(f"  Peer count: {adapter.peer_count()}")
    print()

    # Run send and receive concurrently
    try:
        await asyncio.gather(
            send_loop(adapter),
            recv_loop(adapter)
        )
    except KeyboardInterrupt:
        print("\nShutting down...")
        adapter.deinit()


# Run the example
if __name__ == "__main__":
    asyncio.run(main())
