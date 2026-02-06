"""
µMesh Main Interface
====================

Complete implementation integrating all phases of the µMesh protocol:
- Phase 1: Core (adapter, packet, peer, neighbor, duplicate)
- Phase 2: Routing (routing table, AODV, forwarding)
- Phase 3: Transport (fragmentation, reassembly, reliable delivery)
- Phase 4: Application (pubsub, rpc, diagnostics, file transfer)
- Phase 5: Security (crypto, utils)

Basic usage:
    >>> import umesh
    >>> mesh = umesh.Mesh()  # Auto-configure from MAC
    >>> await mesh.start()
    >>>
    >>> # Register message handler
    >>> mesh.on_message(lambda src, data: print(f"From {src:04x}: {data}"))
    >>>
    >>> # Send to node (routed automatically)
    >>> await mesh.send(0x1234, b"Hello!")
    >>>
    >>> # Send broadcast
    >>> await mesh.send(0xFFFF, b"Hello mesh!")
    >>>
    >>> # Reliable delivery
    >>> await mesh.send_reliable(0x1234, b"Important!")
    >>>
    >>> # Access advanced features
    >>> await mesh.pubsub.publish("temp/room1", b"\x17")
    >>> result = await mesh.rpc.call(0x1234, "get_sensor")
    >>> await mesh.file.send_file(0x1234, "config.json", data)
"""

try:
    import uasyncio as asyncio
except ImportError:
    import asyncio

# Add sleep_ms compatibility for standard asyncio (CPython fallback)
if not hasattr(asyncio, 'sleep_ms'):
    async def _sleep_ms(ms):
        await asyncio.sleep(ms / 1000.0)
    asyncio.sleep_ms = _sleep_ms

try:
    import gc
    GC_AVAILABLE = True
except ImportError:
    GC_AVAILABLE = False

try:
    import urandom as random
except ImportError:
    import random

try:
    from utime import ticks_ms, ticks_diff, sleep_ms
except ImportError:
    # Fallback for desktop Python
    from time import monotonic
    ticks_ms = lambda: int(monotonic() * 1000)
    ticks_diff = lambda a, b: a - b
    sleep_ms = lambda ms: None

from .config import MeshConfig
from .adapter import ESPNowAdapter
from .packet import PacketCodec
from .peer import PeerManager
from .neighbor import NeighborTable
from .duplicate import DuplicateFilter
from .const import (
    PKT_DATA, PKT_DATA_FRAG, PKT_HELLO, PKT_PING, PKT_PONG,
    PKT_ACK, PKT_NACK, PKT_RREQ, PKT_RREP, PKT_RERR,
    PKT_PUB, PKT_SUB, PKT_RPC, PKT_CTRL,
    PKT_TOPO, PKT_SYNC,  # Topology and time sync packets
    BROADCAST_ID, BROADCAST_MAC,
    DEFAULT_TTL, HELLO_INTERVAL, MAX_PAYLOAD,
    FLAG_ENCRYPTED, FLAG_FRAG, FLAG_ACK_REQ,
    ROLE_ROUTER, ROLE_LEAF, ROLE_GATEWAY,
)


# Maintenance interval (30 seconds)
MAINTENANCE_INTERVAL_MS = 30_000

# Broadcast jitter max (50ms)
BROADCAST_JITTER_MS = 50


class Mesh:
    """
    Main µMesh interface - Complete ESP-NOW mesh networking stack.

    The Mesh class integrates all protocol components:

    Phase 1 (Core):
    - ESPNowAdapter: Layer 1 physical ESP-NOW communication
    - PacketCodec: Packet encoding/decoding with CRC validation
    - PeerManager: ESP-NOW peer slot management with LRU eviction
    - NeighborTable: Direct neighbor tracking with LQI calculation
    - DuplicateFilter: Sliding window duplicate packet detection

    Phase 2 (Routing):
    - RoutingTable: Destination-based routing with sequence numbers
    - RouteDiscovery: AODV-lite route discovery with expanding ring
    - ForwardingEngine: Multi-hop packet forwarding

    Phase 3 (Transport):
    - Fragmenter: Large message fragmentation (up to 3540 bytes)
    - Reassembler: Fragment reassembly
    - ReliableTransport: ACK-based reliable delivery

    Phase 4 (Application - lazy loaded):
    - PubSub: Topic-based publish/subscribe
    - MeshRPC: Remote procedure calls
    - FileTransfer: File transfer with CRC32 verification
    - MeshDiag: Network diagnostics (ping, traceroute, stats)

    Phase 5 (Security):
    - MeshCrypto: AES-128 CTR mode encryption

    Configuration:
        config: MeshConfig instance or None for auto-configuration

    Auto-configuration (when config=None):
        - node_id: Derived from last 2 bytes of MAC address
        - channel: 1
        - hello_interval: 10s
        - All other parameters: defaults from MeshConfig

    Example:
        # Auto-configure
        mesh = Mesh()
        await mesh.start()

        # Explicit configuration with encryption
        config = MeshConfig(
            node_id=0x0042,
            channel=6,
            lr_mode=True,
            enable_encryption=True,
            psk=b"my_secret_key_16"
        )
        mesh = Mesh(config)
        await mesh.start()
    """

    def __init__(self, config=None):
        """
        Initialize mesh with optional configuration.

        Args:
            config: MeshConfig instance or None for auto-configuration
        """
        # Initialize adapter first to get MAC address
        self._adapter = None
        self._codec = PacketCodec()
        self._config = None
        self._node_id = None

        # Initialize components
        self._init_adapter_and_config(config)

        # Phase 1: Core components
        self._peer_mgr = PeerManager(self._adapter)
        self._neighbors = NeighborTable(
            timeout=self._config.route_timeout,
            hello_interval=self._config.hello_interval,
            max_neighbors=64
        )
        self._duplicates = DuplicateFilter(
            window_size=self._config.duplicate_window,
            max_sources=64
        )

        # Phase 2: Routing (always initialized)
        from .routing import RoutingTable
        from .aodv import RouteDiscovery
        from .forward import ForwardingEngine

        self._routes = RoutingTable()
        self._discovery = RouteDiscovery(self, self._routes)
        self._forward = ForwardingEngine(self, self._routes, self._discovery)

        # Phase 3: Transport
        from .transport import Fragmenter, Reassembler, ReliableTransport

        self._fragmenter = Fragmenter()
        self._reassembler = Reassembler()
        self._reliable = ReliableTransport(self)

        # Phase 4: Application (lazy loaded)
        self._pubsub = None
        self._rpc = None
        self._file = None
        self._diag = None

        # Phase 5: Security
        self._crypto = None
        if self._config.enable_encryption and self._config.psk:
            from .crypto import MeshCrypto
            self._crypto = MeshCrypto(self._config.psk)

        # Packet ID counter (for sending)
        self._packet_id = 0

        # Message callback
        self._message_callback = None

        # Event callbacks
        self._on_neighbor_join = None
        self._on_neighbor_leave = None
        self._on_route_change = None
        self._on_route_error = None
        self._on_topo = None
        self._on_sync = None

        # Message queue for async iteration
        self._message_queue = asyncio.Queue(maxsize=16)

        # Statistics
        self._stats = {
            'tx_packets': 0,
            'rx_packets': 0,
            'tx_bytes': 0,
            'rx_bytes': 0,
            'dropped_duplicates': 0,
            'dropped_crc_fail': 0,
            'dropped_decrypt_fail': 0,
            'hello_sent': 0,
            'hello_received': 0,
            'fragments_sent': 0,
            'fragments_received': 0,
            'messages_reassembled': 0,
            'routes_discovered': 0,
            'packets_forwarded': 0,
            'encrypt_errors': 0,
            'callback_errors': 0,
            'queue_overflows': 0,
            'processing_errors': 0,
            'topo_received': 0,
            'sync_received': 0,
        }

        # Background tasks
        self._tasks = []
        self._running = False

        # Node role
        self._role = ROLE_ROUTER  # Default: router

    def _init_adapter_and_config(self, config):
        """Initialize adapter and derive node_id from MAC if needed."""
        # Create adapter with basic config
        channel = config.channel if config else 1
        lr_mode = config.lr_mode if config else False
        rx_buf = config.rx_buf if config else 2048

        self._adapter = ESPNowAdapter(
            channel=channel,
            lr_mode=lr_mode,
            rx_buf=rx_buf
        )

        # Get MAC address for node_id derivation
        mac = self._adapter.mac

        # Create config with auto node_id if needed
        if config is None:
            # Auto-derive node_id from last 2 bytes of MAC
            auto_node_id = (mac[4] << 8) | mac[5]
            self._config = MeshConfig(node_id=auto_node_id)
            self._node_id = auto_node_id
        else:
            # Use provided config
            if config.node_id is None:
                # Derive node_id from MAC
                auto_node_id = (mac[4] << 8) | mac[5]
                config.node_id = auto_node_id
                self._node_id = auto_node_id
            else:
                self._node_id = config.node_id
            self._config = config

    async def start(self):
        """
        Start mesh networking.

        Creates background asyncio tasks for:
        - _rx_loop(): Receive and process packets
        - _hello_loop(): Send periodic HELLO beacons
        - _maintenance(): Cleanup dead neighbors, expired entries, GC

        Returns when all tasks are created and running.
        """
        if self._running:
            return

        self._running = True

        # Create background tasks
        self._tasks = [
            asyncio.create_task(self._rx_loop()),
            asyncio.create_task(self._hello_loop()),
            asyncio.create_task(self._maintenance()),
        ]

    async def stop(self):
        """
        Stop mesh networking.

        Cancels all background tasks and waits for cleanup with timeout.
        """
        if not self._running:
            return

        self._running = False

        # Cancel all tasks
        for task in self._tasks:
            task.cancel()

        # Wait for all tasks to finish with timeout
        for task in self._tasks:
            try:
                await asyncio.wait_for(task, timeout=2.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
            except Exception as e:
                print(f"Error stopping task: {e}")

        self._tasks = []

    # =========================================================================
    # PUBLIC API - Message handlers and sending
    # =========================================================================

    def on_message(self, callback):
        """
        Register callback for received DATA packets.

        Args:
            callback: Function with signature callback(src_node_id: int, data: bytes)

        Example:
            >>> mesh.on_message(lambda src, data: print(f"From {src:04x}: {data}"))
        """
        self._message_callback = callback

    def on_neighbor_join(self, callback):
        """Register callback for new neighbor: callback(mac: bytes, node_id: int, rssi: int)"""
        self._on_neighbor_join = callback

    def on_neighbor_leave(self, callback):
        """Register callback for lost neighbor: callback(node_id: int)"""
        self._on_neighbor_leave = callback

    def on_route_change(self, callback):
        """Register callback for route changes: callback(dest_id: int, next_hop_id: int, metric: int)"""
        self._on_route_change = callback

    def on_route_error(self, callback):
        """Register callback for route errors: callback(dest_id: int)"""
        self._on_route_error = callback

    async def send(self, dest_id: int, data: bytes, reliable: bool = False,
                   pkt_type: int = None) -> bool:
        """
        Send data to destination with optional reliability.

        Automatically handles:
        - Encryption (if enabled)
        - Fragmentation (if data > MAX_PAYLOAD)
        - Routing (AODV route discovery if needed)
        - Reliable delivery (ACK/retries if reliable=True)

        Args:
            dest_id: Destination node ID (0xFFFF for broadcast)
            data: Payload bytes (max 3540 bytes for fragmented, 236 for single)
            reliable: If True, use ACK-based reliable delivery
            pkt_type: Packet type override (default: PKT_DATA)

        Returns:
            True if send succeeded, False otherwise

        Raises:
            ValueError: If data exceeds maximum size
        """
        if pkt_type is None:
            pkt_type = PKT_DATA
        # Validate payload size
        max_size = MAX_PAYLOAD if not self._fragmenter.needs_fragmentation(data) else self._fragmenter.MAX_MESSAGE
        if len(data) > max_size:
            raise ValueError(f"Payload too large: {len(data)} bytes (max {max_size})")

        # Encrypt if enabled
        if self._crypto:
            try:
                pkt_id = self._next_packet_id()
                data = self._crypto.encrypt(data, self._node_id, pkt_id)
            except Exception as e:
                print(f"Encryption failed: {e}")
                self._stats['encrypt_errors'] = self._stats.get('encrypt_errors', 0) + 1
                return False

        # Use reliable transport if requested
        if reliable:
            return await self.send_reliable(dest_id, data)

        # Fragment if needed
        if self._fragmenter.needs_fragmentation(data):
            return await self._send_fragmented(dest_id, data)

        # Route or broadcast
        if dest_id == BROADCAST_ID:
            return await self._send_broadcast(data, pkt_type=pkt_type)
        else:
            return await self._send_routed(dest_id, data, pkt_type=pkt_type)

    async def send_reliable(self, dest_id: int, data: bytes, timeout: int = 5000) -> bool:
        """
        Send with acknowledgment and retries.

        Uses ReliableTransport for ACK-based delivery with exponential backoff.

        Args:
            dest_id: Destination node ID
            data: Payload bytes
            timeout: Total timeout in milliseconds

        Returns:
            True if ACK received, False on timeout/failure
        """
        return await self._reliable.send_reliable(dest_id, data, timeout)

    async def broadcast(self, data: bytes, reliable: bool = False) -> bool:
        """
        Broadcast data to all nodes.

        Convenience method equivalent to send(BROADCAST_ID, data).

        Args:
            data: Payload bytes
            reliable: If True, use ACK-based reliable delivery (note: broadcasts
                      don't typically use reliability, use with caution)

        Returns:
            True if send succeeded, False otherwise
        """
        return await self.send(BROADCAST_ID, data, reliable=reliable)

    async def send_raw(self, packet: bytes, dest_mac: bytes = None) -> bool:
        """
        Send raw packet (for internal use by transport/routing layers).

        Args:
            packet: Complete packet bytes (header + payload)
            dest_mac: Destination MAC or None for broadcast

        Returns:
            True if send succeeded, False otherwise
        """
        if dest_mac is None or dest_mac == BROADCAST_MAC:
            success = await self._adapter.send_broadcast(packet)
        else:
            # Ensure peer is registered
            await self._peer_mgr.ensure_peer(dest_mac)
            success = await self._adapter.send(dest_mac, packet)

        if success:
            self._stats['tx_packets'] += 1
            self._stats['tx_bytes'] += len(packet)

        return success

    # =========================================================================
    # PROPERTIES - Access to components and state
    # =========================================================================

    @property
    def node_id(self) -> int:
        """This node's 16-bit ID."""
        return self._node_id

    @property
    def mac(self) -> bytes:
        """This node's 6-byte MAC address."""
        return self._adapter.mac

    @property
    def config(self) -> MeshConfig:
        """Current mesh configuration."""
        return self._config

    @property
    def neighbors(self) -> list:
        """
        List of direct neighbors with link quality metrics.

        Returns:
            List of tuples (node_id, mac, rssi, lqi) sorted by LQI descending
        """
        neighbors = []
        for node_id in list(self._neighbors._neighbors.keys()):
            info = self._neighbors.get_neighbor(node_id)
            if info:
                neighbors.append((
                    node_id,
                    info['mac'],
                    info['rssi_avg'],
                    info['lqi']
                ))

        neighbors.sort(key=lambda x: x[3], reverse=True)
        return neighbors

    @property
    def routes(self):
        """
        Access routing table.

        Returns:
            RoutingTable instance for route inspection/management
        """
        return self._routes

    @property
    def nodes(self) -> set:
        """
        Set of all known node IDs (neighbors + routing destinations).

        Returns:
            Set of node IDs known to this mesh node, excluding broadcast ID.
        """
        known = set()
        # Add neighbors
        for node_id in self._neighbors._neighbors.keys():
            known.add(node_id)
        # Add routing destinations
        for dest_id in self._routes.destinations():
            known.add(dest_id)
        # Exclude broadcast ID
        known.discard(BROADCAST_ID)
        return known

    @property
    def stats(self) -> dict:
        """
        Statistics dictionary with comprehensive counters.

        Returns:
            Dictionary with packet counts, bytes, drops, etc.
        """
        return self._stats.copy()

    def set_role(self, role: int):
        """Set node role: ROLE_ROUTER, ROLE_LEAF, or ROLE_GATEWAY."""
        if role not in (ROLE_ROUTER, ROLE_LEAF, ROLE_GATEWAY):
            raise ValueError("Invalid role")
        self._role = role

    @property
    def role(self) -> int:
        """Current node role."""
        return self._role

    def topology(self) -> dict:
        """Return network topology map."""
        neighbors_list = []
        for node_id, mac, rssi, lqi in self.neighbors:
            neighbors_list.append({
                'node_id': node_id,
                'mac': mac,
                'rssi': rssi,
                'lqi': lqi
            })

        routes_list = []
        for dest_id in self._routes.destinations():
            route = self._routes.lookup(dest_id)
            if route:
                routes_list.append({
                    'dest': dest_id,
                    'next_hop': route.next_hop_id,
                    'metric': route.metric,
                    'hops': route.hop_count
                })

        return {
            'node_id': self._node_id,
            'role': self._role,
            'neighbors': neighbors_list,
            'routes': routes_list
        }

    @property
    def pubsub(self):
        """
        Lazy-loaded Pub/Sub interface.

        Returns:
            PubSub instance for topic-based messaging

        Example:
            >>> mesh.pubsub.subscribe("temp", lambda src, topic, data: print(data))
            >>> await mesh.pubsub.publish("temp", b"\x17")
        """
        if self._pubsub is None:
            from .pubsub import PubSub
            self._pubsub = PubSub(self)
        return self._pubsub

    @property
    def rpc(self):
        """
        Lazy-loaded RPC interface.

        Returns:
            MeshRPC instance for remote procedure calls

        Example:
            >>> mesh.rpc.register("get_temp", lambda: 23.5)
            >>> result = await mesh.rpc.call(0x1234, "get_temp")
        """
        if self._rpc is None:
            from .rpc import MeshRPC
            self._rpc = MeshRPC(self, self._reliable)
        return self._rpc

    @property
    def file(self):
        """
        Lazy-loaded file transfer interface.

        Returns:
            FileTransfer instance for file transfers

        Example:
            >>> await mesh.file.send_file(0x1234, "config.json", data)
            >>> filename, data = await mesh.file.recv_file()
        """
        if self._file is None:
            from .file import FileTransfer
            self._file = FileTransfer(self, self._reliable)
        return self._file

    @property
    def diag(self):
        """
        Get or create diagnostics interface.

        Returns:
            MeshDiag instance for ping, traceroute, and statistics

        Example:
            >>> rtts = await mesh.diag.ping(0x1234, count=3)
            >>> stats = get_mesh_stats(mesh)
            >>> print(format_stats(stats))
        """
        if self._diag is None:
            from .diag import MeshDiag
            self._diag = MeshDiag(self)
        return self._diag

    # =========================================================================
    # INTERNAL METHODS - Background loops
    # =========================================================================

    async def _rx_loop(self):
        """
        Main receive loop using async for over adapter.

        Receives packets, validates them, checks for duplicates,
        and dispatches to appropriate handlers based on packet type.
        """
        try:
            async for mac, raw, rssi in self._adapter:
                try:
                    await self._process_packet(mac, raw, rssi)
                except Exception as e:
                    self._stats['processing_errors'] = self._stats.get('processing_errors', 0) + 1
        except asyncio.CancelledError:
            pass
        except Exception as e:
            print(f"Error in rx_loop: {e}")

    async def _hello_loop(self):
        """
        Periodic HELLO beacon transmission.

        Sends HELLO packets at configured interval to announce presence
        to direct neighbors.
        """
        try:
            while self._running:
                await self._send_hello()
                await asyncio.sleep_ms(self._config.hello_interval)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            print(f"Error in hello_loop: {e}")

    async def _maintenance(self):
        """
        Periodic cleanup and garbage collection.

        Runs every 30 seconds to:
        - Prune dead neighbors
        - Prune expired routes
        - Prune stale duplicate filter entries
        - Prune stale reassembly contexts
        - Prune RREQ seen cache
        - Run garbage collection
        """
        try:
            while self._running:
                await asyncio.sleep_ms(MAINTENANCE_INTERVAL_MS)

                # Prune dead neighbors
                self._neighbors.prune_dead()

                # Prune expired routes
                self._routes.prune_expired()

                # Prune duplicate filter
                self._duplicates.prune()

                # Prune reassembly contexts
                self._reassembler.prune_stale()

                # Prune RREQ seen cache
                self._discovery.prune_seen_rreq()

                # Garbage collection
                if GC_AVAILABLE:
                    gc.collect()
        except asyncio.CancelledError:
            pass
        except Exception as e:
            print(f"Error in maintenance: {e}")

    # =========================================================================
    # INTERNAL METHODS - Packet processing
    # =========================================================================

    async def _process_packet(self, mac: bytes, raw: bytes, rssi: int):
        """
        Process received packet based on type.

        Full packet processing pipeline:
        1. Decode header, validate CRC
        2. Check for duplicates
        3. Decrypt if encrypted
        4. Handle fragments
        5. Route to appropriate handler

        Args:
            mac: Sender MAC address (6 bytes)
            raw: Raw packet bytes
            rssi: Signal strength in dBm
        """
        # Update stats
        self._stats['rx_bytes'] += len(raw)

        # Decode packet
        parsed = self._codec.decode(raw)
        if parsed is None:
            self._stats['dropped_crc_fail'] += 1
            return

        # Extract header fields
        src_id = parsed['src_id']
        dest_id = parsed['dest_id']
        pkt_id = parsed['pkt_id']
        pkt_type = parsed['pkt_type']
        flags = parsed['flags']
        ttl = parsed['ttl']
        hop_count = parsed['hop_count']

        # Check for duplicates (except HELLO packets which may be intentionally repeated)
        if pkt_type != PKT_HELLO:
            if self._duplicates.is_duplicate(src_id, pkt_id):
                self._stats['dropped_duplicates'] += 1
                return
            self._duplicates.record(src_id, pkt_id)

        # Update stats
        self._stats['rx_packets'] += 1

        # Get payload
        payload = parsed['payload']

        # Decrypt if encrypted
        if flags & FLAG_ENCRYPTED:
            if self._crypto:
                try:
                    payload = self._crypto.decrypt(payload)
                except Exception:
                    self._stats['dropped_decrypt_fail'] += 1
                    return
            else:
                # Encrypted but no crypto configured - drop
                self._stats['dropped_decrypt_fail'] += 1
                return

        # Update parsed payload after decryption
        parsed['payload'] = payload

        # Dispatch based on packet type
        if pkt_type == PKT_HELLO:
            self._handle_hello(mac, parsed, rssi)
        elif pkt_type == PKT_DATA:
            await self._handle_data_or_forward(mac, parsed, raw, rssi)
        elif pkt_type == PKT_DATA_FRAG:
            await self._handle_fragment(mac, parsed, raw, rssi)
        elif pkt_type == PKT_ACK:
            await self._handle_ack(src_id, payload)
        elif pkt_type == PKT_NACK:
            await self._handle_nack(src_id, payload)
        elif pkt_type == PKT_RREQ:
            await self._discovery.handle_rreq(mac, parsed, payload, rssi)
        elif pkt_type == PKT_RREP:
            await self._discovery.handle_rrep(mac, parsed, payload, rssi)
        elif pkt_type == PKT_RERR:
            await self._discovery.handle_rerr(mac, parsed, payload)
        elif pkt_type == PKT_PING:
            await self._handle_ping(src_id, payload, rssi)
        elif pkt_type == PKT_PONG:
            self._handle_pong(src_id, payload, rssi)
        elif pkt_type == PKT_PUB:
            if self._pubsub:
                self._pubsub.handle_pub(src_id, payload)
        elif pkt_type == PKT_SUB:
            if self._pubsub:
                self._pubsub.handle_sub(src_id, payload)
        elif pkt_type == PKT_RPC:
            if self._rpc:
                # Dispatch RPC based on FLAG_RESPONSE in flags byte (payload[2])
                # RPC packet format: call_id(2) + flags(1) + data...
                if len(payload) >= 3:
                    rpc_flags = payload[2]
                    _RPC_FLAG_RESPONSE = 0x01
                    if rpc_flags & _RPC_FLAG_RESPONSE:
                        self._rpc.handle_rpc_response(src_id, payload)
                    else:
                        self._rpc.handle_rpc_request(src_id, payload)
        elif pkt_type == PKT_CTRL:
            await self._handle_ctrl(src_id, payload)
        elif pkt_type == PKT_TOPO:
            await self._handle_topo(src_id, payload)
        elif pkt_type == PKT_SYNC:
            await self._handle_sync(src_id, payload)

    def _handle_hello(self, mac: bytes, parsed: dict, rssi: int):
        """
        Handle HELLO packet - update neighbor table.

        HELLO payload format:
        - bytes 0-1: node_id (uint16, big-endian)
        - byte 2: hop_count (always 0 for direct neighbors)

        Args:
            mac: Sender MAC address
            parsed: Parsed packet dictionary
            rssi: Signal strength in dBm
        """
        payload = parsed['payload']

        if len(payload) < 3:
            return  # Invalid HELLO

        # Extract node_id and hop_count
        sender_node_id = (payload[0] << 8) | payload[1]
        hop_count = payload[2]

        # Update neighbor table
        try:
            self._neighbors.update(mac, sender_node_id, rssi, hop_count)
            self._stats['hello_received'] += 1
        except MemoryError:
            pass  # Neighbor table full

    async def _handle_data_or_forward(self, mac: bytes, parsed: dict, raw: bytes, rssi: int):
        """
        Handle DATA packet - deliver locally or forward.

        Args:
            mac: Sender MAC address
            parsed: Parsed packet dictionary
            raw: Raw packet bytes
            rssi: Signal strength in dBm
        """
        src_id = parsed['src_id']
        dest_id = parsed['dest_id']
        payload = parsed['payload']

        # Check if packet is for us or broadcast
        if dest_id == BROADCAST_ID or dest_id == self._node_id:
            # Deliver locally
            if self._message_callback:
                try:
                    self._message_callback(src_id, payload)
                except Exception as e:
                    print(f"Error in message callback: {e}")
                    self._stats['callback_errors'] = self._stats.get('callback_errors', 0) + 1

            # Add to message queue
            try:
                self._message_queue.put_nowait((src_id, payload))
            except asyncio.QueueFull:
                self._stats['queue_overflows'] = self._stats.get('queue_overflows', 0) + 1
                try:
                    self._message_queue.get_nowait()
                    self._message_queue.put_nowait((src_id, payload))
                except Exception:
                    pass

        # Forward if not for us and TTL allows
        if dest_id != self._node_id:
            await self._forward.forward(raw, mac, rssi)

    async def _handle_fragment(self, mac: bytes, parsed: dict, raw: bytes, rssi: int):
        """
        Handle fragmented data packet.

        Args:
            mac: Sender MAC address
            parsed: Parsed packet dictionary
            raw: Raw packet bytes
            rssi: Signal strength in dBm
        """
        src_id = parsed['src_id']
        dest_id = parsed['dest_id']
        frag_seq = parsed.get('frag_seq', 0)
        frag_idx = parsed.get('frag_idx', 0)
        frag_total = parsed.get('frag_total', 1)
        payload = parsed['payload']

        self._stats['fragments_received'] += 1

        # Check if fragment is for us or needs forwarding
        if dest_id == self._node_id or dest_id == BROADCAST_ID:
            # Try to reassemble
            complete_msg = self._reassembler.add_fragment(
                src_id, frag_seq, frag_idx, frag_total, payload
            )

            if complete_msg:
                self._stats['messages_reassembled'] += 1
                # Deliver reassembled message
                if self._message_callback:
                    try:
                        self._message_callback(src_id, complete_msg)
                    except Exception as e:
                        print(f"Error in message callback: {e}")
                        self._stats['callback_errors'] = self._stats.get('callback_errors', 0) + 1

                try:
                    self._message_queue.put_nowait((src_id, complete_msg))
                except asyncio.QueueFull:
                    self._stats['queue_overflows'] = self._stats.get('queue_overflows', 0) + 1
                    try:
                        self._message_queue.get_nowait()
                        self._message_queue.put_nowait((src_id, complete_msg))
                    except Exception:
                        pass

        # Forward if not exclusively for us
        if dest_id != self._node_id:
            await self._forward.forward(raw, mac, rssi)

    async def _handle_ack(self, src_id: int, payload: bytes):
        """
        Handle ACK packet - pass to reliable transport.

        Args:
            src_id: Source node ID
            payload: ACK payload
        """
        if len(payload) >= 2:
            ack_pkt_id = (payload[0] << 8) | payload[1]
            await self._reliable.handle_ack(src_id, ack_pkt_id)

    async def _handle_nack(self, src_id: int, payload: bytes):
        """
        Handle NACK packet - pass to reliable transport.

        Args:
            src_id: Source node ID
            payload: NACK payload
        """
        if len(payload) >= 2:
            nack_pkt_id = (payload[0] << 8) | payload[1]
            # Extract missing fragment indices if present
            missing = []
            if len(payload) > 2:
                for i in range(2, len(payload)):
                    missing.append(payload[i])
            await self._reliable.handle_nack(src_id, nack_pkt_id, missing)

    async def _handle_ping(self, src_id: int, payload: bytes, rssi: int):
        """
        Handle PING packet - delegate to diagnostics or send simple PONG.

        Args:
            src_id: Source node ID
            payload: PING payload
            rssi: Signal strength in dBm
        """
        if self._diag:
            await self._diag.handle_ping(src_id, payload, rssi)
        else:
            # Simple PONG response
            pong = self._codec.encode(
                pkt_type=PKT_PONG,
                src_id=self._node_id,
                dest_id=src_id,
                payload=payload,  # Echo payload
                pkt_id=self._next_packet_id(),
                ttl=DEFAULT_TTL,
                hop_count=0
            )
            await self.send_raw(pong)

    def _handle_pong(self, src_id: int, payload: bytes, rssi: int):
        """
        Handle PONG packet - delegate to diagnostics.

        Args:
            src_id: Source node ID
            payload: PONG payload
            rssi: Signal strength in dBm
        """
        if self._diag:
            self._diag.handle_pong(src_id, payload, rssi)

    async def _handle_ctrl(self, src_id: int, payload: bytes):
        """
        Handle CTRL packet - delegate to file transfer or other control handlers.

        Args:
            src_id: Source node ID
            payload: CTRL payload
        """
        if self._file:
            self._file.handle_file_packet(src_id, payload)

    async def _handle_topo(self, src_id: int, payload: bytes):
        """
        Handle TOPO packet - topology update.

        Args:
            src_id: Source node ID
            payload: TOPO payload
        """
        self._stats['topo_received'] = self._stats.get('topo_received', 0) + 1

        if self._on_topo:
            try:
                self._on_topo(src_id, payload)
            except Exception as e:
                print(f"Error in topo callback: {e}")
                self._stats['callback_errors'] = self._stats.get('callback_errors', 0) + 1

    async def _handle_sync(self, src_id: int, payload: bytes):
        """
        Handle SYNC packet - time synchronization.

        Args:
            src_id: Source node ID
            payload: SYNC payload
        """
        self._stats['sync_received'] = self._stats.get('sync_received', 0) + 1

        if self._on_sync:
            try:
                self._on_sync(src_id, payload)
            except Exception as e:
                print(f"Error in sync callback: {e}")
                self._stats['callback_errors'] = self._stats.get('callback_errors', 0) + 1

    def on_topo(self, callback):
        """
        Register callback for TOPO packets.

        Args:
            callback: Function(src_id: int, payload: bytes)
        """
        self._on_topo = callback

    def on_sync(self, callback):
        """
        Register callback for SYNC packets.

        Args:
            callback: Function(src_id: int, payload: bytes)
        """
        self._on_sync = callback

    # =========================================================================
    # INTERNAL METHODS - Sending
    # =========================================================================

    async def _send_hello(self):
        """
        Send HELLO beacon to announce presence.

        HELLO payload format:
        - bytes 0-1: node_id (uint16, big-endian)
        - byte 2: hop_count (always 0 for our own HELLO)
        """
        payload = bytes([
            (self._node_id >> 8) & 0xFF,
            self._node_id & 0xFF,
            0  # hop_count = 0
        ])

        packet = self._codec.encode(
            pkt_type=PKT_HELLO,
            src_id=self._node_id,
            dest_id=BROADCAST_ID,
            payload=payload,
            pkt_id=self._next_packet_id(),
            ttl=1,  # HELLO not forwarded
            hop_count=0
        )

        success = await self._adapter.send_broadcast(packet)
        if success:
            self._stats['hello_sent'] += 1
            self._stats['tx_packets'] += 1
            self._stats['tx_bytes'] += len(packet)

    async def _send_broadcast(self, data: bytes, pkt_type: int = PKT_DATA) -> bool:
        """
        Send broadcast packet.

        Args:
            data: Payload bytes
            pkt_type: Packet type (default: PKT_DATA)

        Returns:
            True if send succeeded
        """
        packet = self._codec.encode(
            pkt_type=pkt_type,
            src_id=self._node_id,
            dest_id=BROADCAST_ID,
            payload=data,
            pkt_id=self._next_packet_id(),
            ttl=self._config.max_ttl,
            hop_count=0
        )

        success = await self._adapter.send_broadcast(packet)
        if success:
            self._stats['tx_packets'] += 1
            self._stats['tx_bytes'] += len(packet)
        return success

    async def _send_routed(self, dest_id: int, data: bytes, pkt_type: int = PKT_DATA) -> bool:
        """
        Send unicast packet via routing table.

        Performs route discovery if needed.

        Args:
            dest_id: Destination node ID
            data: Payload bytes
            pkt_type: Packet type (default: PKT_DATA)

        Returns:
            True if send succeeded
        """
        # Look up route
        route = self._routes.lookup(dest_id)

        # If no route, discover one
        if route is None:
            success = await self._discovery.discover(dest_id, timeout=3000)
            if not success:
                return False
            route = self._routes.lookup(dest_id)
            if route is None:
                return False
            self._stats['routes_discovered'] += 1

        # Build packet
        packet = self._codec.encode(
            pkt_type=pkt_type,
            src_id=self._node_id,
            dest_id=dest_id,
            payload=data,
            pkt_id=self._next_packet_id(),
            ttl=self._config.max_ttl,
            hop_count=0
        )

        # Send via next hop
        next_hop_mac = route.next_hop_mac
        await self._peer_mgr.ensure_peer(next_hop_mac)

        success = await self._adapter.send(next_hop_mac, packet)
        if success:
            self._stats['tx_packets'] += 1
            self._stats['tx_bytes'] += len(packet)
            # Mark route as used
            self._routes.mark_used(dest_id)

        return success

    async def _send_fragmented(self, dest_id: int, data: bytes) -> bool:
        """
        Send fragmented message.

        Args:
            dest_id: Destination node ID
            data: Payload bytes (> MAX_PAYLOAD)

        Returns:
            True if all fragments sent successfully
        """
        pkt_id = self._next_packet_id()
        flags = FLAG_FRAG

        # Create fragments
        fragments = self._fragmenter.fragment(
            data, self._node_id, dest_id, pkt_id, flags
        )

        self._stats['fragments_sent'] += len(fragments)

        # Send each fragment
        success = True
        for frag in fragments:
            if dest_id == BROADCAST_ID:
                result = await self._adapter.send_broadcast(frag)
            else:
                # Route via next hop
                route = self._routes.lookup(dest_id)
                if route is None:
                    # Discover route
                    disc_success = await self._discovery.discover(dest_id, timeout=3000)
                    if not disc_success:
                        return False
                    route = self._routes.lookup(dest_id)
                    if route is None:
                        return False

                next_hop_mac = route.next_hop_mac
                await self._peer_mgr.ensure_peer(next_hop_mac)
                result = await self._adapter.send(next_hop_mac, frag)

            if result:
                self._stats['tx_packets'] += 1
                self._stats['tx_bytes'] += len(frag)
            else:
                success = False

        return success

    def _next_packet_id(self) -> int:
        """
        Generate next packet ID (incrementing counter).

        Returns:
            16-bit packet ID
        """
        pkt_id = self._packet_id
        self._packet_id = (self._packet_id + 1) & 0xFFFF
        return pkt_id

    async def _send_packet(self, pkt_type: int, dest_id: int, payload: bytes,
                           ttl: int = None, hop_count: int = 0, flags: int = 0,
                           dest_mac: bytes = None) -> bool:
        """Internal method for sending protocol packets (RREQ, RREP, RERR, etc.)"""
        if ttl is None:
            ttl = self._config.max_ttl
        pkt_id = self._next_packet_id()
        packet = self._codec.encode(
            pkt_type=pkt_type, src_id=self._node_id, dest_id=dest_id,
            payload=payload, pkt_id=pkt_id, ttl=ttl, hop_count=hop_count, flags=flags
        )
        if dest_id == BROADCAST_ID or dest_mac is None:
            success = await self._adapter.send_broadcast(packet)
        else:
            await self._peer_mgr.ensure_peer(dest_mac)
            success = await self._adapter.send(dest_mac, packet)

        if success:
            self._stats['tx_packets'] += 1
            self._stats['tx_bytes'] += len(packet)
        return success

    # =========================================================================
    # ASYNC ITERATION SUPPORT
    # =========================================================================

    def __aiter__(self):
        """Support async for iteration over received messages."""
        return self

    async def __anext__(self):
        """
        Yield next received message.

        Returns:
            Tuple of (src_node_id: int, data: bytes)

        Example:
            >>> async for src_id, data in mesh:
            ...     print(f"Message from {src_id:04x}: {data}")
        """
        if not self._running:
            raise StopAsyncIteration

        return await self._message_queue.get()
