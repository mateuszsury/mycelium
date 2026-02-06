"""
µMesh Packet Encoding/Decoding
================================

High-level packet codec for building and parsing µMesh protocol packets.
Provides zero-allocation encoding using pre-allocated buffers and fast
header processing via _fast.py viper functions.

Key features:
- PacketCodec class with pre-allocated buffers
- Helper functions for common packet types
- Fragment info packing/unpacking
- Payload extraction and validation

Usage:
    codec = PacketCodec()

    # Encode a data packet
    pkt = codec.encode(PKT_DATA, src_id=0x1234, dest_id=0x5678,
                       payload=b'Hello mesh!', pkt_id=42)

    # Decode received packet
    parsed = codec.decode(pkt)
    if parsed:
        print(f"From: {parsed['src_id']}, Type: {parsed['pkt_type']}")
        print(f"Payload: {parsed['payload']}")

    # Quick payload extraction
    payload = codec.get_payload(pkt)
"""

try:
    from array import array
    ARRAY_AVAILABLE = True
except ImportError:
    ARRAY_AVAILABLE = False

from .const import (
    PROTO_VERSION,
    PKT_DATA, PKT_DATA_FRAG, PKT_ACK, PKT_NACK,
    PKT_RREQ, PKT_RREP, PKT_RERR, PKT_HELLO,
    PKT_PING, PKT_PONG,
    HEADER_SIZE, MAX_PAYLOAD, MAX_PACKET,
    DEFAULT_TTL, BROADCAST_ID,
    FLAG_FRAG, FLAG_ACK_REQ, FLAG_ENCRYPTED, FLAG_ROUTED,
)

from ._fast import _parse_header, _build_header


# =============================================================================
# PACKET CODEC CLASS
# =============================================================================

class PacketCodec:
    """Packet encoder/decoder with pre-allocated buffers for zero-allocation encoding.

    This class provides high-level methods for building and parsing µMesh packets.
    Internal buffers are reused across encode/decode operations to minimize
    memory allocation in hot paths.

    Attributes:
        _hdr_buf: Pre-allocated 14-byte header buffer
        _parsed: Pre-allocated int array for parsed header fields
    """

    def __init__(self):
        """Initialize codec with pre-allocated buffers."""
        self._hdr_buf = bytearray(14)  # Header buffer for building

        # Parsed header fields array (for viper compatibility)
        if ARRAY_AVAILABLE:
            self._parsed = array('i', [0] * 10)
        else:
            self._parsed = [0] * 10

    def encode(self, pkt_type, src_id, dest_id, payload=b'',
               flags=0, pkt_id=0, ttl=DEFAULT_TTL, hop_count=0,
               frag_seq=0, frag_idx=0, frag_total=0, qos=0) -> bytes:
        """Encode packet with header + payload.

        Builds a complete µMesh packet using the viper-optimized _build_header
        function for maximum performance.

        Args:
            pkt_type: Packet type (PKT_DATA, PKT_HELLO, etc.)
            src_id: Source node ID (16-bit)
            dest_id: Destination node ID (16-bit, 0xFFFF for broadcast)
            payload: Payload bytes (max 236 bytes)
            flags: Flags byte (FLAG_FRAG, FLAG_ACK_REQ, etc.)
            pkt_id: Packet sequence number (16-bit)
            ttl: Time-to-live (default 7, max 15)
            hop_count: Number of hops taken (default 0)
            frag_seq: Fragment sequence number (12-bit)
            frag_idx: Fragment index (4-bit, 0-15)
            frag_total: Total fragments (4-bit, 0-15)
            qos: Quality of Service / channel (4-bit)

        Returns:
            Complete packet as bytes (14-byte header + payload)

        Raises:
            ValueError: If payload exceeds MAX_PAYLOAD or parameters out of range
        """
        if len(payload) > MAX_PAYLOAD:
            raise ValueError(f"Payload too large: {len(payload)} bytes, max {MAX_PAYLOAD}")

        if ttl > 15:
            raise ValueError(f"TTL too large: {ttl}, max 15")

        if dest_id > 0xFFFF or src_id > 0xFFFF:
            raise ValueError("Node IDs must be 16-bit (0-65535)")

        # Pack fragment info (24-bit value)
        frag_info = self.make_frag_info(frag_seq, frag_idx, frag_total, qos)

        # Build header into pre-allocated buffer
        _build_header(
            self._hdr_buf,
            PROTO_VERSION,
            pkt_type,
            flags,
            pkt_id,
            ttl,
            hop_count,
            src_id,
            dest_id,
            frag_info
        )

        # Combine header + payload
        if payload:
            return bytes(self._hdr_buf) + payload
        else:
            return bytes(self._hdr_buf)

    def decode(self, raw: bytes) -> dict:
        """Decode raw packet into header dict + payload.

        Parses packet header using viper-optimized _parse_header and validates
        CRC. Returns None if packet is invalid.

        Args:
            raw: Raw packet bytes (minimum 14 bytes)

        Returns:
            Dictionary with header fields + 'payload' key, or None if CRC fails

        Example:
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
        """
        if len(raw) < HEADER_SIZE:
            return None

        # Parse header using viper function
        ok = _parse_header(raw, self._parsed)
        if not ok:
            return None  # CRC mismatch

        # Unpack fragment info
        frag_seq, frag_idx, frag_total, qos = self.unpack_frag_info(self._parsed[8])

        # Extract payload
        payload = raw[HEADER_SIZE:] if len(raw) > HEADER_SIZE else b''

        return {
            'version': self._parsed[0],
            'pkt_type': self._parsed[1],
            'flags': self._parsed[2],
            'pkt_id': self._parsed[3],
            'ttl': self._parsed[4],
            'hop_count': self._parsed[5],
            'src_id': self._parsed[6],
            'dest_id': self._parsed[7],
            'frag_seq': frag_seq,
            'frag_idx': frag_idx,
            'frag_total': frag_total,
            'qos': qos,
            'checksum': self._parsed[9],
            'payload': payload
        }

    def validate(self, raw: bytes) -> bool:
        """Check if packet has valid CRC and minimum size.

        Quick validation without full parsing. Useful for filtering
        invalid packets before processing.

        Args:
            raw: Raw packet bytes

        Returns:
            True if packet is valid, False otherwise
        """
        if len(raw) < HEADER_SIZE:
            return False

        return _parse_header(raw, self._parsed)

    def get_payload(self, raw: bytes) -> bytes:
        """Extract payload from raw packet (skip 14-byte header).

        Fast payload extraction without full header parsing.
        Does not validate CRC.

        Args:
            raw: Raw packet bytes

        Returns:
            Payload bytes (empty if packet is header-only)
        """
        if len(raw) <= HEADER_SIZE:
            return b''
        return raw[HEADER_SIZE:]

    def make_frag_info(self, seq, idx, total, qos=0) -> int:
        """Pack fragment info into 24-bit value.

        Fragment info layout (24 bits):
            Bits 12-23: Fragment sequence number (12 bits)
            Bits 8-11:  Fragment index (4 bits, 0-15)
            Bits 4-7:   Total fragments (4 bits, 0-15)
            Bits 0-3:   QoS/channel (4 bits)

        Args:
            seq: Fragment sequence number (0-4095)
            idx: Fragment index (0-15)
            total: Total fragments (0-15)
            qos: Quality of Service / channel (0-15)

        Returns:
            24-bit packed value

        Raises:
            ValueError: If parameters out of range
        """
        if seq > 4095:
            raise ValueError(f"Fragment seq too large: {seq}, max 4095 (12 bits)")
        if idx > 15:
            raise ValueError(f"Fragment idx too large: {idx}, max 15 (4 bits)")
        if total > 15:
            raise ValueError(f"Fragment total too large: {total}, max 15 (4 bits)")
        if qos > 15:
            raise ValueError(f"QoS too large: {qos}, max 15 (4 bits)")

        return ((seq & 0xFFF) << 12) | ((idx & 0x0F) << 8) | ((total & 0x0F) << 4) | (qos & 0x0F)

    def unpack_frag_info(self, frag_info: int) -> tuple:
        """Unpack 24-bit fragment info into (seq, idx, total, qos).

        Args:
            frag_info: Packed 24-bit fragment info value

        Returns:
            Tuple (frag_seq, frag_idx, frag_total, qos)
        """
        seq = (frag_info >> 12) & 0xFFF
        idx = (frag_info >> 8) & 0x0F
        total = (frag_info >> 4) & 0x0F
        qos = frag_info & 0x0F
        return (seq, idx, total, qos)


# =============================================================================
# HELPER FUNCTIONS - Common packet builders
# =============================================================================

# Global codec instance for helper functions (avoids repeated allocation)
_codec = PacketCodec()


def make_data_packet(src_id, dest_id, payload, pkt_id, ack_req=False) -> bytes:
    """Build a DATA packet for user payload transmission.

    Args:
        src_id: Source node ID (16-bit)
        dest_id: Destination node ID (16-bit)
        payload: User data (max 236 bytes)
        pkt_id: Packet sequence number
        ack_req: Request acknowledgement (default False)

    Returns:
        Complete DATA packet
    """
    flags = FLAG_ACK_REQ if ack_req else 0
    return _codec.encode(
        PKT_DATA,
        src_id=src_id,
        dest_id=dest_id,
        payload=payload,
        pkt_id=pkt_id,
        flags=flags
    )


def make_hello_packet(src_id, hop_count=0) -> bytes:
    """Build a HELLO beacon packet for neighbor discovery.

    HELLO packets are broadcast periodically to announce presence
    and allow neighbors to measure link quality.

    Args:
        src_id: Source node ID
        hop_count: Number of hops from gateway (default 0)

    Returns:
        Complete HELLO packet
    """
    return _codec.encode(
        PKT_HELLO,
        src_id=src_id,
        dest_id=BROADCAST_ID,
        hop_count=hop_count
    )


def make_ack_packet(src_id, dest_id, ack_pkt_id) -> bytes:
    """Build an ACK packet to acknowledge received data.

    The payload contains the packet ID being acknowledged (2 bytes).

    Args:
        src_id: Source node ID (acknowledger)
        dest_id: Destination node ID (original sender)
        ack_pkt_id: Packet ID being acknowledged

    Returns:
        Complete ACK packet
    """
    # Encode ack_pkt_id as 2-byte payload
    payload = bytes([(ack_pkt_id >> 8) & 0xFF, ack_pkt_id & 0xFF])
    return _codec.encode(
        PKT_ACK,
        src_id=src_id,
        dest_id=dest_id,
        payload=payload
    )


def make_rreq_packet(src_id, dest_id, rreq_id, src_seq) -> bytes:
    """Build a Route Request (RREQ) packet for route discovery.

    Used in AODV-style routing to find a path to a destination.

    Payload format (4 bytes):
        [0-1]: RREQ ID (16-bit)
        [2-3]: Source sequence number (16-bit)

    Args:
        src_id: Source node ID (route requester)
        dest_id: Destination node ID being sought
        rreq_id: Route request ID for deduplication
        src_seq: Source sequence number

    Returns:
        Complete RREQ packet
    """
    payload = bytearray(4)
    payload[0] = (rreq_id >> 8) & 0xFF
    payload[1] = rreq_id & 0xFF
    payload[2] = (src_seq >> 8) & 0xFF
    payload[3] = src_seq & 0xFF

    return _codec.encode(
        PKT_RREQ,
        src_id=src_id,
        dest_id=dest_id,
        payload=bytes(payload)
    )


def make_rrep_packet(src_id, dest_id, rreq_id, hop_count, metric) -> bytes:
    """Build a Route Reply (RREP) packet in response to RREQ.

    Sent by the destination (or intermediate node with route) back
    to the source to establish a route.

    Payload format (6 bytes):
        [0-1]: RREQ ID being replied to (16-bit)
        [2]:   Hop count to destination (8-bit)
        [3-5]: Route metric (24-bit)

    Args:
        src_id: Source node ID (replier, typically the destination)
        dest_id: Destination node ID (original requester)
        rreq_id: RREQ ID being replied to
        hop_count: Hop count to reach destination
        metric: Route quality metric (0-16777215, lower is better)

    Returns:
        Complete RREP packet
    """
    payload = bytearray(6)
    payload[0] = (rreq_id >> 8) & 0xFF
    payload[1] = rreq_id & 0xFF
    payload[2] = hop_count & 0xFF
    payload[3] = (metric >> 16) & 0xFF
    payload[4] = (metric >> 8) & 0xFF
    payload[5] = metric & 0xFF

    return _codec.encode(
        PKT_RREP,
        src_id=src_id,
        dest_id=dest_id,
        payload=bytes(payload),
        hop_count=hop_count
    )


def make_ping_packet(src_id, dest_id, ping_id) -> bytes:
    """Build a PING packet for mesh connectivity testing.

    Args:
        src_id: Source node ID (pinger)
        dest_id: Destination node ID (target)
        ping_id: Ping sequence number (16-bit)

    Returns:
        Complete PING packet
    """
    payload = bytes([(ping_id >> 8) & 0xFF, ping_id & 0xFF])
    return _codec.encode(
        PKT_PING,
        src_id=src_id,
        dest_id=dest_id,
        payload=payload
    )


def make_pong_packet(src_id, dest_id, ping_id) -> bytes:
    """Build a PONG packet in response to PING.

    Args:
        src_id: Source node ID (responder)
        dest_id: Destination node ID (original pinger)
        ping_id: Ping ID being responded to (16-bit)

    Returns:
        Complete PONG packet
    """
    payload = bytes([(ping_id >> 8) & 0xFF, ping_id & 0xFF])
    return _codec.encode(
        PKT_PONG,
        src_id=src_id,
        dest_id=dest_id,
        payload=payload
    )


def make_frag_packet(src_id, dest_id, payload, pkt_id, frag_seq,
                     frag_idx, frag_total, ack_req=False) -> bytes:
    """Build a fragmented DATA packet.

    Args:
        src_id: Source node ID
        dest_id: Destination node ID
        payload: Fragment payload (max 236 bytes)
        pkt_id: Packet sequence number
        frag_seq: Fragment sequence number (12-bit)
        frag_idx: Fragment index (0-15)
        frag_total: Total fragments (1-15)
        ack_req: Request acknowledgement for this fragment

    Returns:
        Complete fragmented packet
    """
    flags = FLAG_FRAG
    if ack_req:
        flags |= FLAG_ACK_REQ

    return _codec.encode(
        PKT_DATA_FRAG,
        src_id=src_id,
        dest_id=dest_id,
        payload=payload,
        pkt_id=pkt_id,
        flags=flags,
        frag_seq=frag_seq,
        frag_idx=frag_idx,
        frag_total=frag_total
    )


# =============================================================================
# MODULE INFO
# =============================================================================

__all__ = [
    # Main codec class
    'PacketCodec',

    # Helper functions
    'make_data_packet',
    'make_hello_packet',
    'make_ack_packet',
    'make_rreq_packet',
    'make_rrep_packet',
    'make_ping_packet',
    'make_pong_packet',
    'make_frag_packet',
]
