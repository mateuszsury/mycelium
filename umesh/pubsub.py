"""
µMesh Publish/Subscribe System
===============================

Lightweight pub/sub messaging over mesh network with topic hashing,
subscription tracking, and QoS support.

Features:
- Topic string hashing to uint16 for efficient transmission
- Local callback registration for subscribed topics
- Remote subscriber tracking for targeted publishing
- QoS 0 (best effort broadcast) and QoS 1 (acknowledged delivery)
- Memory-bounded topic and subscriber limits

Usage:
    >>> from umesh.pubsub import PubSub
    >>>
    >>> pubsub = PubSub(mesh)
    >>>
    >>> # Subscribe to topic
    >>> def on_temp(src_id, topic, data):
    ...     temp = int.from_bytes(data, 'little')
    ...     print(f"Temperature from {src_id:04x}: {temp}°C")
    >>>
    >>> pubsub.subscribe("temp/room1", on_temp)
    >>>
    >>> # Publish to topic
    >>> temp_data = (23).to_bytes(2, 'little')
    >>> await pubsub.publish("temp/room1", temp_data, qos=1)
    >>>
    >>> # Unsubscribe
    >>> pubsub.unsubscribe("temp/room1", on_temp)
"""

try:
    import uasyncio as asyncio
except ImportError:
    import asyncio

try:
    from micropython import const
except ImportError:
    # Fallback for non-MicroPython environments
    def const(x):
        return x

from .const import PKT_PUB, PKT_SUB, BROADCAST_ID, MAX_PAYLOAD

# PubSub constants
MAX_TOPICS = const(32)          # Max subscribed topics per node
MAX_SUBSCRIBERS = const(64)     # Max remote subscribers tracked
TOPIC_MAX_LEN = const(16)       # Max topic string length (bytes)

# SUB packet flags
SUB_SUBSCRIBE = const(0)        # Subscribe to topic
SUB_UNSUBSCRIBE = const(1)      # Unsubscribe from topic


class PubSub:
    """
    Lightweight Publish/Subscribe system over mesh network.

    Provides topic-based messaging with:
    - Local subscription callbacks
    - Remote subscriber discovery via SUB packets
    - QoS 0 (broadcast) and QoS 1 (acknowledged) delivery
    - Topic hashing for efficient 16-bit identifiers
    """

    def __init__(self, mesh):
        """
        Initialize PubSub with Mesh instance.

        Args:
            mesh: Mesh instance for sending/receiving packets
        """
        self._mesh = mesh

        # Local subscriptions: topic_hash -> [callbacks]
        self._local_subs = {}

        # Remote subscriptions: topic_hash -> set(node_ids)
        self._remote_subs = {}

        # Topic names: topic_hash -> topic_str (for debugging/callbacks)
        self._topic_names = {}

        # Statistics
        self._stats = {
            'pub_sent': 0,        # PUB packets sent
            'pub_received': 0,    # PUB packets received
            'sub_sent': 0,        # SUB packets sent
            'sub_received': 0,    # SUB packets received
            'topics_subscribed': 0,  # Current local subscriptions
            'remote_subscribers': 0, # Current remote subscribers
        }

    def _schedule_task(self, coro):
        """
        Safely schedule a coroutine as a task if event loop is running.

        Handles MicroPython/CPython differences for asyncio.get_running_loop()
        vs deprecated get_event_loop().

        Args:
            coro: Coroutine to schedule
        """
        scheduled = False
        try:
            try:
                loop = asyncio.get_running_loop()
            except (RuntimeError, AttributeError):
                loop = asyncio.get_event_loop()
                if hasattr(loop, 'is_running') and not loop.is_running():
                    return
            asyncio.create_task(coro)
            scheduled = True
        except RuntimeError:
            pass
        finally:
            if not scheduled and hasattr(coro, 'close'):
                coro.close()

    def subscribe(self, topic: str, callback):
        """
        Subscribe to topic with callback.

        Registers callback for local delivery and broadcasts SUB packet
        to announce subscription to the network.

        Args:
            topic: Topic string (max 16 chars)
            callback: Function with signature callback(src_node_id: int, topic: str, data: bytes)

        Raises:
            ValueError: If topic exceeds TOPIC_MAX_LEN or max topics reached
        """
        if len(topic) > TOPIC_MAX_LEN:
            raise ValueError(f"Topic too long: {len(topic)} chars (max {TOPIC_MAX_LEN})")

        if len(self._local_subs) >= MAX_TOPICS and topic not in self._topic_names.values():
            raise ValueError(f"Max topics reached: {MAX_TOPICS}")

        # Hash topic to uint16
        topic_hash = self._hash_topic(topic)

        # Store topic name for callbacks
        self._topic_names[topic_hash] = topic

        # Add callback to list
        if topic_hash not in self._local_subs:
            self._local_subs[topic_hash] = []

        if callback not in self._local_subs[topic_hash]:
            self._local_subs[topic_hash].append(callback)
            self._stats['topics_subscribed'] = len(self._local_subs)

        # Broadcast SUB packet to announce subscription (fire-and-forget)
        self._schedule_task(self._broadcast_sub(topic_hash, SUB_SUBSCRIBE))

    def unsubscribe(self, topic: str, callback=None):
        """
        Unsubscribe from topic.

        Args:
            topic: Topic string
            callback: Specific callback to remove, or None to remove all

        If callback is None, removes all callbacks for topic and broadcasts
        UNSUB packet.
        """
        topic_hash = self._hash_topic(topic)

        if topic_hash not in self._local_subs:
            return  # Not subscribed

        if callback is None:
            # Remove all callbacks
            del self._local_subs[topic_hash]
            if topic_hash in self._topic_names:
                del self._topic_names[topic_hash]

            # Broadcast unsubscribe
            self._schedule_task(self._broadcast_sub(topic_hash, SUB_UNSUBSCRIBE))
        else:
            # Remove specific callback
            if callback in self._local_subs[topic_hash]:
                self._local_subs[topic_hash].remove(callback)

            # If no callbacks remain, remove topic
            if not self._local_subs[topic_hash]:
                del self._local_subs[topic_hash]
                if topic_hash in self._topic_names:
                    del self._topic_names[topic_hash]

                # Broadcast unsubscribe
                self._schedule_task(self._broadcast_sub(topic_hash, SUB_UNSUBSCRIBE))

        self._stats['topics_subscribed'] = len(self._local_subs)

    async def publish(self, topic: str, data: bytes, qos: int = 0, retain: bool = False) -> int:
        """
        Publish data to topic.

        Args:
            topic: Topic string (max 16 chars)
            data: Payload data (max ~233 bytes for PUB packet)
            qos: Quality of Service level
                 0 = Best effort broadcast
                 1 = Acknowledged delivery to known subscribers
            retain: Reserved for future use (retained messages)

        Returns:
            Number of recipients (known subscribers for QoS 1, 1 for QoS 0 broadcast)

        Raises:
            ValueError: If topic or payload too large
        """
        if len(topic) > TOPIC_MAX_LEN:
            raise ValueError(f"Topic too long: {len(topic)} chars (max {TOPIC_MAX_LEN})")

        topic_hash = self._hash_topic(topic)

        # Build PUB payload: [topic_hash_hi, topic_hash_lo, qos, ...data]
        payload = bytearray(3 + len(data))
        payload[0] = (topic_hash >> 8) & 0xFF
        payload[1] = topic_hash & 0xFF
        payload[2] = qos & 0xFF
        payload[3:] = data

        if len(payload) > MAX_PAYLOAD:
            raise ValueError(f"PUB payload too large: {len(payload)} bytes (max {MAX_PAYLOAD})")

        recipients = 0

        if qos == 0:
            # Best effort broadcast
            packet = self._mesh._codec.encode(
                pkt_type=PKT_PUB,
                src_id=self._mesh.node_id,
                dest_id=BROADCAST_ID,
                payload=bytes(payload),
                pkt_id=self._mesh._next_packet_id(),
                ttl=self._mesh._config.max_ttl,
                hop_count=0
            )

            success = await self._mesh._adapter.send_broadcast(packet)
            if success:
                self._stats['pub_sent'] += 1
                recipients = 1  # Broadcast counts as 1 recipient

        elif qos == 1:
            # Acknowledged delivery to known subscribers
            subscribers = self._remote_subs.get(topic_hash, set())

            for dest_id in subscribers:
                packet = self._mesh._codec.encode(
                    pkt_type=PKT_PUB,
                    src_id=self._mesh.node_id,
                    dest_id=dest_id,
                    payload=bytes(payload),
                    pkt_id=self._mesh._next_packet_id(),
                    ttl=self._mesh._config.max_ttl,
                    hop_count=0,
                    flags=0x02  # ACK_REQ flag
                )

                # Use mesh send for unicast with routing
                # In Phase 1, this will broadcast
                # In Phase 2+, this will use routing
                success = await self._mesh.send(dest_id, bytes(payload))
                if success:
                    recipients += 1

            self._stats['pub_sent'] += recipients

        else:
            raise ValueError(f"Invalid QoS: {qos} (must be 0 or 1)")

        return recipients

    def handle_pub(self, src_id: int, payload: bytes):
        """
        Handle received PUB packet.

        Extracts topic hash and data, calls registered callbacks.

        Args:
            src_id: Source node ID
            payload: PUB packet payload (topic_hash + qos + data)
        """
        if len(payload) < 3:
            return  # Invalid payload

        # Parse PUB payload
        topic_hash = (payload[0] << 8) | payload[1]
        qos = payload[2]
        data = payload[3:]

        # Update stats
        self._stats['pub_received'] += 1

        # Check if we're subscribed
        callbacks = self._local_subs.get(topic_hash)
        if not callbacks:
            return  # Not subscribed to this topic

        # Get topic name for callback (or use hash if name unknown)
        topic_name = self._topic_names.get(topic_hash, f"#{topic_hash:04x}")

        # Call all registered callbacks
        for callback in callbacks:
            try:
                callback(src_id, topic_name, data)
            except Exception as e:
                print(f"PubSub callback error: {e}")

    def handle_sub(self, src_id: int, payload: bytes):
        """
        Handle received SUB packet.

        Records remote subscriber for topic.

        Args:
            src_id: Source node ID
            payload: SUB packet payload (topic_hash + flags)
        """
        if len(payload) < 3:
            return  # Invalid payload

        # Parse SUB payload: [topic_hash_hi, topic_hash_lo, flags]
        topic_hash = (payload[0] << 8) | payload[1]
        flags = payload[2]

        # Update stats
        self._stats['sub_received'] += 1

        if flags == SUB_SUBSCRIBE:
            # Add remote subscriber
            if topic_hash not in self._remote_subs:
                self._remote_subs[topic_hash] = set()

            # Check subscriber limit (snapshot values to avoid concurrent modification)
            total_subscribers = sum(len(subs) for subs in list(self._remote_subs.values()))
            if total_subscribers >= MAX_SUBSCRIBERS and src_id not in self._remote_subs[topic_hash]:
                # Too many subscribers - ignore (silent drop)
                return

            self._remote_subs[topic_hash].add(src_id)
            self._stats['remote_subscribers'] = total_subscribers + 1

        elif flags == SUB_UNSUBSCRIBE:
            # Remove remote subscriber
            if topic_hash in self._remote_subs:
                self._remote_subs[topic_hash].discard(src_id)

                # Remove empty topic
                if not self._remote_subs[topic_hash]:
                    del self._remote_subs[topic_hash]

                total_subscribers = sum(len(subs) for subs in list(self._remote_subs.values()))
                self._stats['remote_subscribers'] = total_subscribers

    async def _broadcast_sub(self, topic_hash: int, flags: int):
        """
        Broadcast SUB packet to announce subscription.

        Args:
            topic_hash: Topic hash (uint16)
            flags: SUB_SUBSCRIBE or SUB_UNSUBSCRIBE
        """
        # Build SUB payload: [topic_hash_hi, topic_hash_lo, flags]
        payload = bytes([
            (topic_hash >> 8) & 0xFF,
            topic_hash & 0xFF,
            flags & 0xFF
        ])

        packet = self._mesh._codec.encode(
            pkt_type=PKT_SUB,
            src_id=self._mesh.node_id,
            dest_id=BROADCAST_ID,
            payload=payload,
            pkt_id=self._mesh._next_packet_id(),
            ttl=self._mesh._config.max_ttl,
            hop_count=0
        )

        success = await self._mesh._adapter.send_broadcast(packet)
        if success:
            self._stats['sub_sent'] += 1

    def _hash_topic(self, topic: str) -> int:
        """
        Hash topic string to uint16.

        Uses DJB2 hash algorithm truncated to 16 bits.
        Simple but effective for short strings.

        Args:
            topic: Topic string

        Returns:
            16-bit hash value (0-65535)
        """
        h = 5381
        for c in topic:
            h = ((h << 5) + h + ord(c)) & 0xFFFFFFFF
        return h & 0xFFFF

    def get_subscribers(self, topic: str) -> list:
        """
        Get list of known subscribers for topic.

        Args:
            topic: Topic string

        Returns:
            List of node IDs subscribed to topic
        """
        topic_hash = self._hash_topic(topic)
        return list(self._remote_subs.get(topic_hash, set()))

    def get_subscriptions(self) -> list:
        """
        Get list of locally subscribed topics.

        Returns:
            List of topic strings this node is subscribed to
        """
        return list(self._topic_names.values())

    @property
    def stats(self) -> dict:
        """
        Pub/Sub statistics.

        Returns:
            Dict with pub_sent, pub_received, sub_sent, sub_received,
            topics_subscribed, remote_subscribers
        """
        return self._stats.copy()
