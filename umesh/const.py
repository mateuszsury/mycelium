"""
µMesh Protocol Constants
========================

Memory-optimized constants for MicroPython ESP32 mesh networking.
All integer constants use micropython.const() to save RAM.

Based on µMesh protocol specification:
- 14-byte header with CRC8 validation
- 16 packet types for various mesh operations
- AODV-inspired routing with proactive HELLO beacons
- Fragmentation support for payloads up to 3540 bytes
"""

try:
    from micropython import const
except ImportError:
    # Fallback for non-MicroPython environments (testing)
    def const(x):
        return x

# Protocol version
PROTO_VERSION = const(1)

# Packet types (4 bits = 16 types)
PKT_DATA = const(0x0)         # User data packet
PKT_DATA_FRAG = const(0x1)    # Fragmented data packet
PKT_ACK = const(0x2)          # Acknowledgement
PKT_NACK = const(0x3)         # Negative acknowledgement (retransmit request)
PKT_RREQ = const(0x4)         # Route Request (AODV)
PKT_RREP = const(0x5)         # Route Reply (AODV)
PKT_RERR = const(0x6)         # Route Error
PKT_HELLO = const(0x7)        # Neighbor beacon (proactive)
PKT_PING = const(0x8)         # Mesh ping
PKT_PONG = const(0x9)         # Mesh pong response
PKT_TOPO = const(0xA)         # Topology update
PKT_SYNC = const(0xB)         # Time synchronization
PKT_PUB = const(0xC)          # Publish (Pub/Sub)
PKT_SUB = const(0xD)          # Subscribe
PKT_RPC = const(0xE)          # Remote Procedure Call
PKT_CTRL = const(0xF)         # Control (OTA, reboot, config)

# Packet structure sizes (bytes)
HEADER_SIZE = const(14)       # µMesh header size
MAX_PAYLOAD = const(236)      # Max payload per packet (250 - 14)
MAX_PACKET = const(250)       # ESP-NOW max packet size

# Timing constants (milliseconds)
NEIGHBOR_TIMEOUT = const(60000)     # 60s - neighbor expires after no HELLO
HELLO_INTERVAL = const(10000)       # 10s - proactive HELLO beacon interval
ROUTE_TIMEOUT = const(120000)       # 120s - route entry expiration
ACTIVE_TIMEOUT = const(300000)      # 300s - active route lifetime extension
ACK_TIMEOUT = const(500)            # 500ms - ACK wait timeout
REASSEMBLY_TIMEOUT = const(10000)   # 10s - fragment reassembly timeout
PENDING_TIMEOUT = const(5000)       # 5s - pending transmission timeout

# Protocol limits
MAX_TTL = const(15)           # Maximum Time-To-Live (4 bits)
DEFAULT_TTL = const(7)        # Default TTL for new packets
MAX_PEERS = const(19)         # ESP-NOW peer limit (20 total - 1 broadcast)
MAX_ROUTES = const(64)        # Maximum routing table entries
MAX_FRAGMENTS = const(15)     # Maximum fragments per message (4 bits)
WINDOW_SIZE = const(32)       # Duplicate detection sliding window size
MAX_SOURCES = const(64)       # Maximum tracked source nodes for deduplication
MAX_PENDING = const(8)        # Maximum pending transmissions queue size
MAX_RETRIES = const(3)        # Maximum retransmission attempts

# Packet flags (1 byte)
FLAG_FRAG = const(0x01)       # Packet is fragmented
FLAG_ACK_REQ = const(0x02)    # Acknowledgement requested
FLAG_ENCRYPTED = const(0x04)  # Payload is encrypted
FLAG_ROUTED = const(0x08)     # Packet has been routed (not direct neighbor)

# Special addresses
BROADCAST_ID = const(0xFFFF)  # 16-bit broadcast node ID
BROADCAST_MAC = b'\xff\xff\xff\xff\xff\xff'  # ESP-NOW broadcast MAC

# Node roles (bitmask)
ROLE_ROUTER = const(0x01)     # Node routes packets for others
ROLE_LEAF = const(0x02)       # Leaf node (end device, minimal routing)
ROLE_GATEWAY = const(0x04)    # Gateway to external network (WiFi/LTE/LoRa)

# Fragmentation constants
FRAG_SEQ_BITS = const(12)     # Fragment sequence number bits
FRAG_INDEX_BITS = const(4)    # Fragment index bits (0-15)
FRAG_TOTAL_BITS = const(4)    # Total fragments bits (1-15)
MAX_MESSAGE_SIZE = const(3540)  # Max fragmented message (15 × 236 bytes)

# CRC polynomial for header validation
CRC8_POLY = const(0x07)       # CRC-8 polynomial (x^8 + x^2 + x + 1)

# Forwarding engine constants
PENDING_QUEUE_SIZE = const(8)     # Max packets waiting for route discovery
REBROADCAST_JITTER = const(50)    # ms - max random delay before rebroadcast
