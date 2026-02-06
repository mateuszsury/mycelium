# PubSub Integration Guide

## Overview
This guide explains how to integrate the PubSub system with the Mesh class for topic-based messaging.

## Quick Start

### 1. Create PubSub Instance
```python
from umesh import Mesh, PubSub

# Create and start mesh
mesh = Mesh()
await mesh.start()

# Create PubSub instance
pubsub = PubSub(mesh)
```

### 2. Subscribe to Topics
```python
def on_temperature(src_id: int, topic: str, data: bytes):
    """Handle temperature readings."""
    temp = int.from_bytes(data, 'little', signed=True) / 10.0
    print(f"Temperature from {src_id:04x}: {temp:.1f}°C")

# Subscribe to topic
pubsub.subscribe("temp/room1", on_temperature)
```

### 3. Publish Messages
```python
# Encode temperature as 2-byte little-endian (23.5°C)
temp_data = (235).to_bytes(2, 'little', signed=True)

# Publish with QoS 1 (acknowledged delivery)
recipients = await pubsub.publish("temp/room1", temp_data, qos=1)
print(f"Published to {recipients} subscriber(s)")
```

## Full Integration with Mesh

To fully integrate PubSub with the Mesh class, add packet dispatching in `mesh.py`:

### Step 1: Add PubSub to Mesh Class

```python
# In umesh/mesh.py

class Mesh:
    def __init__(self, config=None):
        # ... existing code ...

        # PubSub instance (optional)
        self._pubsub = None

    def enable_pubsub(self):
        """Enable Pub/Sub messaging."""
        if self._pubsub is None:
            from .pubsub import PubSub
            self._pubsub = PubSub(self)
        return self._pubsub

    @property
    def pubsub(self):
        """Get PubSub instance (creates if needed)."""
        if self._pubsub is None:
            self.enable_pubsub()
        return self._pubsub
```

### Step 2: Add Packet Dispatching

```python
# In Mesh._process_packet() method, add:

async def _process_packet(self, mac: bytes, raw: bytes, rssi: int):
    # ... existing decode and duplicate check ...

    # Dispatch based on packet type
    if pkt_type == PKT_HELLO:
        self._handle_hello(mac, parsed, rssi)
    elif pkt_type == PKT_DATA:
        await self._handle_data(mac, parsed)
    elif pkt_type == PKT_PING:
        await self._handle_ping(mac, parsed)
    elif pkt_type == PKT_PONG:
        self._handle_pong(mac, parsed)

    # PubSub packet types
    elif pkt_type == PKT_PUB:
        if self._pubsub:
            payload = self._codec.get_payload(raw)
            self._pubsub.handle_pub(parsed['src_id'], payload)

    elif pkt_type == PKT_SUB:
        if self._pubsub:
            payload = self._codec.get_payload(raw)
            self._pubsub.handle_sub(parsed['src_id'], payload)
```

### Step 3: Usage with Integrated Mesh

```python
# Create mesh with PubSub enabled
mesh = Mesh()
await mesh.start()

# Access PubSub through mesh
pubsub = mesh.pubsub

# Subscribe
def on_message(src_id, topic, data):
    print(f"[{topic}] from {src_id:04x}: {data}")

pubsub.subscribe("sensors/temp", on_message)

# Publish
await pubsub.publish("sensors/temp", b"23.5", qos=1)
```

## QoS Levels

### QoS 0 - Best Effort (Broadcast)
```python
# Broadcast to all nodes, no acknowledgment
await pubsub.publish("alerts/motion", b"\x01", qos=0)
```

**Use cases:**
- High-frequency sensor data
- Non-critical notifications
- Low-latency required

### QoS 1 - Acknowledged Delivery
```python
# Send to known subscribers with ACK
recipients = await pubsub.publish("config/update", data, qos=1)
```

**Use cases:**
- Important state changes
- Configuration updates
- Commands requiring confirmation

## Topic Naming Conventions

### Best Practices
- Use forward slashes for hierarchy: `sensors/temp/room1`
- Keep topics under 16 characters
- Use lowercase for consistency
- Avoid special characters

### Good Examples
```python
"temp/room1"
"humidity/out"
"motion/hall"
"config/wifi"
"status/battery"
```

### Bad Examples
```python
"ThisIsAVeryLongTopicNameThatExceedsTheLimit"  # Too long
"temp room1"  # Spaces problematic
"TEMP/ROOM1"  # Inconsistent case
```

## Managing Subscriptions

### Multiple Callbacks per Topic
```python
def logger(src_id, topic, data):
    print(f"LOG: {topic} = {data}")

def processor(src_id, topic, data):
    process_data(data)

# Both callbacks will be called
pubsub.subscribe("temp/room1", logger)
pubsub.subscribe("temp/room1", processor)
```

### Unsubscribe Specific Callback
```python
# Remove only the logger callback
pubsub.unsubscribe("temp/room1", logger)

# processor callback still active
```

### Unsubscribe All Callbacks
```python
# Remove all callbacks for topic
pubsub.unsubscribe("temp/room1")
```

## Monitoring

### Get Active Subscriptions
```python
topics = pubsub.get_subscriptions()
print(f"Subscribed to: {topics}")
# Output: ['temp/room1', 'humidity/room1', 'motion/hall']
```

### Get Known Subscribers
```python
subscribers = pubsub.get_subscribers("temp/room1")
print(f"Subscribers: {[f'{s:04x}' for s in subscribers]}")
# Output: Subscribers: ['0x1234', '0x5678']
```

### Statistics
```python
stats = pubsub.stats
print(f"PUB sent: {stats['pub_sent']}")
print(f"PUB received: {stats['pub_received']}")
print(f"SUB sent: {stats['sub_sent']}")
print(f"SUB received: {stats['sub_received']}")
print(f"Topics subscribed: {stats['topics_subscribed']}")
print(f"Remote subscribers: {stats['remote_subscribers']}")
```

## Common Use Cases

### 1. Sensor Network
```python
# Temperature sensor node
temp_sensor = (23.5 * 10).to_bytes(2, 'little', signed=True)
await pubsub.publish("temp/living", temp_sensor, qos=1)

# Monitor node
def on_temp(src_id, topic, data):
    temp = int.from_bytes(data, 'little', signed=True) / 10.0
    if temp > 25:
        print(f"ALERT: High temperature {temp}°C")

pubsub.subscribe("temp/living", on_temp)
```

### 2. Remote Control
```python
# Controller node
await pubsub.publish("lights/living", b"\x01", qos=1)  # Turn on

# Light node
def on_light_cmd(src_id, topic, data):
    state = data[0] == 1
    set_light(state)
    print(f"Light {'ON' if state else 'OFF'}")

pubsub.subscribe("lights/living", on_light_cmd)
```

### 3. Event Broadcasting
```python
# Door sensor
await pubsub.publish("events/door", b"open", qos=0)

# Multiple listeners
def alarm_handler(src_id, topic, data):
    if data == b"open":
        trigger_alarm()

def logger_handler(src_id, topic, data):
    log(f"Door {data.decode()}")

pubsub.subscribe("events/door", alarm_handler)
pubsub.subscribe("events/door", logger_handler)
```

### 4. Configuration Distribution
```python
# Gateway node
config_data = json.dumps({"interval": 60}).encode()
await pubsub.publish("config/global", config_data, qos=1)

# Sensor nodes
def on_config(src_id, topic, data):
    config = json.loads(data)
    update_interval(config['interval'])

pubsub.subscribe("config/global", on_config)
```

## Error Handling

### Callback Exceptions
```python
def risky_callback(src_id, topic, data):
    # If this raises, it won't crash the system
    value = int(data)  # May raise ValueError
    process(value)

# Exceptions are caught and logged
pubsub.subscribe("test", risky_callback)
```

### Payload Size Validation
```python
try:
    large_data = b"x" * 234
    await pubsub.publish("test", large_data, qos=0)
except ValueError as e:
    print(f"Payload too large: {e}")
```

### Topic Length Validation
```python
try:
    long_topic = "x" * 17
    pubsub.subscribe(long_topic, callback)
except ValueError as e:
    print(f"Topic too long: {e}")
```

## Performance Tips

1. **Use QoS 0 for high-frequency data**
   - Lower overhead, faster delivery
   - Acceptable loss for sensor readings

2. **Use QoS 1 for critical messages**
   - Acknowledged delivery
   - Better reliability for commands

3. **Keep callbacks fast**
   - Avoid blocking operations
   - Use async/await for I/O

4. **Limit topic count**
   - Stay well below MAX_TOPICS (32)
   - Reuse topics when possible

5. **Monitor remote subscribers**
   - Check `get_subscribers()` before publishing
   - Avoid QoS 1 to topics with no subscribers

## Troubleshooting

### Callbacks Not Called
- Verify topic spelling (case-sensitive hash)
- Check if subscribed: `pubsub.get_subscriptions()`
- Ensure Mesh is running: `await mesh.start()`

### SUB Packets Not Received
- Check event loop is running
- Verify WiFi channel matches
- Check mesh network connectivity

### High Memory Usage
- Limit topic count < MAX_TOPICS
- Monitor `stats['remote_subscribers']`
- Unsubscribe unused topics

## See Also
- `examples/example_pubsub.py` - Complete examples
- `tests/test_pubsub.py` - Unit tests
- `.claude/agent-memory/micropython-senior-dev/pubsub.md` - Implementation details
