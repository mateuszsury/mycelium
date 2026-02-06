"""
µMesh AODV-lite Route Discovery
================================

AODV-lite reactive routing protocol for µMesh.

Implements:
- Route Request (RREQ) broadcasting with expanding ring search
- Route Reply (RREP) unicast back to originator
- Route Error (RERR) broadcasting for broken routes
- Duplicate RREQ detection
- Reverse route creation for RREP forwarding
- Forward route creation from RREP
- Metric-based route selection (lower is better)

Key features:
- Expanding ring search: TTL sequence [1, 3, 7, 15] to minimize broadcast overhead
- Sequence numbers for route freshness comparison
- Metric accumulation based on link quality (LQI)
- Timeout-based route discovery with configurable retries

Usage:
    from umesh import RoutingTable, RouteDiscovery

    routing_table = RoutingTable()
    discovery = RouteDiscovery(mesh, routing_table)

    # Start route discovery
    success = await discovery.discover(dest_id=0x1234, timeout=3000)
    if success:
        route = routing_table.lookup(0x1234)
        print(f"Route found via {route.next_hop_id:04x}, metric={route.metric}")
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

from .const import (
    PKT_RREQ, PKT_RREP, PKT_RERR,
    DEFAULT_TTL, BROADCAST_ID,
    ROUTE_TIMEOUT, ACTIVE_TIMEOUT,
)

from ._fast import accumulate_metric
from .routing import _seq_newer


# =============================================================================
# TASK TRACKING - Callbacks for async task error handling
# =============================================================================

def _task_done_callback(task):
    """Handle completed async tasks, logging any exceptions."""
    if task.cancelled():
        return
    exc = task.exception()
    if exc:
        print(f"AODV task error: {exc}")


def _create_tracked_task(coro):
    """Create an async task with error tracking callback."""
    task = asyncio.create_task(coro)
    task.add_done_callback(_task_done_callback)
    return task


# =============================================================================
# CONSTANTS
# =============================================================================

MAX_SEEN_RREQ = 256  # Maximum entries in seen_rreq cache


# =============================================================================
# PENDING DISCOVERY STATE
# =============================================================================

class PendingDiscovery:
    """State for an ongoing route discovery.

    Tracks:
    - RREQ ID and retries
    - Best metric seen so far
    - Event for signaling completion
    - TTL level in expanding ring search
    """

    __slots__ = ('rreq_id', 'retries', 'best_metric', 'event', 'ttl_idx', 'found')

    def __init__(self, rreq_id: int):
        """Initialize pending discovery.

        Args:
            rreq_id: RREQ identifier for this discovery
        """
        self.rreq_id = rreq_id
        self.retries = 0
        self.best_metric = 65535  # Start with worst possible metric
        self.event = asyncio.Event()
        self.ttl_idx = 0  # Index into EXPANDING_RING
        self.found = False


# =============================================================================
# ROUTE DISCOVERY (AODV-lite)
# =============================================================================

class RouteDiscovery:
    """AODV-lite route discovery with expanding ring search.

    Implements reactive route discovery protocol:
    - Broadcasts RREQ with incrementing TTL (expanding ring)
    - Processes RREQ: creates reverse routes, forwards or replies
    - Processes RREP: creates forward routes, forwards to originator
    - Processes RERR: invalidates broken routes

    Attributes:
        RREQ_TIMEOUT: Timeout per TTL level (ms)
        MAX_RREQ_RETRIES: Maximum retry attempts
        EXPANDING_RING: TTL sequence for expanding ring search
    """

    RREQ_TIMEOUT = 500       # ms - wait for RREP per TTL level
    MAX_RREQ_RETRIES = 4     # Expanding ring: TTL 1, 3, 7, 15
    EXPANDING_RING = [1, 3, 7, 15]  # TTL sequence

    def __init__(self, mesh, routing_table):
        """Initialize route discovery.

        Args:
            mesh: Mesh instance for sending packets and accessing state
            routing_table: RoutingTable instance
        """
        self._mesh = mesh
        self._routes = routing_table
        self._pending = {}  # dest_id -> PendingDiscovery
        self._pending_lock = asyncio.Lock()  # Lock for _pending dict access
        self._rreq_id = 0   # Incrementing RREQ identifier
        self._seen_rreq = {}  # (originator_id, rreq_id) -> timestamp
        self._seen_lock = asyncio.Lock()  # Lock for _seen_rreq dict access

    async def discover(self, dest_id: int, timeout: int = 3000) -> bool:
        """Initiate route discovery to dest_id.

        Uses expanding ring search with TTL sequence [1, 3, 7, 15].

        Args:
            dest_id: Destination node ID to discover route to
            timeout: Total timeout in milliseconds

        Returns:
            True if route found within timeout, False otherwise
        """
        # Prune old RREQ cache entries to prevent memory leak
        await self._prune_seen_rreq_locked()

        # Check if route already exists
        if self._routes.lookup(dest_id) is not None:
            return True

        # Check if discovery already in progress (with lock)
        async with self._pending_lock:
            if dest_id in self._pending:
                pending = self._pending[dest_id]
            else:
                pending = None

        if pending is not None:
            try:
                await asyncio.wait_for(pending.event.wait(), timeout / 1000.0)
                return pending.found
            except asyncio.TimeoutError:
                return False

        # Start new discovery
        rreq_id = self._next_rreq_id()
        pending = PendingDiscovery(rreq_id)
        async with self._pending_lock:
            self._pending[dest_id] = pending

        try:
            # Expanding ring search
            for ttl_idx, ttl in enumerate(self.EXPANDING_RING):
                pending.ttl_idx = ttl_idx

                # Send RREQ with current TTL
                await self.send_rreq(dest_id, ttl, rreq_id)

                # Wait for RREP with timeout
                try:
                    await asyncio.wait_for(pending.event.wait(),
                                          self.RREQ_TIMEOUT / 1000.0)
                    # Route found!
                    pending.found = True
                    return True
                except asyncio.TimeoutError:
                    # No response at this TTL - try next
                    pending.retries += 1
                    if pending.retries >= self.MAX_RREQ_RETRIES:
                        break
                    pending.event.clear()

            # All retries exhausted
            return False

        finally:
            # Clean up pending discovery (with lock)
            async with self._pending_lock:
                if dest_id in self._pending:
                    del self._pending[dest_id]

    async def handle_rreq(self, from_mac: bytes, header: dict, payload: bytes, rssi: int):
        """Handle received RREQ packet.

        Processing steps:
        1. Check for duplicate (seen_rreq)
        2. Create/update reverse route to originator
        3. If we are destination OR have fresh route -> send RREP
        4. Otherwise -> rebroadcast with hop_count+1, metric updated

        Args:
            from_mac: MAC address of sender
            header: Parsed packet header
            payload: RREQ payload bytes
            rssi: RSSI of received packet
        """
        # Decode RREQ payload (13 bytes: 2+2+2+1+2+2+2)
        if len(payload) < 13:
            return  # Invalid RREQ

        try:
            dest_id, src_seq, dest_seq, hop_count, originator_id, metric, rreq_id = \
                struct.unpack('!HHHBHHH', payload[:13])
        except (struct.error, IndexError):
            return  # Malformed payload

        # Check for duplicate RREQ (with lock to prevent race condition)
        if await self._check_and_record_rreq(originator_id, rreq_id):
            return  # Duplicate

        # Get sender's node_id from neighbor table
        sender_id = header['src_id']

        # Calculate link quality to sender
        neighbor = self._mesh._neighbors.get_neighbor(sender_id)
        if neighbor:
            lqi = neighbor['lqi']
        else:
            # Estimate LQI from RSSI only
            from ._fast import calc_lqi
            lqi = calc_lqi(rssi, 0, 0, 60000)

        # Accumulate metric
        new_metric = accumulate_metric(metric, lqi)

        # Create/update reverse route to originator
        self._routes.update(
            dest_id=originator_id,
            next_hop_id=sender_id,
            next_hop_mac=from_mac,
            metric=new_metric,
            seq_num=src_seq,
            hop_count=hop_count + 1,
            lifetime_ms=ACTIVE_TIMEOUT,
        )

        # Are we the destination?
        if dest_id == self._mesh.node_id:
            # Send RREP back to originator
            next_seq = self._routes.next_seq_num()
            _create_tracked_task(self.send_rrep(
                originator_id=originator_id,
                dest_id=dest_id,
                dest_seq=next_seq,
                hop_count=0,
                metric=0
            ))
            return

        # Do we have a fresh route to destination?
        route = self._routes.lookup(dest_id)
        if route is not None:
            # route is a RouteEntry object
            # Check if our route is fresh enough using RouteEntry's is_fresher
            if _seq_newer(route.seq_num, dest_seq) or route.seq_num == dest_seq:
                # Send RREP with our route
                _create_tracked_task(self.send_rrep(
                    originator_id=originator_id,
                    dest_id=dest_id,
                    dest_seq=route.seq_num,
                    hop_count=route.hop_count,
                    metric=route.metric
                ))
                return

        # Forward RREQ if TTL allows
        if header['ttl'] > 1:
            # Rebroadcast RREQ
            _create_tracked_task(self._forward_rreq(
                dest_id=dest_id,
                src_seq=src_seq,
                dest_seq=dest_seq,
                hop_count=hop_count + 1,
                originator_id=originator_id,
                metric=new_metric,
                rreq_id=rreq_id,
                ttl=header['ttl'] - 1
            ))

    async def handle_rrep(self, from_mac: bytes, header: dict, payload: bytes, rssi: int):
        """Handle received RREP packet.

        Processing steps:
        1. Create/update forward route to destination
        2. If we are originator -> route discovery complete
        3. Otherwise -> forward RREP to originator via reverse route

        Args:
            from_mac: MAC address of sender
            header: Parsed packet header
            payload: RREP payload bytes
            rssi: RSSI of received packet
        """
        # Decode RREP payload (11 bytes: 2+2+2+1+2+2)
        if len(payload) < 11:
            return  # Invalid RREP

        try:
            dest_id, dest_seq, originator_id, hop_count, lifetime, metric = \
                struct.unpack('!HHHBHH', payload[:11])
        except (struct.error, IndexError):
            return  # Malformed payload

        # Get sender's node_id
        sender_id = header['src_id']

        # Calculate link quality to sender
        neighbor = self._mesh._neighbors.get_neighbor(sender_id)
        if neighbor:
            lqi = neighbor['lqi']
        else:
            from ._fast import calc_lqi
            lqi = calc_lqi(rssi, 0, 0, 60000)

        # Accumulate metric
        new_metric = accumulate_metric(metric, lqi)

        # Create/update forward route to destination
        self._routes.update(
            dest_id=dest_id,
            next_hop_id=sender_id,
            next_hop_mac=from_mac,
            metric=new_metric,
            seq_num=dest_seq,
            hop_count=hop_count + 1,
            lifetime_ms=lifetime * 100,  # Convert from centiseconds
        )

        # Are we the originator?
        if originator_id == self._mesh.node_id:
            # Route discovery complete! (access _pending with lock)
            async with self._pending_lock:
                pending = self._pending.get(dest_id)
                if pending:
                    pending.best_metric = min(pending.best_metric, new_metric)
                    pending.found = True
                    pending.event.set()
            return

        # Forward RREP to originator via reverse route
        reverse_route = self._routes.lookup(originator_id)
        if reverse_route is not None:
            # reverse_route is a RouteEntry object
            _create_tracked_task(self._forward_rrep(
                dest_id=dest_id,
                dest_seq=dest_seq,
                originator_id=originator_id,
                hop_count=hop_count + 1,
                lifetime=lifetime,
                metric=new_metric,
                next_hop_mac=reverse_route.next_hop_mac,
                next_hop_id=reverse_route.next_hop_id
            ))

    async def handle_rerr(self, from_mac: bytes, header: dict, payload: bytes):
        """Handle received RERR packet.

        Invalidates routes to unreachable destinations.
        Propagates RERR to affected nodes.

        Args:
            from_mac: MAC address of sender
            header: Parsed packet header
            payload: RERR payload bytes
        """
        # Decode RERR payload
        if len(payload) < 1:
            return

        unreachable_count = payload[0]

        if len(payload) < 1 + unreachable_count * 4:
            return  # Invalid RERR

        # Parse unreachable nodes
        unreachable = []
        offset = 1
        for _ in range(unreachable_count):
            node_id, seq_num = struct.unpack('!HH', payload[offset:offset+4])
            unreachable.append((node_id, seq_num))
            offset += 4

        # Check which routes we need to invalidate
        affected = []
        sender_id = header['src_id']
        for dest_id, dest_seq in unreachable:
            route = self._routes.lookup(dest_id)
            if route is not None:
                # route is a RouteEntry object
                # Check if route goes through the sender
                if route.next_hop_id == sender_id:
                    # Only invalidate if RERR seq_num is fresher or equal
                    # This prevents stale RERRs from invalidating fresh routes
                    if _seq_newer(dest_seq, route.seq_num) or dest_seq == route.seq_num:
                        # Invalidate this route
                        self._routes.invalidate(dest_id)
                        affected.append((dest_id, dest_seq))

        # Propagate RERR if we have affected routes
        if affected:
            _create_tracked_task(self.send_rerr(affected))

    async def send_rreq(self, dest_id: int, ttl: int = 7, rreq_id: int = None):
        """Send RREQ broadcast for destination.

        Args:
            dest_id: Destination node ID to discover
            ttl: Time-to-live for RREQ broadcast
            rreq_id: RREQ identifier (generated if None)
        """
        if rreq_id is None:
            rreq_id = self._next_rreq_id()

        # Increment our sequence number
        src_seq = self._routes.next_seq_num()

        # Get destination's last known sequence (0 if unknown)
        route = self._routes.lookup(dest_id)
        dest_seq = route.seq_num if route else 0

        # Build RREQ payload
        payload = struct.pack('!HHHBHHH',
            dest_id,                    # dest_node_id
            src_seq,                    # src_seq_num
            dest_seq,                   # dest_seq_num
            0,                          # hop_count
            self._mesh.node_id,         # originator_id
            0,                          # metric
            rreq_id                     # rreq_id
        )

        # Send via mesh broadcast
        await self._mesh._send_packet(
            pkt_type=PKT_RREQ,
            dest_id=BROADCAST_ID,
            dest_mac=None,  # Broadcast
            payload=payload,
            ttl=ttl,
            hop_count=0
        )

    async def send_rrep(self, originator_id: int, dest_id: int,
                        dest_seq: int, hop_count: int, metric: int):
        """Send RREP unicast back to originator.

        Args:
            originator_id: Node that initiated route discovery
            dest_id: Destination that was found
            dest_seq: Destination's sequence number
            hop_count: Hops from destination
            metric: Route metric
        """
        # Lookup reverse route to originator
        reverse_route = self._routes.lookup(originator_id)
        if reverse_route is None:
            return  # No route back to originator

        # reverse_route is a RouteEntry object
        next_hop_id = reverse_route.next_hop_id
        next_hop_mac = reverse_route.next_hop_mac

        # Calculate lifetime (in centiseconds for payload)
        lifetime_cs = ACTIVE_TIMEOUT // 100

        # Build RREP payload
        payload = struct.pack('!HHHBHH',
            dest_id,                    # dest_node_id
            dest_seq,                   # dest_seq_num
            originator_id,              # originator_id
            hop_count,                  # hop_count
            lifetime_cs,                # lifetime (centiseconds)
            metric                      # metric
        )

        # Send via mesh unicast
        await self._mesh._send_packet(
            pkt_type=PKT_RREP,
            dest_id=originator_id,
            dest_mac=next_hop_mac,
            payload=payload,
            ttl=DEFAULT_TTL,
            hop_count=0
        )

    async def send_rerr(self, unreachable: list):
        """Send RERR broadcast for unreachable destinations.

        Args:
            unreachable: List of (dest_id, dest_seq) tuples
        """
        if not unreachable:
            return

        # Limit to 255 unreachable nodes (1 byte count)
        unreachable = unreachable[:255]

        # Build RERR payload
        payload = struct.pack('!B', len(unreachable))
        for dest_id, dest_seq in unreachable:
            payload += struct.pack('!HH', dest_id, dest_seq)

        # Send via mesh broadcast
        await self._mesh._send_packet(
            pkt_type=PKT_RERR,
            dest_id=BROADCAST_ID,
            dest_mac=None,
            payload=payload,
            ttl=DEFAULT_TTL,
            hop_count=0
        )

    def _next_rreq_id(self) -> int:
        """Generate next RREQ identifier.

        Returns:
            RREQ ID (16-bit, wraps at 65535)
        """
        self._rreq_id = (self._rreq_id + 1) & 0xFFFF
        return self._rreq_id

    async def _check_and_record_rreq(self, originator_id: int, rreq_id: int) -> bool:
        """Atomically check and record RREQ with lock.

        Args:
            originator_id: Originator node ID
            rreq_id: RREQ identifier

        Returns:
            True if duplicate (already seen), False if new (now recorded)
        """
        key = (originator_id, rreq_id)
        async with self._seen_lock:
            # Check if duplicate
            if key in self._seen_rreq:
                return True  # Duplicate

            # Enforce memory limit on seen_rreq cache
            if len(self._seen_rreq) >= MAX_SEEN_RREQ:
                self._prune_seen_rreq_unlocked()
                # If still at limit after prune, remove oldest entries using FIFO
                # O(n) - dict preserves insertion order since Python 3.7/MicroPython
                if len(self._seen_rreq) >= MAX_SEEN_RREQ:
                    # Remove oldest 25% entries (first inserted = oldest)
                    keys_to_remove = list(self._seen_rreq.keys())[:len(self._seen_rreq) // 4 or 1]
                    for k in keys_to_remove:
                        del self._seen_rreq[k]

            # Record new RREQ
            self._seen_rreq[key] = ticks_ms()
            return False  # New

    def _is_duplicate_rreq(self, originator_id: int, rreq_id: int) -> bool:
        """Check if RREQ was recently seen (no lock - for internal use).

        Args:
            originator_id: Originator node ID
            rreq_id: RREQ identifier

        Returns:
            True if duplicate, False if new
        """
        key = (originator_id, rreq_id)
        return key in self._seen_rreq

    def _record_rreq(self, originator_id: int, rreq_id: int):
        """Record seen RREQ for duplicate detection (no lock - for internal use).

        Args:
            originator_id: Originator node ID
            rreq_id: RREQ identifier
        """
        # Enforce memory limit on seen_rreq cache
        if len(self._seen_rreq) >= MAX_SEEN_RREQ:
            self._prune_seen_rreq_unlocked()
            # If still at limit after prune, remove oldest entries using FIFO
            # O(n) - dict preserves insertion order since Python 3.7/MicroPython
            if len(self._seen_rreq) >= MAX_SEEN_RREQ:
                # Remove oldest 25% entries (first inserted = oldest)
                keys_to_remove = list(self._seen_rreq.keys())[:len(self._seen_rreq) // 4 or 1]
                for key in keys_to_remove:
                    del self._seen_rreq[key]

        key = (originator_id, rreq_id)
        self._seen_rreq[key] = ticks_ms()

    def _prune_seen_rreq_unlocked(self, max_age_ms: int = 10000):
        """Remove old entries from seen_rreq (no lock - internal use).

        Args:
            max_age_ms: Maximum age in milliseconds (default 10s)
        """
        now_ms = ticks_ms()
        to_remove = []
        for key, timestamp in self._seen_rreq.items():
            if ticks_diff(now_ms, timestamp) > max_age_ms:
                to_remove.append(key)
        for key in to_remove:
            del self._seen_rreq[key]

    async def _prune_seen_rreq_locked(self, max_age_ms: int = 10000):
        """Remove old entries from seen_rreq with lock.

        Args:
            max_age_ms: Maximum age in milliseconds (default 10s)
        """
        async with self._seen_lock:
            self._prune_seen_rreq_unlocked(max_age_ms)

    def prune_seen_rreq(self, max_age_ms: int = 10000):
        """Remove old entries from seen_rreq (legacy sync API).

        Args:
            max_age_ms: Maximum age in milliseconds (default 10s)
        """
        self._prune_seen_rreq_unlocked(max_age_ms)

    async def _forward_rreq(self, dest_id: int, src_seq: int, dest_seq: int,
                           hop_count: int, originator_id: int, metric: int,
                           rreq_id: int, ttl: int):
        """Forward RREQ with updated hop_count and metric.

        Args:
            dest_id: Destination being sought
            src_seq: Originator's sequence number
            dest_seq: Last known destination sequence
            hop_count: Updated hop count
            originator_id: Original RREQ initiator
            metric: Updated metric
            rreq_id: RREQ identifier
            ttl: Remaining TTL
        """
        # Build RREQ payload
        payload = struct.pack('!HHHBHHH',
            dest_id,
            src_seq,
            dest_seq,
            hop_count,
            originator_id,
            metric,
            rreq_id
        )

        # Broadcast
        await self._mesh._send_packet(
            pkt_type=PKT_RREQ,
            dest_id=BROADCAST_ID,
            dest_mac=None,
            payload=payload,
            ttl=ttl,
            hop_count=hop_count
        )

    async def _forward_rrep(self, dest_id: int, dest_seq: int, originator_id: int,
                           hop_count: int, lifetime: int, metric: int,
                           next_hop_mac: bytes, next_hop_id: int):
        """Forward RREP to originator.

        Args:
            dest_id: Destination that was found
            dest_seq: Destination's sequence number
            originator_id: Original RREQ initiator
            hop_count: Updated hop count
            lifetime: Route lifetime (centiseconds)
            metric: Updated metric
            next_hop_mac: MAC address of next hop toward originator
            next_hop_id: Node ID of next hop
        """
        # Build RREP payload
        payload = struct.pack('!HHHBHH',
            dest_id,
            dest_seq,
            originator_id,
            hop_count,
            lifetime,
            metric
        )

        # Unicast to next hop
        await self._mesh._send_packet(
            pkt_type=PKT_RREP,
            dest_id=originator_id,
            dest_mac=next_hop_mac,
            payload=payload,
            ttl=DEFAULT_TTL,
            hop_count=hop_count
        )


# =============================================================================
# MODULE EXPORTS
# =============================================================================

__all__ = [
    'RouteDiscovery',
    'PendingDiscovery',
]
