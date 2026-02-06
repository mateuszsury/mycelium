"""
µMesh Pub/Sub Example
=====================

Demonstrates publish/subscribe messaging over mesh network.

This example shows:
- Creating PubSub instance with Mesh
- Subscribing to topics with callbacks
- Publishing messages with QoS 0 (broadcast) and QoS 1 (acknowledged)
- Remote subscriber tracking
- Topic statistics

Hardware requirements:
- 2+ ESP32 boards
- Same WiFi channel configured
"""

import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

try:
    import uasyncio as asyncio
except ImportError:
    import asyncio

from umesh import Mesh, MeshConfig, PubSub


async def temperature_sensor_example():
    """
    Example: Temperature sensor publishing readings.

    This node publishes temperature data every 5 seconds.
    """
    # Configure mesh
    config = MeshConfig(
        channel=6,
        hello_interval=10000,  # 10s
        max_ttl=7,
    )

    # Create mesh and start
    mesh = Mesh(config)
    await mesh.start()

    # Create PubSub
    pubsub = PubSub(mesh)

    print(f"Temperature sensor started (node_id: {mesh.node_id:04x})")

    try:
        temp = 20  # Starting temperature

        while True:
            # Simulate temperature reading
            temp += 0.5 if temp < 25 else -0.5

            # Encode temperature as 2-byte little-endian
            temp_data = int(temp * 10).to_bytes(2, 'little', signed=True)

            # Publish to "temp/room1" topic with QoS 1 (acknowledged)
            recipients = await pubsub.publish("temp/room1", temp_data, qos=1)

            print(f"Published temp={temp:.1f}°C to {recipients} subscriber(s)")

            # Wait 5 seconds
            await asyncio.sleep(5)

    except KeyboardInterrupt:
        print("\nStopping...")
    finally:
        await mesh.stop()


async def temperature_monitor_example():
    """
    Example: Temperature monitor subscribing to readings.

    This node subscribes to temperature topic and displays readings.
    """
    # Configure mesh
    config = MeshConfig(
        channel=6,
        hello_interval=10000,
        max_ttl=7,
    )

    # Create mesh and start
    mesh = Mesh(config)
    await mesh.start()

    # Create PubSub
    pubsub = PubSub(mesh)

    print(f"Temperature monitor started (node_id: {mesh.node_id:04x})")

    # Define callback for temperature updates
    def on_temperature(src_id: int, topic: str, data: bytes):
        """Handle temperature reading."""
        # Decode temperature from 2-byte little-endian
        temp = int.from_bytes(data, 'little', signed=True) / 10.0
        print(f"[{topic}] Temperature from {src_id:04x}: {temp:.1f}°C")

    # Subscribe to temperature topic
    pubsub.subscribe("temp/room1", on_temperature)
    print("Subscribed to 'temp/room1'")

    # Register message handler for SUB/PUB packets
    def on_message(src_id: int, data: bytes):
        """Handle mesh messages - dispatch to PubSub."""
        # In real integration, Mesh would dispatch PKT_PUB and PKT_SUB
        # to PubSub automatically
        pass

    mesh.on_message(on_message)

    try:
        # Keep running
        while True:
            # Show stats every 30 seconds
            await asyncio.sleep(30)

            stats = pubsub.stats
            print("\nPubSub Stats:")
            print(f"  Topics subscribed: {stats['topics_subscribed']}")
            print(f"  PUB received: {stats['pub_received']}")
            print(f"  SUB received: {stats['sub_received']}")

    except KeyboardInterrupt:
        print("\nStopping...")
    finally:
        await mesh.stop()


async def multi_topic_example():
    """
    Example: Multi-topic subscriber with different handlers.

    Demonstrates subscribing to multiple topics with separate callbacks.
    """
    config = MeshConfig(channel=6)
    mesh = Mesh(config)
    await mesh.start()

    pubsub = PubSub(mesh)

    print(f"Multi-topic subscriber started (node_id: {mesh.node_id:04x})")

    # Temperature callback
    def on_temp(src_id, topic, data):
        temp = int.from_bytes(data, 'little', signed=True) / 10.0
        print(f"[TEMP] {src_id:04x}: {temp:.1f}°C")

    # Humidity callback
    def on_humidity(src_id, topic, data):
        humidity = int.from_bytes(data, 'little') / 10.0
        print(f"[HUMID] {src_id:04x}: {humidity:.1f}%")

    # Motion callback
    def on_motion(src_id, topic, data):
        motion = data[0] == 1
        print(f"[MOTION] {src_id:04x}: {'DETECTED' if motion else 'Clear'}")

    # Subscribe to multiple topics
    pubsub.subscribe("temp/room1", on_temp)
    pubsub.subscribe("humidity/room1", on_humidity)
    pubsub.subscribe("motion/room1", on_motion)

    print("Subscribed to:")
    for topic in pubsub.get_subscriptions():
        print(f"  - {topic}")

    try:
        while True:
            await asyncio.sleep(60)
    except KeyboardInterrupt:
        print("\nStopping...")
    finally:
        await mesh.stop()


async def publisher_example():
    """
    Example: Multi-sensor publisher.

    Publishes to multiple topics with different QoS levels.
    """
    config = MeshConfig(channel=6)
    mesh = Mesh(config)
    await mesh.start()

    pubsub = PubSub(mesh)

    print(f"Multi-sensor publisher started (node_id: {mesh.node_id:04x})")

    try:
        count = 0

        while True:
            # Publish temperature (QoS 1 - acknowledged)
            temp = 20 + (count % 10)
            temp_data = (temp * 10).to_bytes(2, 'little', signed=True)
            await pubsub.publish("temp/room1", temp_data, qos=1)
            print(f"Published temp={temp}°C")

            # Publish humidity (QoS 1 - acknowledged)
            humidity = 50 + (count % 20)
            humid_data = (humidity * 10).to_bytes(2, 'little')
            await pubsub.publish("humidity/room1", humid_data, qos=1)
            print(f"Published humidity={humidity}%")

            # Publish motion (QoS 0 - broadcast, best effort)
            motion = (count % 5) == 0
            motion_data = bytes([1 if motion else 0])
            await pubsub.publish("motion/room1", motion_data, qos=0)
            print(f"Published motion={'DETECTED' if motion else 'Clear'}")

            # Show subscriber counts
            temp_subs = pubsub.get_subscribers("temp/room1")
            print(f"Known subscribers for temp/room1: {len(temp_subs)}")

            count += 1
            await asyncio.sleep(10)

    except KeyboardInterrupt:
        print("\nStopping...")
    finally:
        await mesh.stop()


# =============================================================================
# Main - Choose which example to run
# =============================================================================

if __name__ == '__main__':
    import sys

    print("\nµMesh Pub/Sub Examples\n" + "=" * 50)
    print("\nAvailable examples:")
    print("  1. Temperature sensor (publisher)")
    print("  2. Temperature monitor (subscriber)")
    print("  3. Multi-topic subscriber")
    print("  4. Multi-sensor publisher")

    if len(sys.argv) > 1:
        choice = sys.argv[1]
    else:
        choice = input("\nSelect example (1-4): ").strip()

    examples = {
        '1': temperature_sensor_example,
        '2': temperature_monitor_example,
        '3': multi_topic_example,
        '4': publisher_example,
    }

    example = examples.get(choice)
    if example:
        print(f"\nRunning example {choice}...\n")
        try:
            asyncio.run(example())
        except KeyboardInterrupt:
            print("\nExiting...")
    else:
        print(f"Invalid choice: {choice}")
        print("Usage: python example_pubsub.py [1-4]")
