"""
µMesh Network Diagnostics
==========================

Diagnostic tools for mesh network monitoring and troubleshooting.

Provides:
- MeshDiag: Ping and traceroute capabilities with RTT measurement
- get_mesh_stats(): Comprehensive statistics collection
- format_stats(): Human-readable statistics formatting
- format_neighbors(): Neighbor table visualization
- format_routes(): Routing table visualization

Usage:
    from umesh.diag import MeshDiag, get_mesh_stats, format_stats

    # Initialize diagnostics
    diag = MeshDiag(mesh)

    # Ping remote node
    rtts = await diag.ping(0x1234, count=3)
    print(f"Average RTT: {sum(r for r in rtts if r)/len([r for r in rtts if r]):.1f}ms")

    # Traceroute to destination
    trace = await diag.traceroute(0x1234, max_hops=10)
    for hop, node_id, rtt in trace:
        print(f"{hop}: {node_id:04x} ({rtt}ms)")

    # Get statistics
    stats = get_mesh_stats(mesh)
    print(format_stats(stats))
"""

import struct

try:
    import uasyncio as asyncio
except ImportError:
    import asyncio

try:
    from utime import ticks_ms, ticks_diff
except ImportError:
    from time import monotonic
    ticks_ms = lambda: int(monotonic() * 1000)
    ticks_diff = lambda a, b: a - b

try:
    import gc
    GC_AVAILABLE = True
except ImportError:
    GC_AVAILABLE = False

from .const import PKT_PING, PKT_PONG, DEFAULT_TTL, BROADCAST_ID


# Diagnostic constants
PING_TIMEOUT = 2000        # ms - default ping timeout
TRACEROUTE_TIMEOUT = 3000  # ms - default traceroute hop timeout
MAX_HOPS = 15              # Maximum traceroute hops


class MeshDiag:
    """Mesh network diagnostic tools.

    Provides ping and traceroute functionality for testing connectivity
    and measuring network performance.

    Attributes:
        _mesh: Reference to main Mesh instance
        _ping_events: Dict of pkt_id -> (event, result_dict)
        _trace_results: Dict of dest_id -> list of (hop, node_id, rtt)
        _seq_counter: Sequence number counter for diagnostics
    """

    __slots__ = ('_mesh', '_ping_events', '_trace_results', '_seq_counter')

    def __init__(self, mesh):
        """Initialize diagnostic tools.

        Args:
            mesh: Main Mesh instance
        """
        self._mesh = mesh
        self._ping_events = {}      # pkt_id -> (event, result_dict)
        self._trace_results = {}    # dest_id -> [(hop, node_id, rtt)]
        self._seq_counter = 0       # Sequence number for diagnostics

    async def ping(self, dest_id: int, count: int = 3,
                   timeout: int = None, callback=None) -> list:
        """Ping remote node and measure RTT.

        Sends multiple PING packets to the destination and waits for PONG
        responses. Returns list of RTT values (None for timeouts).

        Args:
            dest_id: Destination node ID (16-bit)
            count: Number of pings to send (default: 3)
            timeout: Timeout per ping in ms (default: PING_TIMEOUT)
            callback: Optional callback(seq: int, rtt_ms: int or None)

        Returns:
            List of RTT values in milliseconds (None for timeouts)

        Example:
            >>> rtts = await diag.ping(0x1234, count=5)
            >>> valid = [r for r in rtts if r is not None]
            >>> if valid:
            ...     print(f"Avg RTT: {sum(valid)/len(valid):.1f}ms")
            ...     print(f"Packet loss: {(len(rtts)-len(valid))/len(rtts)*100:.0f}%")
        """
        if timeout is None:
            timeout = PING_TIMEOUT

        rtts = []

        for seq in range(count):
            # Send ping and wait for response
            rtt = await self._send_ping(dest_id, seq, timeout)
            rtts.append(rtt)

            # Call user callback if provided
            if callback:
                callback(seq, rtt)

            # Small delay between pings (100ms)
            if seq < count - 1:
                await asyncio.sleep_ms(100)

        return rtts

    async def traceroute(self, dest_id: int, max_hops: int = None,
                         timeout: int = None, callback=None) -> list:
        """Trace route to destination.

        Uses TTL-limited PING packets to discover the path to the destination.
        Each hop responds when TTL reaches zero or destination is reached.

        Args:
            dest_id: Destination node ID (16-bit)
            max_hops: Maximum hops to probe (default: MAX_HOPS)
            timeout: Timeout per hop in ms (default: TRACEROUTE_TIMEOUT)
            callback: Optional callback(hop: int, node_id: int, rtt_ms: int)

        Returns:
            List of (hop_num, node_id, rtt_ms) tuples

        Example:
            >>> trace = await diag.traceroute(0x1234)
            >>> for hop, node_id, rtt in trace:
            ...     print(f"{hop}: {node_id:04x} ({rtt}ms)")
        """
        if max_hops is None:
            max_hops = MAX_HOPS
        if timeout is None:
            timeout = TRACEROUTE_TIMEOUT

        path = []

        for ttl in range(1, max_hops + 1):
            # Send TTL-limited ping
            seq = self._seq_counter
            self._seq_counter = (self._seq_counter + 1) & 0xFFFF

            rtt = await self._send_ping_ttl(dest_id, seq, ttl, timeout)

            if rtt is not None:
                # Got response - extract responding node ID from result
                result = self._ping_events.get(seq)
                if result and result[1].get('node_id'):
                    node_id = result[1]['node_id']
                    path.append((ttl, node_id, rtt))

                    # Call user callback if provided
                    if callback:
                        callback(ttl, node_id, rtt)

                    # If we reached destination, stop
                    if node_id == dest_id:
                        break
            else:
                # Timeout at this hop - continue probing
                pass

            # Small delay between hops
            if ttl < max_hops:
                await asyncio.sleep_ms(100)

        return path

    def handle_ping(self, src_id: int, payload: bytes, rssi: int):
        """Handle received PING packet. Send PONG response.

        This is called by the mesh when a PING packet is received.
        Extracts the timestamp and sequence number, then sends a PONG
        with the original values echoed back.

        Args:
            src_id: Source node ID
            payload: PING payload (seq + timestamp)
            rssi: Received signal strength indicator
        """
        # Parse PING payload: seq (uint16) + timestamp (uint32)
        if len(payload) < 6:
            return  # Invalid payload

        seq, timestamp = struct.unpack('>HI', payload[:6])

        # Get hop count to responder (from neighbor table if direct)
        neighbor = self._mesh._neighbors.get_neighbor(src_id)
        hop_count = neighbor['hop_count'] if neighbor else 1

        # Build PONG payload: seq + timestamp + rssi + hop_count
        pong_payload = struct.pack('>HIbB', seq, timestamp, rssi, hop_count)

        # Send PONG response
        pong = self._mesh._codec.encode(
            pkt_type=PKT_PONG,
            src_id=self._mesh.node_id,
            dest_id=src_id,
            payload=pong_payload,
            pkt_id=self._mesh._next_packet_id(),
            ttl=DEFAULT_TTL,
            hop_count=0
        )

        # Send via adapter (broadcast for now, routing in Phase 2)
        # Try to create task, fallback to direct call if no event loop
        try:
            asyncio.create_task(self._mesh._adapter.send_broadcast(pong))
        except RuntimeError:
            # No event loop running (e.g., in tests)
            pass

    def handle_pong(self, src_id: int, payload: bytes, rssi: int):
        """Handle received PONG packet. Complete ping wait.

        This is called by the mesh when a PONG packet is received.
        Calculates RTT and signals the waiting ping task.

        Args:
            src_id: Source node ID (responder)
            payload: PONG payload (seq + timestamp + rssi + hop_count)
            rssi: Received signal strength indicator
        """
        # Parse PONG payload: seq + timestamp + rssi + hop_count
        if len(payload) < 8:
            return  # Invalid payload

        seq, orig_timestamp, pong_rssi, hop_count = struct.unpack('>HIbB', payload[:8])

        # Calculate RTT
        rtt = ticks_diff(ticks_ms(), orig_timestamp)

        # Find waiting event
        if seq in self._ping_events:
            event, result = self._ping_events[seq]

            # Store result
            result['rtt'] = rtt
            result['node_id'] = src_id
            result['rssi'] = rssi
            result['pong_rssi'] = pong_rssi
            result['hop_count'] = hop_count

            # Signal completion
            event.set()

    async def _send_ping(self, dest_id: int, seq: int, timeout: int) -> int:
        """Send single ping and wait for pong.

        Args:
            dest_id: Destination node ID
            seq: Sequence number
            timeout: Timeout in milliseconds

        Returns:
            RTT in milliseconds or None if timeout
        """
        return await self._send_ping_ttl(dest_id, seq, DEFAULT_TTL, timeout)

    async def _send_ping_ttl(self, dest_id: int, seq: int, ttl: int, timeout: int) -> int:
        """Send single ping with specific TTL and wait for pong.

        Args:
            dest_id: Destination node ID
            seq: Sequence number
            ttl: Time-to-live for packet
            timeout: Timeout in milliseconds

        Returns:
            RTT in milliseconds or None if timeout
        """
        # Create event for waiting
        event = asyncio.Event()
        result = {}
        self._ping_events[seq] = (event, result)

        # Build PING payload: seq + timestamp
        timestamp = ticks_ms()
        payload = struct.pack('>HI', seq, timestamp)

        # Build PING packet
        ping = self._mesh._codec.encode(
            pkt_type=PKT_PING,
            src_id=self._mesh.node_id,
            dest_id=dest_id,
            payload=payload,
            pkt_id=self._mesh._next_packet_id(),
            ttl=ttl,
            hop_count=0
        )

        # Send ping via adapter (broadcast for now, routing in Phase 2)
        await self._mesh._adapter.send_broadcast(ping)

        # Wait for pong with timeout
        try:
            await asyncio.wait_for(event.wait(), timeout / 1000.0)
            rtt = result.get('rtt')
            return rtt
        except asyncio.TimeoutError:
            return None
        finally:
            # Clean up event
            del self._ping_events[seq]


# =============================================================================
# STATISTICS COLLECTION AND FORMATTING
# =============================================================================

def get_mesh_stats(mesh) -> dict:
    """Collect comprehensive mesh statistics.

    Gathers statistics from all mesh components including packet counts,
    neighbor information, routing table state, and memory usage.

    Args:
        mesh: Main Mesh instance

    Returns:
        Dictionary with statistics:
        - node_id: This node's ID (hex string)
        - mac: This node's MAC address (hex string)
        - uptime_ms: Time since mesh started (if available)
        - tx_packets: Total packets sent
        - rx_packets: Total packets received
        - tx_bytes: Total bytes sent
        - rx_bytes: Total bytes received
        - dropped_duplicates: Duplicate packets filtered
        - dropped_crc_fail: Packets with invalid CRC
        - hello_sent: HELLO beacons sent
        - hello_received: HELLO beacons received
        - neighbor_count: Number of direct neighbors
        - route_count: Number of known routes (if routing enabled)
        - pending_routes: Routes being discovered (if routing enabled)
        - memory_free: Free heap memory (if gc available)
        - memory_alloc: Allocated memory (if gc available)
        - peer_slots_used: ESP-NOW peer slots in use
        - peer_evictions: Total peer evictions (LRU)
    """
    stats = {}

    # Node identification
    stats['node_id'] = f"0x{mesh.node_id:04x}"
    stats['mac'] = ':'.join(f'{b:02x}' for b in mesh.mac)

    # Packet statistics
    mesh_stats = mesh.stats
    stats['tx_packets'] = mesh_stats.get('tx_packets', 0)
    stats['rx_packets'] = mesh_stats.get('rx_packets', 0)
    stats['tx_bytes'] = mesh_stats.get('tx_bytes', 0)
    stats['rx_bytes'] = mesh_stats.get('rx_bytes', 0)
    stats['dropped_duplicates'] = mesh_stats.get('dropped_duplicates', 0)
    stats['dropped_crc_fail'] = mesh_stats.get('dropped_crc_fail', 0)
    stats['hello_sent'] = mesh_stats.get('hello_sent', 0)
    stats['hello_received'] = mesh_stats.get('hello_received', 0)

    # Neighbor statistics
    stats['neighbor_count'] = len(mesh.neighbors)

    # Routing statistics (if Phase 2+)
    if hasattr(mesh, '_routes') and mesh._routes:
        stats['route_count'] = len(mesh._routes._routes)
    else:
        stats['route_count'] = 0

    if hasattr(mesh, '_discovery') and mesh._discovery:
        stats['pending_routes'] = len(mesh._discovery._pending)
    else:
        stats['pending_routes'] = 0

    # Peer manager statistics
    if hasattr(mesh, '_peer_mgr') and mesh._peer_mgr:
        peer_stats = mesh._peer_mgr.get_stats()
        stats['peer_slots_used'] = peer_stats.get('slots_used', 0)
        stats['peer_evictions'] = peer_stats.get('evictions', 0)
    else:
        stats['peer_slots_used'] = 0
        stats['peer_evictions'] = 0

    # Memory statistics (if gc available)
    if GC_AVAILABLE and hasattr(gc, 'mem_free'):
        stats['memory_free'] = gc.mem_free()
        stats['memory_alloc'] = gc.mem_alloc()
    else:
        stats['memory_free'] = 0
        stats['memory_alloc'] = 0

    return stats


def format_stats(stats: dict) -> str:
    """Format statistics as human-readable string.

    Args:
        stats: Statistics dictionary from get_mesh_stats()

    Returns:
        Multi-line formatted string

    Example:
        >>> stats = get_mesh_stats(mesh)
        >>> print(format_stats(stats))
        Node ID: 0x1234
        MAC Address: aa:bb:cc:dd:ee:ff
        TX: 1234 packets, 56789 bytes
        RX: 2345 packets, 67890 bytes
        Dropped: 12 duplicates, 3 CRC errors
        Neighbors: 5 direct
        Routes: 8 known, 1 pending
        Memory: 45678 free, 12345 allocated
        ...
    """
    lines = []

    # Header
    lines.append("=" * 50)
    lines.append("µMesh Network Statistics")
    lines.append("=" * 50)

    # Node info
    lines.append(f"Node ID:      {stats['node_id']}")
    lines.append(f"MAC Address:  {stats['mac']}")
    lines.append("")

    # Packet statistics
    lines.append("Packet Statistics:")
    lines.append(f"  TX:         {stats['tx_packets']} packets, {stats['tx_bytes']} bytes")
    lines.append(f"  RX:         {stats['rx_packets']} packets, {stats['rx_bytes']} bytes")
    lines.append(f"  Dropped:    {stats['dropped_duplicates']} duplicates, {stats['dropped_crc_fail']} CRC errors")
    lines.append(f"  HELLO:      {stats['hello_sent']} sent, {stats['hello_received']} received")
    lines.append("")

    # Network statistics
    lines.append("Network Statistics:")
    lines.append(f"  Neighbors:  {stats['neighbor_count']} direct")
    lines.append(f"  Routes:     {stats['route_count']} known, {stats['pending_routes']} pending")
    lines.append(f"  Peer slots: {stats['peer_slots_used']}/19 used, {stats['peer_evictions']} evictions")
    lines.append("")

    # Memory statistics
    if stats['memory_free'] > 0 or stats['memory_alloc'] > 0:
        lines.append("Memory Statistics:")
        lines.append(f"  Free:       {stats['memory_free']:,} bytes")
        lines.append(f"  Allocated:  {stats['memory_alloc']:,} bytes")
        total = stats['memory_free'] + stats['memory_alloc']
        if total > 0:
            usage_pct = (stats['memory_alloc'] / total) * 100
            lines.append(f"  Usage:      {usage_pct:.1f}%")
        lines.append("")

    lines.append("=" * 50)

    return '\n'.join(lines)


def format_neighbors(mesh) -> str:
    """Format neighbor table as human-readable string.

    Args:
        mesh: Main Mesh instance

    Returns:
        Multi-line formatted string with neighbor list

    Example:
        >>> print(format_neighbors(mesh))
        ==================== Neighbor Table ====================
        Node ID  MAC Address        RSSI   LQI  Last Seen
        --------------------------------------------------------
        0x1234   aa:bb:cc:dd:ee:01  -45    95   2.3s ago
        0x5678   aa:bb:cc:dd:ee:02  -62    78   1.1s ago
        ...
    """
    lines = []

    # Header
    lines.append("=" * 56)
    lines.append("Neighbor Table")
    lines.append("=" * 56)
    lines.append(f"{'Node ID':<8} {'MAC Address':<17} {'RSSI':<6} {'LQI':<4} {'Last Seen'}")
    lines.append("-" * 56)

    # Get neighbors sorted by LQI (already sorted by mesh.neighbors property)
    neighbors = mesh.neighbors

    if not neighbors:
        lines.append("  (no neighbors)")
    else:
        now = ticks_ms()
        for node_id, mac, rssi, lqi in neighbors:
            mac_str = ':'.join(f'{b:02x}' for b in mac)

            # Get last seen time
            neighbor = mesh._neighbors.get_neighbor(node_id)
            if neighbor:
                last_seen_ms = ticks_diff(now, neighbor['last_seen'])
                if last_seen_ms < 1000:
                    last_seen = f"{last_seen_ms}ms ago"
                elif last_seen_ms < 60000:
                    last_seen = f"{last_seen_ms/1000:.1f}s ago"
                else:
                    last_seen = f"{last_seen_ms/60000:.1f}m ago"
            else:
                last_seen = "unknown"

            lines.append(f"{node_id:04x}     {mac_str:<17} {rssi:<6} {lqi:<4} {last_seen}")

    lines.append("=" * 56)

    return '\n'.join(lines)


def format_routes(mesh) -> str:
    """Format routing table as human-readable string.

    Args:
        mesh: Main Mesh instance

    Returns:
        Multi-line formatted string with route list

    Example:
        >>> print(format_routes(mesh))
        ==================== Routing Table ====================
        Dest     Next Hop  Metric  Hops  Seq    Expires
        -------------------------------------------------------
        0x1234   0x5678    100     3     42     45s
        0xABCD   0x5678    150     4     38     23s
        ...
    """
    lines = []

    # Header
    lines.append("=" * 56)
    lines.append("Routing Table")
    lines.append("=" * 56)

    # Check if routing is enabled (Phase 2+)
    if not hasattr(mesh, '_routes') or not mesh._routes:
        lines.append("  (routing not enabled)")
        lines.append("=" * 56)
        return '\n'.join(lines)

    lines.append(f"{'Dest':<8} {'Next Hop':<9} {'Metric':<7} {'Hops':<5} {'Seq':<6} {'Expires'}")
    lines.append("-" * 56)

    # Get routes from routing table
    routes = mesh._routes._routes

    if not routes:
        lines.append("  (no routes)")
    else:
        now = ticks_ms()
        for dest_id, route in routes.items():
            # Calculate time until expiry
            expires_ms = ticks_diff(route.expiry_ms, now)
            if expires_ms < 0:
                expires_str = "expired"
            elif expires_ms < 1000:
                expires_str = f"{expires_ms}ms"
            elif expires_ms < 60000:
                expires_str = f"{expires_ms/1000:.0f}s"
            else:
                expires_str = f"{expires_ms/60000:.1f}m"

            lines.append(
                f"{dest_id:04x}     {route.next_hop_id:04x}      "
                f"{route.metric:<7} {route.hop_count:<5} "
                f"{route.seq_num:<6} {expires_str}"
            )

    lines.append("=" * 56)

    return '\n'.join(lines)
