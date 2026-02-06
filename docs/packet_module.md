# µMesh Packet Module Documentation

## Overview

The `umesh/packet.py` module provides high-level packet encoding and decoding functionality for the µMesh protocol. It implements the PacketCodec class and helper functions for creating common packet types.

## File Locations

- **Implementation**: `umesh/packet.py`
- **Tests**: `tests/test_packet.py`
- **Demo**: `examples/packet_demo.py`

## Packet Format

### Header Structure (14 bytes)

```
Byte 0     | Protocol version (4 bit) + Packet type (4 bit)
Byte 1     | Flags (frag, ack_req, encrypted, routed)
Byte 2-3   | Packet ID (uint16)
Byte 4     | TTL (max 15)
Byte 5     | Hop count
Byte 6-7   | Source Node ID (uint16)
Byte 8-9   | Destination Node ID (uint16, 0xFFFF=broadcast)
Byte 10-11 | Fragment info: seq(12 bit) + idx(4 bit)
Byte 12    | Fragment: total(4 bit) + channel/QoS(4 bit)
Byte 13    | Checksum (CRC8 of bytes 0-12)
Byte 14-249| PAYLOAD (max 236 bytes)
```

### Packet Types (16 total)

- `PKT_DATA` (0x0): User data packet
- `PKT_DATA_FRAG` (0x1): Fragmented data packet
- `PKT_ACK` (0x2): Acknowledgement
- `PKT_NACK` (0x3): Negative acknowledgement
- `PKT_RREQ` (0x4): Route Request (AODV)
- `PKT_RREP` (0x5): Route Reply (AODV)
- `PKT_RERR` (0x6): Route Error
- `PKT_HELLO` (0x7): Neighbor beacon
- `PKT_PING` (0x8): Mesh ping
- `PKT_PONG` (0x9): Mesh pong response
- `PKT_TOPO` (0xA): Topology update
- `PKT_SYNC` (0xB): Time synchronization
- `PKT_PUB` (0xC): Publish (Pub/Sub)
- `PKT_SUB` (0xD): Subscribe
- `PKT_RPC` (0xE): Remote Procedure Call
- `PKT_CTRL` (0xF): Control (OTA, reboot, config)

### Flags (8 bits)

- `FLAG_FRAG` (0x01): Packet is fragmented
- `FLAG_ACK_REQ` (0x02): Acknowledgement requested
- `FLAG_ENCRYPTED` (0x04): Payload is encrypted
- `FLAG_ROUTED` (0x08): Packet has been routed

## PacketCodec Class

### Initialization

```python
from umesh.packet import PacketCodec

codec = PacketCodec()
```

Pre-allocates buffers for zero-allocation encoding:
- `_hdr_buf`: 14-byte header buffer
- `_parsed`: array of 10 integers for parsed fields

### Methods

#### encode()

Build a complete packet with header and payload.

```python
packet = codec.encode(
    pkt_type,           # Packet type (PKT_DATA, PKT_HELLO, etc.)
    src_id,             # Source node ID (16-bit)
    dest_id,            # Destination node ID (16-bit)
    payload=b'',        # Payload bytes (max 236 bytes)
    flags=0,            # Flags byte
    pkt_id=0,           # Packet sequence number
    ttl=DEFAULT_TTL,    # Time-to-live (default 7)
    hop_count=0,        # Hop counter
    frag_seq=0,         # Fragment sequence (12-bit)
    frag_idx=0,         # Fragment index (4-bit)
    frag_total=0,       # Total fragments (4-bit)
    qos=0              # Quality of Service (4-bit)
)
```

**Returns**: Complete packet as bytes

**Raises**:
- `ValueError` if payload > 236 bytes
- `ValueError` if TTL > 15
- `ValueError` if node IDs > 0xFFFF

#### decode()

Parse a raw packet into a dictionary.

```python
parsed = codec.decode(raw_packet)
```

**Returns**: Dictionary with fields or None if CRC fails

```python
{
    'version': 1,
    'pkt_type': PKT_DATA,
    'flags': FLAG_ACK_REQ,
    'pkt_id': 42,
    'ttl': 7,
    'hop_count': 0,
    'src_id': 0x1234,
    'dest_id': 0x5678,
    'frag_seq': 0,
    'frag_idx': 0,
    'frag_total': 0,
    'qos': 0,
    'checksum': 0xAB,
    'payload': b'Hello mesh!'
}
```

#### validate()

Quick CRC validation without full parsing.

```python
is_valid = codec.validate(raw_packet)  # Returns True/False
```

#### get_payload()

Fast payload extraction without parsing header.

```python
payload = codec.get_payload(raw_packet)  # Returns bytes
```

#### make_frag_info()

Pack fragment information into 24-bit value.

```python
frag_info = codec.make_frag_info(
    seq=100,      # Fragment sequence (0-4095)
    idx=0,        # Fragment index (0-15)
    total=3,      # Total fragments (0-15)
    qos=0         # QoS/channel (0-15)
)
```

#### unpack_frag_info()

Unpack 24-bit fragment info.

```python
seq, idx, total, qos = codec.unpack_frag_info(frag_info)
```

## Helper Functions

Global helper functions use a shared codec instance for convenience.

### make_data_packet()

Create a DATA packet for user payload.

```python
from umesh.packet import make_data_packet

pkt = make_data_packet(
    src_id=0x1234,
    dest_id=0x5678,
    payload=b'Hello',
    pkt_id=42,
    ack_req=True  # Request acknowledgement
)
```

### make_hello_packet()

Create a HELLO beacon for neighbor discovery.

```python
from umesh.packet import make_hello_packet

pkt = make_hello_packet(
    src_id=0x1234,
    hop_count=0  # Hops from gateway
)
```

Destination is automatically set to BROADCAST_ID (0xFFFF).

### make_ack_packet()

Create an ACK packet.

```python
from umesh.packet import make_ack_packet

pkt = make_ack_packet(
    src_id=0x5678,       # Acknowledger
    dest_id=0x1234,      # Original sender
    ack_pkt_id=42        # Packet ID being acknowledged
)
```

Payload contains the 16-bit ack_pkt_id.

### make_rreq_packet()

Create a Route Request (RREQ) packet.

```python
from umesh.packet import make_rreq_packet

pkt = make_rreq_packet(
    src_id=0x1234,      # Requester
    dest_id=0x9ABC,     # Destination being sought
    rreq_id=50,         # RREQ ID for deduplication
    src_seq=200         # Source sequence number
)
```

Payload format (4 bytes): [rreq_id (16-bit), src_seq (16-bit)]

### make_rrep_packet()

Create a Route Reply (RREP) packet.

```python
from umesh.packet import make_rrep_packet

pkt = make_rrep_packet(
    src_id=0x9ABC,      # Replier (destination)
    dest_id=0x1234,     # Original requester
    rreq_id=50,         # RREQ being replied to
    hop_count=3,        # Hops to destination
    metric=768          # Route quality metric
)
```

Payload format (6 bytes): [rreq_id (16-bit), hop_count (8-bit), metric (24-bit)]

### make_ping_packet()

Create a PING packet for connectivity testing.

```python
from umesh.packet import make_ping_packet

pkt = make_ping_packet(
    src_id=0x1234,
    dest_id=0x5678,
    ping_id=99
)
```

### make_pong_packet()

Create a PONG response packet.

```python
from umesh.packet import make_pong_packet

pkt = make_pong_packet(
    src_id=0x5678,
    dest_id=0x1234,
    ping_id=99  # Echo the ping_id
)
```

### make_frag_packet()

Create a fragmented DATA packet.

```python
from umesh.packet import make_frag_packet

pkt = make_frag_packet(
    src_id=0x1234,
    dest_id=0x5678,
    payload=b'Fragment 1',
    pkt_id=10,
    frag_seq=5,       # Fragment sequence
    frag_idx=0,       # This fragment's index
    frag_total=3,     # Total fragments in message
    ack_req=True      # Request ACK for this fragment
)
```

## Performance

Measured on desktop Python (non-viper fallback):

- **Encoding**: ~8 µs per packet
- **Decoding**: ~8 µs per packet
- **Validation**: ~7.5 µs per packet
- **Throughput**: 130,000+ packets/sec

On MicroPython with viper optimization, performance is expected to be even better.

## Usage Examples

### Basic Encoding/Decoding

```python
from umesh.packet import PacketCodec
from umesh.const import PKT_DATA

codec = PacketCodec()

# Encode a packet
pkt = codec.encode(
    PKT_DATA,
    src_id=0x1234,
    dest_id=0x5678,
    payload=b'Hello mesh!',
    pkt_id=42
)

# Decode the packet
parsed = codec.decode(pkt)
if parsed:
    print(f"From: 0x{parsed['src_id']:04X}")
    print(f"Payload: {parsed['payload']}")
```

### Fragmentation

```python
from umesh.packet import PacketCodec
from umesh.const import PKT_DATA, MAX_PAYLOAD

codec = PacketCodec()

# Large message to fragment
message = b"Very long message that needs fragmentation..."
frag_size = MAX_PAYLOAD

# Split into fragments
fragments = [message[i:i+frag_size]
             for i in range(0, len(message), frag_size)]

# Encode fragments
frag_seq = 42
packets = []
for idx, frag in enumerate(fragments):
    pkt = codec.encode(
        PKT_DATA,
        src_id=0x1234,
        dest_id=0x5678,
        payload=frag,
        pkt_id=100 + idx,
        flags=0x01,  # FLAG_FRAG
        frag_seq=frag_seq,
        frag_idx=idx,
        frag_total=len(fragments)
    )
    packets.append(pkt)

# Reassemble
reassembled = bytearray()
for pkt in packets:
    parsed = codec.decode(pkt)
    reassembled.extend(parsed['payload'])

assert reassembled == message
```

### Using Helper Functions

```python
from umesh.packet import (
    make_data_packet,
    make_hello_packet,
    make_ack_packet,
    PacketCodec
)

codec = PacketCodec()

# Send data with ACK request
data_pkt = make_data_packet(
    src_id=0x1234,
    dest_id=0x5678,
    payload=b'Important',
    pkt_id=42,
    ack_req=True
)

# Broadcast HELLO
hello_pkt = make_hello_packet(src_id=0x1234)

# Send ACK
ack_pkt = make_ack_packet(
    src_id=0x5678,
    dest_id=0x1234,
    ack_pkt_id=42
)

# Decode received packet
received = codec.decode(data_pkt)
if received and received['flags'] & 0x02:  # FLAG_ACK_REQ
    print("ACK requested!")
```

## Testing

Run the comprehensive test suite:

```bash
python -m pytest tests/test_packet.py -v
```

**Test Coverage:**
- 33 tests covering all functionality
- Encoding/decoding roundtrip validation
- Fragment info packing/unpacking
- Helper function correctness
- Edge cases (max values, errors, corruption)
- Performance benchmarks

All tests pass on desktop Python with fallback implementations.

## Demo Script

Run the interactive demo:

```bash
python -c "import sys; sys.path.insert(0, '.'); exec(open('examples/packet_demo.py').read())"
```

The demo showcases:
1. Basic codec usage
2. All helper functions
3. Fragmentation workflow
4. Performance benchmarks

## Implementation Notes

### Zero-Allocation Design

PacketCodec pre-allocates buffers at initialization:
- Header buffer reused across encode operations
- Parsed array reused across decode operations
- Minimizes GC pressure in hot path

### Viper Integration

Uses optimized functions from `_fast.py`:
- `_build_header()`: Viper-optimized header building with inline CRC8
- `_parse_header()`: Viper-optimized parsing with inline CRC8 validation

### Testing Compatibility

Graceful fallbacks for desktop Python testing:
- `micropython.const()` fallback in `const.py`
- `array.array()` fallback when not available
- Pure Python implementation in `_fast.py` when viper unavailable

### Memory Efficiency

- Fragment info packed into single 24-bit value
- Node IDs are 16-bit (saves 8 bytes vs full MAC)
- Pre-allocated buffers eliminate allocations
- Total codec overhead: 14 + 40 = 54 bytes

## API Reference

### PacketCodec Class

| Method | Parameters | Returns | Description |
|--------|-----------|---------|-------------|
| `__init__()` | - | - | Initialize codec with buffers |
| `encode()` | pkt_type, src_id, dest_id, ... | bytes | Build complete packet |
| `decode()` | raw: bytes | dict or None | Parse packet |
| `validate()` | raw: bytes | bool | Quick CRC check |
| `get_payload()` | raw: bytes | bytes | Extract payload |
| `make_frag_info()` | seq, idx, total, qos | int | Pack fragment info |
| `unpack_frag_info()` | frag_info: int | tuple | Unpack fragment info |

### Helper Functions

| Function | Returns | Description |
|----------|---------|-------------|
| `make_data_packet()` | bytes | User data packet |
| `make_hello_packet()` | bytes | Neighbor beacon |
| `make_ack_packet()` | bytes | Acknowledgement |
| `make_rreq_packet()` | bytes | Route request |
| `make_rrep_packet()` | bytes | Route reply |
| `make_ping_packet()` | bytes | Ping test |
| `make_pong_packet()` | bytes | Pong response |
| `make_frag_packet()` | bytes | Fragmented data |

## Next Steps

With packet encoding/decoding complete, the next layers to implement are:

1. **Routing layer** (umesh/routing.py): AODV-inspired route discovery and maintenance
2. **Fragmentation layer** (umesh/fragment.py): Message fragmentation and reassembly
3. **Transport layer** (umesh/transport.py): Reliable delivery with ACK/retransmission
4. **Mesh core** (umesh/mesh.py): Main Mesh class coordinating all layers

## References

- Protocol specification: `plan.md` lines 103-117
- Constants definitions: `umesh/const.py`
- Performance optimizations: `umesh/_fast.py`
- ESP-NOW adapter: `umesh/adapter.py`
