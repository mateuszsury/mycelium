"""
µMesh Forwarding Engine
========================

Multi-hop packet forwarding with route lookup and packet queuing.

The forwarding engine is responsible for:
- Processing received packets that need to be forwarded
- Decrementing TTL and incrementing hop_count
- Routing unicast packets to next hop
- Rebroadcasting broadcast packets with jitter
- Queuing packets pending route discovery
- Delivering packets to local application

Key features:
- TTL and hop_count tracking
- Broadcast storm prevention via random jitter
- Pending packet queue with timeout
- Integration with RoutingTable and RouteDiscovery
- Zero-copy packet modification where possible

Usage:
    from umesh.forward import ForwardingEngine

    engine = ForwardingEngine(mesh, routing_table, route_discovery)

    # Process incoming packet
    success = await engine.forward(raw_packet, from_mac, rssi)

    # Flush pending packets after route found
    await engine.flush_pending(dest_id)

    # Discard pending for unreachable dest
    engine.discard_pending(dest_id)
"""

try:
    import uasyncio as asyncio
except ImportError:
    import asyncio
    # Add sleep_ms compatibility for standard asyncio
    if not hasattr(asyncio, 'sleep_ms'):
        async def _sleep_ms(ms):
            await asyncio.sleep(ms / 1000.0)
        asyncio.sleep_ms = _sleep_ms

try:
    import urandom as random
except ImportError:
    import random

try:
    from utime import ticks_ms, ticks_diff
except ImportError:
    # Fallback for desktop Python
    from time import monotonic
    ticks_ms = lambda: int(monotonic() * 1000)
    ticks_diff = lambda a, b: a - b

from .const import (
    PKT_DATA, PKT_DATA_FRAG, PKT_HELLO,
    PKT_RREQ, PKT_RREP, PKT_RERR,
    PKT_PING, PKT_PONG,
    PKT_ACK, PKT_NACK,
    BROADCAST_ID, BROADCAST_MAC,
    HEADER_SIZE, MAX_PAYLOAD,
    PENDING_QUEUE_SIZE, PENDING_TIMEOUT, REBROADCAST_JITTER,
)
from .packet import PacketCodec


class ForwardingEngine:
    """Packet forwarding engine for multi-hop routing.

    Handles packet forwarding logic including:
    - TTL validation and decrement
    - Hop count increment
    - Broadcast rebroadcast with jitter
    - Unicast routing via route table lookup
    - Pending queue for packets awaiting route discovery

    Attributes:
        _mesh: Reference to main Mesh instance
        _routes: RoutingTable instance (None if Phase 1 only)
        _discovery: RouteDiscovery instance (None if Phase 1 only)
        _pending: List of (dest_id, raw_packet, timestamp) tuples
        _codec: PacketCodec for encode/decode operations
    """

    def __init__(self, mesh, routing_table=None, route_discovery=None):
        """Initialize forwarding engine.

        Args:
            mesh: Main Mesh instance
            routing_table: RoutingTable instance (optional, Phase 2+)
            route_discovery: RouteDiscovery instance (optional, Phase 2+)
        """
        self._mesh = mesh
        self._routes = routing_table
        self._discovery = route_discovery
        self._pending = []  # List of (dest_id, packet, timestamp)
        self._codec = PacketCodec()
        self._forward_count = 0  # Counter for periodic pending queue pruning

    async def forward(self, raw: bytes, from_mac: bytes, rssi: int) -> bool:
        """Process and forward received packet.

        Forwarding logic:
        1. Decode header, validate CRC (already done by mesh._process_packet)
        2. Check duplicate (already done by mesh._process_packet)
        3. Decrement TTL, drop if 0
        4. If dest == my_node_id: deliver locally
        5. If dest == 0xFFFF (broadcast):
           - Deliver locally
           - Add jitter delay (0-REBROADCAST_JITTER ms)
           - Rebroadcast with hop_count+1
        6. Unicast destination:
           - Lookup route
           - If route found: forward to next_hop
           - If no route: queue packet, start route discovery

        Args:
            raw: Raw packet bytes
            from_mac: Sender MAC address (6 bytes)
            rssi: Signal strength in dBm

        Returns:
            True if packet was processed successfully, False otherwise
        """
        # Periodic pending queue pruning (every 16 forwards)
        self._forward_count += 1
        if self._forward_count >= 16:
            self._forward_count = 0
            self.prune_pending()

        # Decode packet header
        parsed = self._codec.decode(raw)
        if parsed is None:
            # Invalid packet (CRC fail, etc.)
            return False

        # Extract header fields
        ttl = parsed['ttl']
        hop_count = parsed['hop_count']
        dest_id = parsed['dest_id']
        src_id = parsed['src_id']
        pkt_type = parsed['pkt_type']

        # Decrement TTL, drop if exhausted
        if ttl == 0:
            return False

        new_ttl = ttl - 1
        new_hop_count = hop_count + 1

        # Check if packet is for this node
        if dest_id == self._mesh.node_id:
            # Deliver locally
            await self._deliver_local(parsed, from_mac)
            return True

        # Check if broadcast
        if dest_id == BROADCAST_ID:
            # Deliver locally first
            await self._deliver_local(parsed, from_mac)

            # Rebroadcast with jitter to prevent collision
            if new_ttl > 0:
                await self._rebroadcast(raw, parsed, new_ttl, new_hop_count)
            return True

        if new_ttl == 0:
            return False

        # Unicast packet - need routing
        if self._routes is None:
            # Phase 1: No routing yet, drop unicast packets not for us
            return False

        # Lookup route to destination
        route = self._routes.lookup(dest_id)

        if route is not None:
            # Route found, forward to next hop
            next_hop_mac = route.next_hop_mac
            return await self._forward_unicast(raw, parsed, next_hop_mac, new_ttl, new_hop_count)
        else:
            # No route found, queue packet and start discovery
            await self._queue_pending(dest_id, raw)
            return True

    async def _deliver_local(self, parsed: dict, from_mac: bytes):
        """Deliver packet to local application layer.

        Routes packet to appropriate handler based on type:
        - DATA/DATA_FRAG: application callback
        - PING/PONG: diagnostic handler
        - Other types handled by mesh layer

        Args:
            parsed: Parsed packet dictionary
            from_mac: Sender MAC address
        """
        pkt_type = parsed['pkt_type']
        src_id = parsed['src_id']
        payload = parsed['payload']

        if pkt_type == PKT_DATA:
            # Deliver to application via mesh callback
            if self._mesh._message_callback:
                try:
                    self._mesh._message_callback(src_id, payload)
                except Exception as e:
                    print(f"Error in message callback: {e}")

            # Add to message queue for async iteration
            try:
                self._mesh._message_queue.put_nowait((src_id, payload))
            except:
                # Queue full or not available
                pass

        elif pkt_type == PKT_PING:
            # Respond to ping with pong - extract correct parameters
            await self._mesh._handle_ping(parsed['src_id'], parsed['payload'], 0)

        elif pkt_type == PKT_PONG:
            # Handle pong response - extract correct parameters
            self._mesh._handle_pong(parsed['src_id'], parsed['payload'], 0)

        # Other packet types (RREQ, RREP, RERR, etc.) handled by mesh layer

    async def _rebroadcast(self, raw: bytes, parsed: dict, new_ttl: int, new_hop_count: int):
        """Rebroadcast packet with updated TTL and hop_count.

        Adds random jitter delay to prevent broadcast storm collisions.

        Args:
            raw: Original raw packet bytes
            parsed: Parsed packet dictionary
            new_ttl: Updated TTL value
            new_hop_count: Updated hop count value
        """
        # Add jitter delay to prevent collision
        jitter = random.randint(0, REBROADCAST_JITTER)
        await asyncio.sleep_ms(jitter)

        # Build new packet with updated TTL and hop_count
        new_packet = self._build_forwarded_packet(parsed, new_ttl, new_hop_count)

        # Broadcast via adapter
        try:
            await self._mesh._adapter.send_broadcast(new_packet)
        except Exception as e:
            print(f"Rebroadcast failed: {e}")

    async def _forward_unicast(self, raw: bytes, parsed: dict,
                               next_hop_mac: bytes, new_ttl: int, new_hop_count: int) -> bool:
        """Forward packet to next hop via unicast.

        Args:
            raw: Original raw packet bytes
            parsed: Parsed packet dictionary
            next_hop_mac: MAC address of next hop
            new_ttl: Updated TTL value
            new_hop_count: Updated hop count value

        Returns:
            True if forward succeeded, False otherwise
        """
        # Build new packet with updated TTL and hop_count
        new_packet = self._build_forwarded_packet(parsed, new_ttl, new_hop_count)

        # Ensure peer is registered
        try:
            # Get node_id for next hop from peer manager
            node_id = self._mesh._peer_mgr.get_node_id(next_hop_mac)
            await self._mesh._peer_mgr.ensure_peer(next_hop_mac)
            if node_id is not None:
                self._mesh._peer_mgr.mark_on_route(next_hop_mac, True)
                self._mesh._peer_mgr.set_node_id(next_hop_mac, node_id)
        except Exception as e:
            print(f"Failed to ensure peer: {e}")
            return False

        # Send to next hop
        try:
            await self._mesh._adapter.send(next_hop_mac, new_packet)
            return True
        except Exception as e:
            print(f"Forward failed: {e}")
            return False

    async def _queue_pending(self, dest_id: int, raw: bytes):
        """Queue packet while waiting for route discovery.

        Starts route discovery if not already in progress.

        Args:
            dest_id: Destination node ID
            raw: Raw packet bytes to queue
        """
        # Check if queue is full
        if len(self._pending) >= PENDING_QUEUE_SIZE:
            # Remove oldest entry
            self._pending.pop(0)

        # Add to pending queue with timestamp
        timestamp = ticks_ms()
        self._pending.append((dest_id, raw, timestamp))

        # Start route discovery if available
        if self._discovery is not None:
            try:
                await self._discovery.discover(dest_id)
            except Exception as e:
                print(f"Route discovery failed: {e}")

    async def flush_pending(self, dest_id: int):
        """Send all pending packets for destination after route found.

        Called by route discovery when a route is successfully established.

        Args:
            dest_id: Destination node ID
        """
        # Find all pending packets for this destination
        to_send = []
        remaining = []

        for entry in self._pending:
            if entry[0] == dest_id:
                to_send.append(entry)
            else:
                remaining.append(entry)

        self._pending = remaining

        # Lookup route
        if self._routes is None:
            return

        route = self._routes.lookup(dest_id)
        if route is None:
            return

        next_hop_mac = route.next_hop_mac

        # Send all pending packets
        for dest, raw, timestamp in to_send:
            # Re-parse packet to update TTL/hop_count
            parsed = self._codec.decode(raw)
            if parsed is None:
                continue

            # Forward with current TTL/hop_count
            ttl = parsed['ttl']
            hop_count = parsed['hop_count']

            if ttl > 1:
                await self._forward_unicast(raw, parsed, next_hop_mac, ttl - 1, hop_count + 1)

    def discard_pending(self, dest_id: int):
        """Discard pending packets for unreachable destination.

        Called by route discovery when destination is confirmed unreachable.

        Args:
            dest_id: Destination node ID
        """
        # Remove all pending packets for this destination
        self._pending = [entry for entry in self._pending if entry[0] != dest_id]

    def prune_pending(self):
        """Remove packets older than PENDING_TIMEOUT.

        Should be called periodically by mesh maintenance loop.
        """
        now = ticks_ms()
        self._pending = [
            entry for entry in self._pending
            if ticks_diff(now, entry[2]) < PENDING_TIMEOUT
        ]

    def _build_forwarded_packet(self, parsed: dict, new_ttl: int, new_hop_count: int) -> bytes:
        """Create new packet with updated TTL and hop_count.

        Rebuilds the entire packet with new header values. The payload
        remains unchanged.

        Args:
            parsed: Original parsed packet dictionary
            new_ttl: New TTL value
            new_hop_count: New hop count value

        Returns:
            New packet bytes with updated header
        """
        # Rebuild packet with updated TTL and hop_count
        return self._codec.encode(
            pkt_type=parsed['pkt_type'],
            src_id=parsed['src_id'],
            dest_id=parsed['dest_id'],
            payload=parsed['payload'],
            flags=parsed['flags'],
            pkt_id=parsed['pkt_id'],
            ttl=new_ttl,
            hop_count=new_hop_count,
            frag_seq=parsed['frag_seq'],
            frag_idx=parsed['frag_idx'],
            frag_total=parsed['frag_total'],
            qos=parsed['qos']
        )


# =============================================================================
# PACKET PROCESSING DISPATCHER
# =============================================================================

async def process_incoming_packet(mesh, raw: bytes, from_mac: bytes, rssi: int):
    """Top-level packet processing dispatcher.

    Routes packet to appropriate handler based on type:
    - HELLO: neighbor table update (handled by mesh)
    - DATA/DATA_FRAG: forwarding engine
    - RREQ/RREP/RERR: route discovery (Phase 2+)
    - PING/PONG: diagnostic handler (handled by mesh)
    - ACK/NACK: transport layer (Phase 3+)

    This function should be called from mesh._process_packet() to handle
    packet forwarding logic.

    Args:
        mesh: Main Mesh instance
        raw: Raw packet bytes
        from_mac: Sender MAC address
        rssi: Signal strength in dBm

    Returns:
        True if packet was processed successfully, False otherwise
    """
    # Decode packet to determine type
    codec = PacketCodec()
    parsed = codec.decode(raw)

    if parsed is None:
        # Invalid packet
        return False

    pkt_type = parsed['pkt_type']

    # Route to appropriate handler
    if pkt_type == PKT_HELLO:
        # HELLO packets handled directly by mesh
        mesh._handle_hello(from_mac, parsed, rssi)
        return True

    elif pkt_type in (PKT_DATA, PKT_DATA_FRAG):
        # DATA packets go through forwarding engine
        if hasattr(mesh, '_forward_engine'):
            return await mesh._forward_engine.forward(raw, from_mac, rssi)
        else:
            # No forwarding engine, handle locally only
            if parsed['dest_id'] in (BROADCAST_ID, mesh.node_id):
                await mesh._handle_data_or_forward(from_mac, parsed, raw, rssi)
            return True

    elif pkt_type == PKT_PING:
        # PING handled by mesh
        await mesh._handle_ping(parsed['src_id'], parsed['payload'], 0)
        return True

    elif pkt_type == PKT_PONG:
        # PONG handled by mesh
        mesh._handle_pong(parsed['src_id'], parsed['payload'], 0)
        return True

    elif pkt_type in (PKT_RREQ, PKT_RREP, PKT_RERR):
        # Routing packets (Phase 2+)
        if hasattr(mesh, '_route_discovery'):
            # Route to discovery handler
            pass  # TODO: Implement in Phase 2
        return True

    elif pkt_type in (PKT_ACK, PKT_NACK):
        # Transport layer packets (Phase 3+)
        # TODO: Implement reliable transport
        return True

    else:
        # Unknown packet type
        return False
