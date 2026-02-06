"""
µMesh - MicroPython ESP-NOW Mesh Networking
===========================================

A lightweight, memory-efficient mesh networking protocol for ESP32
microcontrollers using ESP-NOW as the physical layer.

Complete protocol stack with:
- Phase 1: Core infrastructure (adapter, packet, peer, neighbor, duplicate)
- Phase 2: Routing (AODV-lite route discovery, forwarding)
- Phase 3: Transport (fragmentation, reassembly, reliable delivery)
- Phase 4: Application (pub/sub, RPC, file transfer, diagnostics)
- Phase 5: Security (AES-128 CTR encryption)

Basic usage:
    >>> import umesh
    >>> mesh = umesh.Mesh()
    >>> await mesh.start()
    >>> await mesh.send(0x1234, b"Hello!")

Advanced usage with encryption:
    >>> config = umesh.MeshConfig(
    ...     channel=6,
    ...     lr_mode=True,
    ...     enable_encryption=True,
    ...     psk=b"my_secret_key_16"
    ... )
    >>> mesh = umesh.Mesh(config)
    >>> await mesh.start()
    >>>
    >>> # Publish/Subscribe
    >>> mesh.pubsub.subscribe("temp", lambda src, topic, data: print(data))
    >>> await mesh.pubsub.publish("temp", b"\\x17")
    >>>
    >>> # Remote Procedure Call
    >>> mesh.rpc.register("get_sensor", lambda: {"temp": 23.5})
    >>> result = await mesh.rpc.call(0x1234, "get_sensor")
    >>>
    >>> # File Transfer
    >>> await mesh.file.send_file(0x1234, "config.json", data)
    >>>
    >>> # Diagnostics
    >>> rtts = await mesh.diag.ping(0x1234, count=3)
"""

# Version information
__version__ = "0.1.0"
__author__ = "µMesh Contributors"

# =============================================================================
# MAIN INTERFACE
# =============================================================================

# Main Mesh class - import first as it's the primary API
try:
    from .mesh import Mesh
except ImportError:
    Mesh = None

# Configuration
try:
    from .config import MeshConfig
except ImportError:
    MeshConfig = None

# Constants module
try:
    from . import const
except ImportError:
    const = None

# =============================================================================
# PHASE 1: CORE INFRASTRUCTURE
# =============================================================================

# Layer 1 - Physical adapter (ESP-NOW)
try:
    from .adapter import ESPNowAdapter
except (ImportError, ModuleNotFoundError):
    # adapter requires MicroPython-specific modules (uasyncio, network, espnow)
    ESPNowAdapter = None

# Performance-optimized functions (viper/native)
try:
    from ._fast import (
        parse_header,
        build_header,
        crc8,
        dup_check,
        rssi_ewma,
        calc_lqi,
        route_lookup,
        accumulate_metric,
        VIPER_AVAILABLE,
        get_optimization_info,
    )
except ImportError:
    # _fast module not available or failed to import
    parse_header = None
    build_header = None
    crc8 = None
    dup_check = None
    rssi_ewma = None
    calc_lqi = None
    route_lookup = None
    accumulate_metric = None
    VIPER_AVAILABLE = False
    get_optimization_info = None

# Layer 2 - Packet encoding/decoding
try:
    from .packet import (
        PacketCodec,
        make_data_packet,
        make_hello_packet,
        make_ack_packet,
        make_rreq_packet,
        make_rrep_packet,
        make_ping_packet,
        make_pong_packet,
        make_frag_packet,
    )
except ImportError:
    PacketCodec = None
    make_data_packet = None
    make_hello_packet = None
    make_ack_packet = None
    make_rreq_packet = None
    make_rrep_packet = None
    make_ping_packet = None
    make_pong_packet = None
    make_frag_packet = None

# Layer 3 - Infrastructure components
try:
    from .peer import PeerManager, PeerSlot
except ImportError:
    PeerManager = None
    PeerSlot = None

try:
    from .neighbor import NeighborTable, NeighborEntry
except ImportError:
    NeighborTable = None
    NeighborEntry = None

try:
    from .duplicate import DuplicateFilter
except ImportError:
    DuplicateFilter = None

# =============================================================================
# PHASE 2: ROUTING
# =============================================================================

try:
    from .routing import RoutingTable, RouteEntry
except ImportError:
    RoutingTable = None
    RouteEntry = None

try:
    from .aodv import RouteDiscovery, PendingDiscovery
except ImportError:
    RouteDiscovery = None
    PendingDiscovery = None

try:
    from .forward import ForwardingEngine, process_incoming_packet
except ImportError:
    ForwardingEngine = None
    process_incoming_packet = None

# =============================================================================
# PHASE 3: TRANSPORT LAYER
# =============================================================================

try:
    from .transport import (
        Fragmenter,
        Reassembler,
        ReassemblyContext,
        ReliableTransport,
        PendingAck,
    )
except ImportError:
    Fragmenter = None
    Reassembler = None
    ReassemblyContext = None
    ReliableTransport = None
    PendingAck = None

# =============================================================================
# PHASE 4: APPLICATION LAYER
# =============================================================================

# Publish/Subscribe
try:
    from .pubsub import PubSub
except ImportError:
    PubSub = None

# Remote Procedure Call
try:
    from .rpc import MeshRPC, PendingCall, RemoteError
except ImportError:
    MeshRPC = None
    PendingCall = None
    RemoteError = None

# File Transfer
try:
    from .file import FileTransfer, ReceiveContext
except ImportError:
    FileTransfer = None
    ReceiveContext = None

# Network Diagnostics
try:
    from .diag import (
        MeshDiag,
        get_mesh_stats,
        format_stats,
        format_neighbors,
        format_routes,
    )
except ImportError:
    MeshDiag = None
    get_mesh_stats = None
    format_stats = None
    format_neighbors = None
    format_routes = None

# =============================================================================
# PHASE 5: SECURITY
# =============================================================================

try:
    from .crypto import (
        MeshCrypto,
        is_encrypted_payload,
        NONCE_SIZE,
        KEY_SIZE,
        BLOCK_SIZE,
    )
except ImportError:
    MeshCrypto = None
    is_encrypted_payload = None
    NONCE_SIZE = None
    KEY_SIZE = None
    BLOCK_SIZE = None

# Utilities
try:
    from . import utils
except ImportError:
    utils = None

# =============================================================================
# PUBLIC API EXPORTS
# =============================================================================

__all__ = [
    # Version
    "__version__",
    "__author__",

    # =========================================================================
    # MAIN INTERFACE
    # =========================================================================
    "Mesh",
    "MeshConfig",
    "const",

    # =========================================================================
    # PHASE 1: CORE INFRASTRUCTURE
    # =========================================================================

    # Layer 1 - Physical adapter
    "ESPNowAdapter",

    # Performance-optimized functions
    "parse_header",
    "build_header",
    "crc8",
    "dup_check",
    "rssi_ewma",
    "calc_lqi",
    "route_lookup",
    "accumulate_metric",
    "VIPER_AVAILABLE",
    "get_optimization_info",

    # Layer 2 - Packet codec
    "PacketCodec",
    "make_data_packet",
    "make_hello_packet",
    "make_ack_packet",
    "make_rreq_packet",
    "make_rrep_packet",
    "make_ping_packet",
    "make_pong_packet",
    "make_frag_packet",

    # Layer 3 - Infrastructure
    "PeerManager",
    "PeerSlot",
    "NeighborTable",
    "NeighborEntry",
    "DuplicateFilter",

    # =========================================================================
    # PHASE 2: ROUTING
    # =========================================================================
    "RoutingTable",
    "RouteEntry",
    "RouteDiscovery",
    "PendingDiscovery",
    "ForwardingEngine",
    "process_incoming_packet",

    # =========================================================================
    # PHASE 3: TRANSPORT LAYER
    # =========================================================================
    "Fragmenter",
    "Reassembler",
    "ReassemblyContext",
    "ReliableTransport",
    "PendingAck",

    # =========================================================================
    # PHASE 4: APPLICATION LAYER
    # =========================================================================

    # Publish/Subscribe
    "PubSub",

    # Remote Procedure Call
    "MeshRPC",
    "PendingCall",
    "RemoteError",

    # File Transfer
    "FileTransfer",
    "ReceiveContext",

    # Network Diagnostics
    "MeshDiag",
    "get_mesh_stats",
    "format_stats",
    "format_neighbors",
    "format_routes",

    # =========================================================================
    # PHASE 5: SECURITY
    # =========================================================================
    "MeshCrypto",
    "is_encrypted_payload",
    "NONCE_SIZE",
    "KEY_SIZE",
    "BLOCK_SIZE",

    # Utilities
    "utils",
]
