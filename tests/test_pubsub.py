"""
Tests for umesh/pubsub.py - Pub/Sub messaging system
"""

import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Fix encoding for Windows console
import io
# Respect the caller's stdout (including pytest capture).

import asyncio
import pytest
from umesh.pubsub import PubSub, MAX_TOPICS, MAX_SUBSCRIBERS, TOPIC_MAX_LEN, SUB_SUBSCRIBE, SUB_UNSUBSCRIBE
from umesh.const import PKT_PUB, PKT_SUB, BROADCAST_ID


class MockMesh:
    """Mock Mesh instance for testing."""

    def __init__(self):
        self.node_id = 0x1234
        self._pkt_id = 0
        self._sent_packets = []

        # Mock codec
        self._codec = MockCodec()

        # Mock adapter
        self._adapter = MockAdapter()

        # Mock config
        self._config = MockConfig()

    def _next_packet_id(self):
        self._pkt_id = (self._pkt_id + 1) & 0xFFFF
        return self._pkt_id

    async def send(self, dest_id, data, reliable=False, pkt_type=None):
        """Mock send."""
        self._sent_packets.append(('send', dest_id, data))
        return True


class MockCodec:
    """Mock PacketCodec."""

    def encode(self, **kwargs):
        """Mock encode - return dict as bytes."""
        # Store kwargs as packet for verification
        return str(kwargs).encode()


class MockAdapter:
    """Mock ESPNowAdapter."""

    def __init__(self):
        self._sent = []

    async def send_broadcast(self, packet):
        """Mock broadcast."""
        self._sent.append(packet)
        return True


class MockConfig:
    """Mock MeshConfig."""

    def __init__(self):
        self.max_ttl = 7


# =============================================================================
# Test Cases
# =============================================================================

def test_init():
    """Test PubSub initialization."""
    mesh = MockMesh()
    pubsub = PubSub(mesh)

    assert pubsub._mesh is mesh
    assert len(pubsub._local_subs) == 0
    assert len(pubsub._remote_subs) == 0
    assert len(pubsub._topic_names) == 0
    assert pubsub._stats['topics_subscribed'] == 0
    print("✓ PubSub initialization")


def test_hash_topic():
    """Test topic hashing."""
    mesh = MockMesh()
    pubsub = PubSub(mesh)

    # Hash should be deterministic
    h1 = pubsub._hash_topic("temp/room1")
    h2 = pubsub._hash_topic("temp/room1")
    assert h1 == h2

    # Different topics should have different hashes (usually)
    h3 = pubsub._hash_topic("temp/room2")
    assert h1 != h3

    # Hash should be uint16 (0-65535)
    assert 0 <= h1 <= 0xFFFF
    assert 0 <= h3 <= 0xFFFF

    print("✓ Topic hashing")


def test_subscribe():
    """Test topic subscription."""
    mesh = MockMesh()
    pubsub = PubSub(mesh)

    callback_called = []

    def on_message(src_id, topic, data):
        callback_called.append((src_id, topic, data))

    # Subscribe to topic
    pubsub.subscribe("test/topic", on_message)

    # Check subscription registered
    topic_hash = pubsub._hash_topic("test/topic")
    assert topic_hash in pubsub._local_subs
    assert on_message in pubsub._local_subs[topic_hash]
    assert pubsub._topic_names[topic_hash] == "test/topic"
    assert pubsub._stats['topics_subscribed'] == 1

    # Check SUB packet was broadcast
    # (will be sent async, so we can't verify immediately in sync test)

    print("✓ Topic subscription")


def test_subscribe_multiple_callbacks():
    """Test multiple callbacks for same topic."""
    mesh = MockMesh()
    pubsub = PubSub(mesh)

    calls1 = []
    calls2 = []

    def cb1(src_id, topic, data):
        calls1.append((src_id, topic, data))

    def cb2(src_id, topic, data):
        calls2.append((src_id, topic, data))

    # Subscribe both callbacks
    pubsub.subscribe("test", cb1)
    pubsub.subscribe("test", cb2)

    topic_hash = pubsub._hash_topic("test")
    assert len(pubsub._local_subs[topic_hash]) == 2
    assert cb1 in pubsub._local_subs[topic_hash]
    assert cb2 in pubsub._local_subs[topic_hash]

    print("✓ Multiple callbacks per topic")


def test_subscribe_too_long():
    """Test topic length validation."""
    mesh = MockMesh()
    pubsub = PubSub(mesh)

    # Topic too long
    long_topic = "x" * (TOPIC_MAX_LEN + 1)

    try:
        pubsub.subscribe(long_topic, lambda s, t, d: None)
        assert False, "Should raise ValueError"
    except ValueError as e:
        assert "too long" in str(e).lower()

    print("✓ Topic length validation")


def test_subscribe_max_topics():
    """Test max topics limit."""
    mesh = MockMesh()
    pubsub = PubSub(mesh)

    # Subscribe to max topics
    for i in range(MAX_TOPICS):
        pubsub.subscribe(f"topic{i}", lambda s, t, d: None)

    assert len(pubsub._local_subs) == MAX_TOPICS

    # Try to subscribe to one more
    try:
        pubsub.subscribe("overflow", lambda s, t, d: None)
        assert False, "Should raise ValueError"
    except ValueError as e:
        assert "max topics" in str(e).lower()

    print("✓ Max topics limit")


def test_unsubscribe():
    """Test topic unsubscription."""
    mesh = MockMesh()
    pubsub = PubSub(mesh)

    def callback(s, t, d):
        pass

    # Subscribe
    pubsub.subscribe("test", callback)
    topic_hash = pubsub._hash_topic("test")
    assert topic_hash in pubsub._local_subs

    # Unsubscribe
    pubsub.unsubscribe("test", callback)

    # Check removed
    assert topic_hash not in pubsub._local_subs
    assert topic_hash not in pubsub._topic_names
    assert pubsub._stats['topics_subscribed'] == 0

    print("✓ Topic unsubscription")


def test_unsubscribe_all_callbacks():
    """Test unsubscribe without callback (removes all)."""
    mesh = MockMesh()
    pubsub = PubSub(mesh)

    def cb1(s, t, d):
        pass

    def cb2(s, t, d):
        pass

    # Subscribe both
    pubsub.subscribe("test", cb1)
    pubsub.subscribe("test", cb2)

    topic_hash = pubsub._hash_topic("test")
    assert len(pubsub._local_subs[topic_hash]) == 2

    # Unsubscribe all
    pubsub.unsubscribe("test")

    # Check all removed
    assert topic_hash not in pubsub._local_subs

    print("✓ Unsubscribe all callbacks")


@pytest.mark.asyncio
async def test_publish_qos0():
    """Test QoS 0 broadcast publish."""
    mesh = MockMesh()
    pubsub = PubSub(mesh)

    # Publish to topic
    data = b"hello"
    recipients = await pubsub.publish("test/topic", data, qos=0)

    assert recipients == 1  # Broadcast counts as 1
    assert pubsub._stats['pub_sent'] == 1

    # Check packet was broadcast
    assert len(mesh._adapter._sent) == 1

    print("✓ QoS 0 publish")


@pytest.mark.asyncio
async def test_publish_qos1():
    """Test QoS 1 acknowledged publish."""
    mesh = MockMesh()
    pubsub = PubSub(mesh)

    # Add remote subscribers
    topic_hash = pubsub._hash_topic("test")
    pubsub._remote_subs[topic_hash] = {0x5678, 0x9ABC}

    # Publish
    data = b"hello"
    recipients = await pubsub.publish("test", data, qos=1)

    assert recipients == 2
    assert pubsub._stats['pub_sent'] == 2

    print("✓ QoS 1 publish")


@pytest.mark.asyncio
async def test_publish_payload_too_large():
    """Test payload size validation."""
    mesh = MockMesh()
    pubsub = PubSub(mesh)

    # Payload too large (3 bytes overhead for topic_hash + qos)
    large_data = b"x" * 234  # Would be 237 total

    try:
        await pubsub.publish("test", large_data, qos=0)
        assert False, "Should raise ValueError"
    except ValueError as e:
        assert "too large" in str(e).lower()

    print("✓ Payload size validation")


def test_handle_pub():
    """Test handling received PUB packets."""
    mesh = MockMesh()
    pubsub = PubSub(mesh)

    received = []

    def on_message(src_id, topic, data):
        received.append((src_id, topic, data))

    # Subscribe
    pubsub.subscribe("test/topic", on_message)

    # Build PUB payload
    topic_hash = pubsub._hash_topic("test/topic")
    payload = bytes([
        (topic_hash >> 8) & 0xFF,
        topic_hash & 0xFF,
        0,  # qos
        0x41, 0x42, 0x43  # "ABC"
    ])

    # Handle PUB
    pubsub.handle_pub(0x5678, payload)

    # Check callback was called
    assert len(received) == 1
    assert received[0] == (0x5678, "test/topic", b"ABC")
    assert pubsub._stats['pub_received'] == 1

    print("✓ Handle PUB packet")


def test_handle_pub_not_subscribed():
    """Test handling PUB for unsubscribed topic."""
    mesh = MockMesh()
    pubsub = PubSub(mesh)

    # Don't subscribe

    # Build PUB payload for unknown topic
    topic_hash = 0x1234
    payload = bytes([
        (topic_hash >> 8) & 0xFF,
        topic_hash & 0xFF,
        0,
        0x41, 0x42
    ])

    # Handle PUB - should not crash
    pubsub.handle_pub(0x5678, payload)

    # Stats should still update
    assert pubsub._stats['pub_received'] == 1

    print("✓ Handle PUB for unsubscribed topic")


def test_handle_pub_invalid():
    """Test handling invalid PUB packet."""
    mesh = MockMesh()
    pubsub = PubSub(mesh)

    # Invalid payload (too short)
    payload = b"\x12"

    # Should not crash
    pubsub.handle_pub(0x5678, payload)

    print("✓ Handle invalid PUB packet")


def test_handle_sub_subscribe():
    """Test handling SUB packet (subscribe)."""
    mesh = MockMesh()
    pubsub = PubSub(mesh)

    # Build SUB payload
    topic_hash = 0x1234
    payload = bytes([
        (topic_hash >> 8) & 0xFF,
        topic_hash & 0xFF,
        SUB_SUBSCRIBE
    ])

    # Handle SUB
    pubsub.handle_sub(0x5678, payload)

    # Check remote subscriber added
    assert topic_hash in pubsub._remote_subs
    assert 0x5678 in pubsub._remote_subs[topic_hash]
    assert pubsub._stats['sub_received'] == 1
    assert pubsub._stats['remote_subscribers'] == 1

    print("✓ Handle SUB subscribe")


def test_handle_sub_unsubscribe():
    """Test handling SUB packet (unsubscribe)."""
    mesh = MockMesh()
    pubsub = PubSub(mesh)

    # Add remote subscriber
    topic_hash = 0x1234
    pubsub._remote_subs[topic_hash] = {0x5678}

    # Build UNSUB payload
    payload = bytes([
        (topic_hash >> 8) & 0xFF,
        topic_hash & 0xFF,
        SUB_UNSUBSCRIBE
    ])

    # Handle UNSUB
    pubsub.handle_sub(0x5678, payload)

    # Check remote subscriber removed
    assert topic_hash not in pubsub._remote_subs

    print("✓ Handle SUB unsubscribe")


def test_handle_sub_max_subscribers():
    """Test max subscribers limit."""
    mesh = MockMesh()
    pubsub = PubSub(mesh)

    # Fill up to max subscribers
    topic_hash = 0x1234
    pubsub._remote_subs[topic_hash] = set(range(MAX_SUBSCRIBERS))

    # Try to add one more
    payload = bytes([
        (topic_hash >> 8) & 0xFF,
        topic_hash & 0xFF,
        SUB_SUBSCRIBE
    ])

    pubsub.handle_sub(0xFFFF, payload)

    # Should be silently ignored
    assert 0xFFFF not in pubsub._remote_subs[topic_hash]

    print("✓ Max subscribers limit")


def test_get_subscribers():
    """Test get_subscribers()."""
    mesh = MockMesh()
    pubsub = PubSub(mesh)

    # Add remote subscribers
    topic_hash = pubsub._hash_topic("test")
    pubsub._remote_subs[topic_hash] = {0x1234, 0x5678}

    # Get subscribers
    subs = pubsub.get_subscribers("test")

    assert len(subs) == 2
    assert 0x1234 in subs
    assert 0x5678 in subs

    print("✓ get_subscribers()")


def test_get_subscriptions():
    """Test get_subscriptions()."""
    mesh = MockMesh()
    pubsub = PubSub(mesh)

    # Subscribe to topics
    pubsub.subscribe("temp/room1", lambda s, t, d: None)
    pubsub.subscribe("humidity", lambda s, t, d: None)

    # Get subscriptions
    topics = pubsub.get_subscriptions()

    assert len(topics) == 2
    assert "temp/room1" in topics
    assert "humidity" in topics

    print("✓ get_subscriptions()")


def test_stats():
    """Test stats property."""
    mesh = MockMesh()
    pubsub = PubSub(mesh)

    stats = pubsub.stats

    assert isinstance(stats, dict)
    assert 'pub_sent' in stats
    assert 'pub_received' in stats
    assert 'sub_sent' in stats
    assert 'sub_received' in stats
    assert 'topics_subscribed' in stats
    assert 'remote_subscribers' in stats

    print("✓ stats property")


def test_callback_exception_handling():
    """Test that callback exceptions don't crash system."""
    mesh = MockMesh()
    pubsub = PubSub(mesh)

    def bad_callback(src_id, topic, data):
        raise Exception("Callback error")

    # Subscribe
    pubsub.subscribe("test", bad_callback)

    # Build PUB payload
    topic_hash = pubsub._hash_topic("test")
    payload = bytes([
        (topic_hash >> 8) & 0xFF,
        topic_hash & 0xFF,
        0,
        0x41
    ])

    # Should not crash
    pubsub.handle_pub(0x5678, payload)

    print("✓ Callback exception handling")


# =============================================================================
# Run Tests
# =============================================================================

if __name__ == '__main__':
    print("\n=== Testing umesh/pubsub.py ===\n")

    # Sync tests
    test_init()
    test_hash_topic()
    test_subscribe()
    test_subscribe_multiple_callbacks()
    test_subscribe_too_long()
    test_subscribe_max_topics()
    test_unsubscribe()
    test_unsubscribe_all_callbacks()
    test_handle_pub()
    test_handle_pub_not_subscribed()
    test_handle_pub_invalid()
    test_handle_sub_subscribe()
    test_handle_sub_unsubscribe()
    test_handle_sub_max_subscribers()
    test_get_subscribers()
    test_get_subscriptions()
    test_stats()
    test_callback_exception_handling()

    # Async tests
    print("\nRunning async tests...")
    asyncio.run(test_publish_qos0())
    asyncio.run(test_publish_qos1())
    asyncio.run(test_publish_payload_too_large())

    print("\n=== All PubSub tests passed! ===\n")
