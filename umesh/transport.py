"""
µMesh Transport Layer
=====================

Handles fragmentation, reassembly, and reliable delivery for the µMesh protocol.

This module provides three main components:

1. **Fragmenter**: Splits messages larger than 236 bytes into fragments
2. **Reassembler**: Reconstructs fragmented messages from received fragments
3. **ReliableTransport**: Provides ACK-based reliable delivery with retransmission

Key features:
- Automatic fragmentation for messages up to 3540 bytes (15 × 236)
- Sliding window reassembly with timeout-based garbage collection
- ACK/NACK handling with exponential backoff
- Memory-bounded pending reassembly queue

Usage:
    # Fragmentation
    frag = Fragmenter()
    if frag.needs_fragmentation(data):
        packets = frag.fragment(data, src_id, dest_id, pkt_id)
        for pkt in packets:
            await mesh.send_raw(pkt)

    # Reassembly
    reasm = Reassembler()
    msg = reasm.add_fragment(src_id, frag_seq, frag_idx, frag_total, payload)
    if msg:
        print(f"Complete message: {msg}")

    # Reliable delivery
    transport = ReliableTransport(mesh)
    success = await transport.send_reliable(dest_id, data, timeout=2000)
"""

try:
    import uasyncio as asyncio
except ImportError:
    import asyncio

try:
    from utime import ticks_ms, ticks_diff
except ImportError:
    # Fallback for desktop Python
    from time import monotonic
    ticks_ms = lambda: int(monotonic() * 1000)
    ticks_diff = lambda a, b: a - b

from .const import (
    PKT_DATA, PKT_DATA_FRAG, PKT_ACK, PKT_NACK,
    MAX_PAYLOAD, MAX_FRAGMENTS, MAX_MESSAGE_SIZE,
    FLAG_FRAG, FLAG_ACK_REQ,
    REASSEMBLY_TIMEOUT, ACK_TIMEOUT, MAX_RETRIES,
    MAX_PENDING,
)

from .packet import PacketCodec, make_frag_packet


# =============================================================================
# FRAGMENTER - Splits large messages into fragments
# =============================================================================

class Fragmenter:
    """
    Splits messages larger than MAX_PAYLOAD (236 bytes) into fragments.

    Fragment packets use:
    - pkt_type = PKT_DATA_FRAG (0x1)
    - frag_seq: 12-bit sequence number shared across all fragments of a message
    - frag_idx: 4-bit fragment index (0-14)
    - frag_total: 4-bit total fragment count (1-15)
    - FLAG_FRAG set in flags

    Attributes:
        MAX_PAYLOAD: Maximum bytes per fragment (236)
        MAX_FRAGMENTS: Maximum fragments per message (15)
        MAX_MESSAGE: Maximum message size (3540 bytes)
    """

    MAX_PAYLOAD = MAX_PAYLOAD
    MAX_FRAGMENTS = MAX_FRAGMENTS
    MAX_MESSAGE = MAX_MESSAGE_SIZE

    def __init__(self):
        """Initialize fragmenter with sequence counter."""
        self._frag_seq = 0  # 12-bit counter (0-4095)
        self._codec = PacketCodec()

    def needs_fragmentation(self, data: bytes) -> bool:
        """
        Check if data needs to be fragmented.

        Args:
            data: Message payload

        Returns:
            True if data exceeds MAX_PAYLOAD, False otherwise
        """
        return len(data) > self.MAX_PAYLOAD

    def fragment(self, data: bytes, src_id: int, dest_id: int,
                 pkt_id: int, flags: int = 0) -> list:
        """
        Split data into fragment packets.

        Creates a list of complete packets (header + payload), each containing
        a fragment of the original message. All fragments share the same frag_seq
        and have sequential frag_idx values.

        Args:
            data: Message to fragment (max 3540 bytes)
            src_id: Source node ID (16-bit)
            dest_id: Destination node ID (16-bit)
            pkt_id: Packet sequence number
            flags: Additional flags (FLAG_FRAG and FLAG_ACK_REQ will be added)

        Returns:
            List of complete packets (bytes), ready to transmit

        Raises:
            ValueError: If data exceeds MAX_MESSAGE or is too small to fragment
        """
        data_len = len(data)

        if data_len <= self.MAX_PAYLOAD:
            raise ValueError(
                f"Data too small to fragment: {data_len} bytes, "
                f"min {self.MAX_PAYLOAD + 1}"
            )

        if data_len > self.MAX_MESSAGE:
            raise ValueError(
                f"Message too large: {data_len} bytes, max {self.MAX_MESSAGE}"
            )

        # Calculate number of fragments needed
        frag_total = (data_len + self.MAX_PAYLOAD - 1) // self.MAX_PAYLOAD

        if frag_total > self.MAX_FRAGMENTS:
            raise ValueError(
                f"Too many fragments: {frag_total}, max {self.MAX_FRAGMENTS}"
            )

        # Get fragment sequence number for this message
        frag_seq = self._next_frag_seq()

        # Add FLAG_FRAG to flags
        frag_flags = flags | FLAG_FRAG

        packets = []
        offset = 0

        for frag_idx in range(frag_total):
            # Extract fragment payload
            end = min(offset + self.MAX_PAYLOAD, data_len)
            payload = data[offset:end]

            # Build fragment packet
            pkt = self._codec.encode(
                PKT_DATA_FRAG,
                src_id=src_id,
                dest_id=dest_id,
                payload=payload,
                pkt_id=pkt_id,
                flags=frag_flags,
                frag_seq=frag_seq,
                frag_idx=frag_idx,
                frag_total=frag_total
            )

            packets.append(pkt)
            offset = end

        return packets

    def _next_frag_seq(self) -> int:
        """
        Get next fragment sequence number.

        Wraps at 4095 (12-bit counter).

        Returns:
            Fragment sequence number (0-4095)
        """
        seq = self._frag_seq
        self._frag_seq = (self._frag_seq + 1) & 0xFFF  # 12-bit wrap
        return seq


# =============================================================================
# REASSEMBLER - Reconstructs fragmented messages
# =============================================================================

class ReassemblyContext:
    """
    Context for a single message being reassembled.

    Tracks received fragments and combines them into a complete message
    when all fragments are received.

    Attributes:
        src_id: Source node ID
        frag_seq: Fragment sequence number (shared by all fragments)
        frag_total: Total number of expected fragments
        received: Bitmask of received fragment indices (bit N = fragment N)
        fragments: List of fragment payloads (None if not yet received)
        started_ms: Timestamp when first fragment received
    """

    __slots__ = ('src_id', 'frag_seq', 'frag_total', 'received',
                 'fragments', 'started_ms')

    def __init__(self, src_id: int, frag_seq: int, frag_total: int):
        """
        Initialize reassembly context.

        Args:
            src_id: Source node ID
            frag_seq: Fragment sequence number
            frag_total: Total number of fragments expected
        """
        self.src_id = src_id
        self.frag_seq = frag_seq
        self.frag_total = frag_total
        self.fragments = [None] * frag_total  # Pre-allocate slots
        self.received = 0  # Bitmask: bit N = fragment N received
        self.started_ms = ticks_ms()

    def add(self, frag_idx: int, payload: bytes) -> bool:
        """
        Add a fragment to the reassembly context.

        Args:
            frag_idx: Fragment index (0 to frag_total-1)
            payload: Fragment payload bytes

        Returns:
            True if this is a new fragment, False if duplicate

        Raises:
            ValueError: If frag_idx is out of range
        """
        if frag_idx >= self.frag_total or frag_idx < 0:
            raise ValueError(
                f"Fragment index out of range: {frag_idx}, "
                f"expected 0-{self.frag_total - 1}"
            )

        # Check if already received
        bit_mask = 1 << frag_idx
        if self.received & bit_mask:
            return False  # Duplicate

        # Store fragment and mark as received
        self.fragments[frag_idx] = payload
        self.received |= bit_mask

        return True

    def is_complete(self) -> bool:
        """
        Check if all fragments have been received.

        Returns:
            True if complete, False otherwise
        """
        # All bits 0 to frag_total-1 should be set
        expected_mask = (1 << self.frag_total) - 1
        return self.received == expected_mask

    def assemble(self) -> bytes:
        """
        Combine all fragments into complete message.

        Returns:
            Complete reassembled message

        Raises:
            RuntimeError: If not all fragments received
        """
        if not self.is_complete():
            raise RuntimeError(
                f"Cannot assemble incomplete message: "
                f"{bin(self.received)} / {bin((1 << self.frag_total) - 1)}"
            )

        # Concatenate all fragment payloads
        return b''.join(self.fragments)

    def get_missing_indices(self) -> list:
        """
        Get list of missing fragment indices.

        Returns:
            List of missing fragment indices (for NACK)
        """
        missing = []
        for idx in range(self.frag_total):
            if not (self.received & (1 << idx)):
                missing.append(idx)
        return missing


class Reassembler:
    """
    Reassembles fragments into complete messages.

    Manages multiple concurrent reassembly contexts, keyed by (src_id, frag_seq).
    Prunes stale reassemblies that exceed timeout to prevent memory leaks.

    Attributes:
        REASSEMBLY_TIMEOUT: Maximum age of incomplete reassembly (10s)
        MAX_PENDING: Maximum concurrent reassemblies (8)
    """

    REASSEMBLY_TIMEOUT = REASSEMBLY_TIMEOUT
    MAX_PENDING = MAX_PENDING

    def __init__(self, timeout=None, max_pending=None):
        """
        Initialize reassembler.

        Args:
            timeout: Reassembly timeout in ms (default REASSEMBLY_TIMEOUT)
            max_pending: Max concurrent reassemblies (default MAX_PENDING)
        """
        self._timeout = timeout if timeout is not None else self.REASSEMBLY_TIMEOUT
        self._max_pending = max_pending if max_pending is not None else self.MAX_PENDING

        # Key = (src_id, frag_seq), Value = ReassemblyContext
        self._pending = {}

    def add_fragment(self, src_id: int, frag_seq: int, frag_idx: int,
                     frag_total: int, payload: bytes):
        """
        Add fragment to reassembly buffer.

        Creates a new reassembly context if this is the first fragment.
        Returns complete message when all fragments received, None otherwise.

        Args:
            src_id: Source node ID
            frag_seq: Fragment sequence number
            frag_idx: Fragment index (0-based)
            frag_total: Total number of fragments
            payload: Fragment payload bytes

        Returns:
            Complete message (bytes) if all fragments received, None otherwise

        Raises:
            MemoryError: If MAX_PENDING exceeded
            ValueError: If frag_total or frag_idx are out of valid range
        """
        # Validate fragment parameters
        if not (1 <= frag_total <= MAX_FRAGMENTS):
            raise ValueError(f"frag_total must be 1-{MAX_FRAGMENTS}, got {frag_total}")
        if not (0 <= frag_idx < frag_total):
            raise ValueError(f"frag_idx must be 0-{frag_total-1}, got {frag_idx}")

        # Proactively prune stale contexts before checking limit (5.1)
        self.prune_stale()

        key = (src_id, frag_seq)

        # Get or create reassembly context
        if key not in self._pending:
            # Check if we have room for new reassembly
            if len(self._pending) >= self._max_pending:
                raise MemoryError(
                    f"Too many pending reassemblies: {len(self._pending)}, "
                    f"max {self._max_pending}"
                )

            self._pending[key] = ReassemblyContext(src_id, frag_seq, frag_total)

        ctx = self._pending[key]

        # Validate frag_total matches
        if ctx.frag_total != frag_total:
            raise ValueError(
                f"Fragment total mismatch: expected {ctx.frag_total}, got {frag_total}"
            )

        # Add fragment
        ctx.add(frag_idx, payload)

        # Check if complete
        if ctx.is_complete():
            msg = ctx.assemble()
            del self._pending[key]  # Clean up
            return msg

        return None

    def prune_stale(self) -> int:
        """
        Remove incomplete reassemblies older than timeout.

        Returns:
            Count of pruned entries
        """
        now = ticks_ms()
        to_remove = []

        for key, ctx in self._pending.items():
            age = ticks_diff(now, ctx.started_ms)
            if age > self._timeout:
                to_remove.append(key)

        for key in to_remove:
            del self._pending[key]

        return len(to_remove)

    def cancel(self, src_id: int, frag_seq: int):
        """
        Cancel specific reassembly (e.g., on error).

        Args:
            src_id: Source node ID
            frag_seq: Fragment sequence number
        """
        key = (src_id, frag_seq)
        if key in self._pending:
            del self._pending[key]

    def get_context(self, src_id: int, frag_seq: int):
        """
        Get reassembly context for specific message.

        Args:
            src_id: Source node ID
            frag_seq: Fragment sequence number

        Returns:
            ReassemblyContext or None if not found
        """
        key = (src_id, frag_seq)
        return self._pending.get(key)

    @property
    def pending_count(self) -> int:
        """
        Number of in-progress reassemblies.

        Returns:
            Count of pending reassemblies
        """
        return len(self._pending)


# =============================================================================
# RELIABLE TRANSPORT - ACK-based reliable delivery
# =============================================================================

class PendingAck:
    """
    Context for a pending ACK.

    Tracks a sent message waiting for acknowledgement.

    Attributes:
        pkt_id: Packet ID waiting for ACK
        dest_id: Destination node ID
        data: Original message data (for retransmission)
        packets: Pre-built packets (fragmented if necessary)
        sent_ms: Timestamp of last transmission
        retries: Number of retransmission attempts so far
        event: asyncio.Event for ACK notification
    """

    __slots__ = ('pkt_id', 'dest_id', 'data', 'packets', 'sent_ms',
                 'retries', 'event')

    def __init__(self, pkt_id: int, dest_id: int, data: bytes, packets: list):
        """
        Initialize pending ACK context.

        Args:
            pkt_id: Packet ID
            dest_id: Destination node ID
            data: Original message data
            packets: Pre-built packet list (for retransmission)
        """
        self.pkt_id = pkt_id
        self.dest_id = dest_id
        self.data = data
        self.packets = packets
        self.sent_ms = ticks_ms()
        self.retries = 0
        self.event = asyncio.Event()


class ReliableTransport:
    """
    Reliable message delivery with ACK and retransmission.

    Provides send_reliable() which fragments (if necessary), sends packets,
    waits for ACK, and retransmits on timeout up to MAX_RETRIES times.

    Handles received ACK and NACK packets to complete pending transmissions.

    Attributes:
        MAX_RETRIES: Maximum retransmission attempts (3)
        ACK_TIMEOUT: Base ACK timeout in ms (500)
    """

    MAX_RETRIES = MAX_RETRIES
    ACK_TIMEOUT = ACK_TIMEOUT

    def __init__(self, mesh):
        """
        Initialize reliable transport.

        Args:
            mesh: Mesh instance for sending packets
        """
        self._mesh = mesh
        self._fragmenter = Fragmenter()

        # pkt_id -> PendingAck
        self._pending_acks = {}

        # Lock for thread-safe ACK handling
        self._pending_lock = asyncio.Lock()

        # For generating packet IDs
        self._next_pkt_id = 0

    async def send_reliable(self, dest_id: int, data: bytes,
                            timeout: int = None) -> bool:
        """
        Send message with delivery confirmation.

        Automatically fragments if necessary, sets FLAG_ACK_REQ,
        waits for ACK, and retransmits up to MAX_RETRIES times on timeout.

        Args:
            dest_id: Destination node ID (16-bit)
            data: Message data (max 3540 bytes)
            timeout: ACK timeout in ms (default: calculated from hop count)

        Returns:
            True if ACK received, False if all retries exhausted

        Raises:
            ValueError: If data exceeds MAX_MESSAGE_SIZE
        """
        if len(data) > self._fragmenter.MAX_MESSAGE:
            raise ValueError(
                f"Message too large: {len(data)} bytes, "
                f"max {self._fragmenter.MAX_MESSAGE}"
            )

        # Generate packet ID
        pkt_id = self._get_next_pkt_id()

        # Build packets (fragment if necessary)
        if self._fragmenter.needs_fragmentation(data):
            packets = self._fragmenter.fragment(
                data,
                src_id=self._mesh.node_id,
                dest_id=dest_id,
                pkt_id=pkt_id,
                flags=FLAG_ACK_REQ
            )
        else:
            # Single packet
            codec = PacketCodec()
            pkt = codec.encode(
                PKT_DATA,
                src_id=self._mesh.node_id,
                dest_id=dest_id,
                payload=data,
                pkt_id=pkt_id,
                flags=FLAG_ACK_REQ
            )
            packets = [pkt]

        # Create pending ACK context
        pending = PendingAck(pkt_id, dest_id, data, packets)
        self._pending_acks[pkt_id] = pending

        # Calculate timeout
        if timeout is None:
            timeout = self._calc_timeout(dest_id, self.ACK_TIMEOUT)

        try:
            # Transmission loop with retries
            for attempt in range(self.MAX_RETRIES + 1):
                # Send all packets
                for pkt in packets:
                    await self._mesh.send_raw(pkt)

                pending.sent_ms = ticks_ms()
                pending.retries = attempt

                # Wait for ACK
                ack_received = await self._wait_for_ack(pkt_id, timeout)

                if ack_received:
                    return True

                # Timeout - retry if attempts remain
                if attempt < self.MAX_RETRIES:
                    # Exponential backoff: double timeout
                    timeout *= 2

            # All retries exhausted
            return False
        finally:
            # Always clean up pending ACK entry (fixes memory leak)
            if pkt_id in self._pending_acks:
                del self._pending_acks[pkt_id]

    async def send_unreliable(self, dest_id: int, data: bytes) -> bool:
        """
        Fire-and-forget send (no ACK).

        Faster than send_reliable but no delivery guarantee.
        Automatically fragments if necessary.

        Args:
            dest_id: Destination node ID
            data: Message data

        Returns:
            True (always succeeds locally)

        Raises:
            ValueError: If data exceeds MAX_MESSAGE_SIZE
        """
        if len(data) > self._fragmenter.MAX_MESSAGE:
            raise ValueError(
                f"Message too large: {len(data)} bytes, "
                f"max {self._fragmenter.MAX_MESSAGE}"
            )

        # Generate packet ID
        pkt_id = self._get_next_pkt_id()

        # Build packets (fragment if necessary)
        if self._fragmenter.needs_fragmentation(data):
            packets = self._fragmenter.fragment(
                data,
                src_id=self._mesh.node_id,
                dest_id=dest_id,
                pkt_id=pkt_id,
                flags=0  # No ACK_REQ
            )
        else:
            # Single packet
            codec = PacketCodec()
            pkt = codec.encode(
                PKT_DATA,
                src_id=self._mesh.node_id,
                dest_id=dest_id,
                payload=data,
                pkt_id=pkt_id,
                flags=0
            )
            packets = [pkt]

        # Send all packets
        for pkt in packets:
            await self._mesh.send_raw(pkt)

        return True

    async def handle_ack(self, src_id: int, ack_pkt_id: int):
        """
        Process received ACK packet.

        Sets the event for the corresponding pending ACK to wake up
        the send_reliable() task.

        Args:
            src_id: Source node ID (acknowledger)
            ack_pkt_id: Packet ID being acknowledged
        """
        async with self._pending_lock:
            if ack_pkt_id in self._pending_acks:
                pending = self._pending_acks[ack_pkt_id]

                # Verify sender matches destination
                if pending.dest_id == src_id:
                    pending.event.set()

    async def handle_nack(self, src_id: int, nack_pkt_id: int, missing: list):
        """
        Process received NACK packet with selective retransmission.

        Retransmits only the missing fragments specified in the NACK,
        improving efficiency over full message retransmission.

        Args:
            src_id: Source node ID
            nack_pkt_id: Packet ID being NACKed
            missing: List of missing fragment indices
        """
        async with self._pending_lock:
            if nack_pkt_id not in self._pending_acks:
                return

            pending = self._pending_acks[nack_pkt_id]

            # Verify sender matches destination
            if pending.dest_id != src_id:
                return

            # Selective retransmission of missing fragments
            for frag_idx in missing:
                if 0 <= frag_idx < len(pending.packets):
                    await self._mesh.send_raw(pending.packets[frag_idx])

            # Update sent timestamp
            pending.sent_ms = ticks_ms()

    async def _wait_for_ack(self, pkt_id: int, timeout: int) -> bool:
        """
        Wait for ACK with timeout.

        Args:
            pkt_id: Packet ID to wait for
            timeout: Timeout in ms

        Returns:
            True if ACK received, False if timeout
        """
        if pkt_id not in self._pending_acks:
            return False

        pending = self._pending_acks[pkt_id]

        try:
            # Wait for event with timeout
            await asyncio.wait_for(
                pending.event.wait(),
                timeout / 1000.0  # Convert ms to seconds
            )
            return True
        except asyncio.TimeoutError:
            return False

    def _calc_timeout(self, dest_id: int, base: int) -> int:
        """
        Calculate timeout based on hop count (if known).

        Multi-hop routes require longer timeouts. Uses neighbor table
        or routing table to estimate hop count.

        Args:
            dest_id: Destination node ID
            base: Base timeout in ms

        Returns:
            Calculated timeout in ms
        """
        # Try to get hop count from neighbor table
        hop_count = 0

        if hasattr(self._mesh, 'neighbors'):
            neighbor = self._mesh.neighbors.get(dest_id)
            if neighbor:
                hop_count = neighbor.hop_count

        # Scale timeout by hop count (multi-hop takes longer)
        return base * (hop_count + 1)

    def _get_next_pkt_id(self) -> int:
        """
        Get next packet ID.

        16-bit counter that wraps at 65535.

        Returns:
            Packet ID (0-65535)
        """
        pkt_id = self._next_pkt_id
        self._next_pkt_id = (self._next_pkt_id + 1) & 0xFFFF
        return pkt_id


# =============================================================================
# MODULE INFO
# =============================================================================

__all__ = [
    'Fragmenter',
    'Reassembler',
    'ReassemblyContext',
    'ReliableTransport',
    'PendingAck',
]
