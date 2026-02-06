"""
Tests for umesh/packet.py - Packet encoding/decoding
"""

import pytest
from array import array

from umesh.packet import (
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

from umesh.const import (
    PROTO_VERSION,
    PKT_DATA, PKT_HELLO, PKT_ACK, PKT_RREQ, PKT_RREP,
    PKT_PING, PKT_PONG, PKT_DATA_FRAG,
    HEADER_SIZE, MAX_PAYLOAD,
    DEFAULT_TTL, BROADCAST_ID,
    FLAG_FRAG, FLAG_ACK_REQ,
)


class TestPacketCodec:
    """Test PacketCodec class."""

    def test_init(self):
        """Test codec initialization."""
        codec = PacketCodec()
        assert len(codec._hdr_buf) == 14
        assert len(codec._parsed) == 10

    def test_encode_simple_data(self):
        """Test encoding a simple DATA packet."""
        codec = PacketCodec()
        pkt = codec.encode(
            PKT_DATA,
            src_id=0x1234,
            dest_id=0x5678,
            payload=b'Hello',
            pkt_id=42,
            ttl=7
        )

        assert len(pkt) == HEADER_SIZE + 5  # 14 + 5 = 19
        assert pkt[14:] == b'Hello'  # Payload starts at byte 14

    def test_encode_broadcast(self):
        """Test encoding a broadcast packet."""
        codec = PacketCodec()
        pkt = codec.encode(
            PKT_HELLO,
            src_id=0x1234,
            dest_id=BROADCAST_ID,
            pkt_id=1
        )

        assert len(pkt) == HEADER_SIZE  # Header only, no payload

    def test_encode_with_flags(self):
        """Test encoding with flags."""
        codec = PacketCodec()
        pkt = codec.encode(
            PKT_DATA,
            src_id=0x1234,
            dest_id=0x5678,
            payload=b'Test',
            pkt_id=10,
            flags=FLAG_ACK_REQ
        )

        # Decode to verify flags
        parsed = codec.decode(pkt)
        assert parsed['flags'] & FLAG_ACK_REQ

    def test_encode_max_payload(self):
        """Test encoding with maximum payload size."""
        codec = PacketCodec()
        payload = b'X' * MAX_PAYLOAD

        pkt = codec.encode(
            PKT_DATA,
            src_id=0x1234,
            dest_id=0x5678,
            payload=payload,
            pkt_id=1
        )

        assert len(pkt) == HEADER_SIZE + MAX_PAYLOAD  # 14 + 236 = 250

    def test_encode_payload_too_large(self):
        """Test that oversized payload raises error."""
        codec = PacketCodec()
        payload = b'X' * (MAX_PAYLOAD + 1)

        with pytest.raises(ValueError, match="Payload too large"):
            codec.encode(
                PKT_DATA,
                src_id=0x1234,
                dest_id=0x5678,
                payload=payload,
                pkt_id=1
            )

    def test_encode_ttl_too_large(self):
        """Test that TTL > 15 raises error."""
        codec = PacketCodec()

        with pytest.raises(ValueError, match="TTL too large"):
            codec.encode(
                PKT_DATA,
                src_id=0x1234,
                dest_id=0x5678,
                ttl=16
            )

    def test_decode_valid_packet(self):
        """Test decoding a valid packet."""
        codec = PacketCodec()

        # Encode a packet
        payload = b'Hello mesh!'
        pkt = codec.encode(
            PKT_DATA,
            src_id=0x1234,
            dest_id=0x5678,
            payload=payload,
            pkt_id=42,
            ttl=7,
            hop_count=2,
            flags=FLAG_ACK_REQ
        )

        # Decode it
        parsed = codec.decode(pkt)

        assert parsed is not None
        assert parsed['version'] == PROTO_VERSION
        assert parsed['pkt_type'] == PKT_DATA
        assert parsed['src_id'] == 0x1234
        assert parsed['dest_id'] == 0x5678
        assert parsed['pkt_id'] == 42
        assert parsed['ttl'] == 7
        assert parsed['hop_count'] == 2
        assert parsed['flags'] & FLAG_ACK_REQ
        assert parsed['payload'] == payload

    def test_decode_header_only(self):
        """Test decoding packet with no payload."""
        codec = PacketCodec()

        pkt = codec.encode(
            PKT_HELLO,
            src_id=0x1234,
            dest_id=BROADCAST_ID,
            pkt_id=1
        )

        parsed = codec.decode(pkt)

        assert parsed is not None
        assert parsed['pkt_type'] == PKT_HELLO
        assert parsed['payload'] == b''

    def test_decode_corrupted_crc(self):
        """Test that corrupted packet fails CRC check."""
        codec = PacketCodec()

        pkt = bytearray(codec.encode(
            PKT_DATA,
            src_id=0x1234,
            dest_id=0x5678,
            payload=b'Test',
            pkt_id=1
        ))

        # Corrupt the CRC byte
        pkt[13] ^= 0xFF

        parsed = codec.decode(bytes(pkt))
        assert parsed is None

    def test_decode_too_short(self):
        """Test that packet shorter than header is rejected."""
        codec = PacketCodec()
        short_pkt = b'tooshort'

        parsed = codec.decode(short_pkt)
        assert parsed is None

    def test_validate_valid_packet(self):
        """Test validate() with valid packet."""
        codec = PacketCodec()

        pkt = codec.encode(
            PKT_DATA,
            src_id=0x1234,
            dest_id=0x5678,
            payload=b'Test',
            pkt_id=1
        )

        assert codec.validate(pkt) is True

    def test_validate_invalid_packet(self):
        """Test validate() with invalid packet."""
        codec = PacketCodec()

        pkt = bytearray(codec.encode(
            PKT_DATA,
            src_id=0x1234,
            dest_id=0x5678,
            payload=b'Test',
            pkt_id=1
        ))

        # Corrupt the CRC
        pkt[13] ^= 0xFF

        assert codec.validate(bytes(pkt)) is False

    def test_get_payload(self):
        """Test quick payload extraction."""
        codec = PacketCodec()

        payload = b'Quick payload test'
        pkt = codec.encode(
            PKT_DATA,
            src_id=0x1234,
            dest_id=0x5678,
            payload=payload,
            pkt_id=1
        )

        extracted = codec.get_payload(pkt)
        assert extracted == payload

    def test_get_payload_header_only(self):
        """Test get_payload with header-only packet."""
        codec = PacketCodec()

        pkt = codec.encode(
            PKT_HELLO,
            src_id=0x1234,
            dest_id=BROADCAST_ID,
            pkt_id=1
        )

        extracted = codec.get_payload(pkt)
        assert extracted == b''

    def test_make_frag_info(self):
        """Test fragment info packing."""
        codec = PacketCodec()

        frag_info = codec.make_frag_info(seq=100, idx=5, total=10, qos=3)

        # Unpack and verify
        seq, idx, total, qos = codec.unpack_frag_info(frag_info)
        assert seq == 100
        assert idx == 5
        assert total == 10
        assert qos == 3

    def test_make_frag_info_max_values(self):
        """Test fragment info with maximum values."""
        codec = PacketCodec()

        frag_info = codec.make_frag_info(seq=4095, idx=15, total=15, qos=15)

        seq, idx, total, qos = codec.unpack_frag_info(frag_info)
        assert seq == 4095  # 12 bits max
        assert idx == 15    # 4 bits max
        assert total == 15  # 4 bits max
        assert qos == 15    # 4 bits max

    def test_make_frag_info_overflow(self):
        """Test that oversized fragment info raises error."""
        codec = PacketCodec()

        with pytest.raises(ValueError, match="Fragment seq too large"):
            codec.make_frag_info(seq=4096, idx=0, total=0, qos=0)

        with pytest.raises(ValueError, match="Fragment idx too large"):
            codec.make_frag_info(seq=0, idx=16, total=0, qos=0)

        with pytest.raises(ValueError, match="Fragment total too large"):
            codec.make_frag_info(seq=0, idx=0, total=16, qos=0)

        with pytest.raises(ValueError, match="QoS too large"):
            codec.make_frag_info(seq=0, idx=0, total=0, qos=16)

    def test_encode_decode_roundtrip(self):
        """Test full encode/decode roundtrip preserves all fields."""
        codec = PacketCodec()

        # Encode with all fields set
        pkt = codec.encode(
            pkt_type=PKT_DATA,
            src_id=0xABCD,
            dest_id=0x1234,
            payload=b'Roundtrip test',
            flags=FLAG_ACK_REQ | FLAG_FRAG,
            pkt_id=999,
            ttl=15,
            hop_count=5,
            frag_seq=100,
            frag_idx=3,
            frag_total=10,
            qos=7
        )

        # Decode and verify
        parsed = codec.decode(pkt)

        assert parsed['version'] == PROTO_VERSION
        assert parsed['pkt_type'] == PKT_DATA
        assert parsed['src_id'] == 0xABCD
        assert parsed['dest_id'] == 0x1234
        assert parsed['pkt_id'] == 999
        assert parsed['ttl'] == 15
        assert parsed['hop_count'] == 5
        assert parsed['flags'] == (FLAG_ACK_REQ | FLAG_FRAG)
        assert parsed['frag_seq'] == 100
        assert parsed['frag_idx'] == 3
        assert parsed['frag_total'] == 10
        assert parsed['qos'] == 7
        assert parsed['payload'] == b'Roundtrip test'


class TestHelperFunctions:
    """Test packet helper functions."""

    def test_make_data_packet(self):
        """Test make_data_packet helper."""
        codec = PacketCodec()

        pkt = make_data_packet(
            src_id=0x1234,
            dest_id=0x5678,
            payload=b'Hello',
            pkt_id=42,
            ack_req=True
        )

        parsed = codec.decode(pkt)
        assert parsed['pkt_type'] == PKT_DATA
        assert parsed['src_id'] == 0x1234
        assert parsed['dest_id'] == 0x5678
        assert parsed['pkt_id'] == 42
        assert parsed['flags'] & FLAG_ACK_REQ
        assert parsed['payload'] == b'Hello'

    def test_make_data_packet_no_ack(self):
        """Test make_data_packet without ACK request."""
        codec = PacketCodec()

        pkt = make_data_packet(
            src_id=0x1234,
            dest_id=0x5678,
            payload=b'No ACK',
            pkt_id=10,
            ack_req=False
        )

        parsed = codec.decode(pkt)
        assert not (parsed['flags'] & FLAG_ACK_REQ)

    def test_make_hello_packet(self):
        """Test make_hello_packet helper."""
        codec = PacketCodec()

        pkt = make_hello_packet(src_id=0x1234, hop_count=2)

        parsed = codec.decode(pkt)
        assert parsed['pkt_type'] == PKT_HELLO
        assert parsed['src_id'] == 0x1234
        assert parsed['dest_id'] == BROADCAST_ID
        assert parsed['hop_count'] == 2
        assert parsed['payload'] == b''

    def test_make_ack_packet(self):
        """Test make_ack_packet helper."""
        codec = PacketCodec()

        pkt = make_ack_packet(
            src_id=0x5678,
            dest_id=0x1234,
            ack_pkt_id=42
        )

        parsed = codec.decode(pkt)
        assert parsed['pkt_type'] == PKT_ACK
        assert parsed['src_id'] == 0x5678
        assert parsed['dest_id'] == 0x1234

        # Verify payload contains ack_pkt_id
        assert len(parsed['payload']) == 2
        ack_id = (parsed['payload'][0] << 8) | parsed['payload'][1]
        assert ack_id == 42

    def test_make_rreq_packet(self):
        """Test make_rreq_packet helper."""
        codec = PacketCodec()

        pkt = make_rreq_packet(
            src_id=0x1234,
            dest_id=0x5678,
            rreq_id=100,
            src_seq=200
        )

        parsed = codec.decode(pkt)
        assert parsed['pkt_type'] == PKT_RREQ
        assert parsed['src_id'] == 0x1234
        assert parsed['dest_id'] == 0x5678

        # Verify payload format
        payload = parsed['payload']
        assert len(payload) == 4
        rreq_id = (payload[0] << 8) | payload[1]
        src_seq = (payload[2] << 8) | payload[3]
        assert rreq_id == 100
        assert src_seq == 200

    def test_make_rrep_packet(self):
        """Test make_rrep_packet helper."""
        codec = PacketCodec()

        pkt = make_rrep_packet(
            src_id=0x5678,
            dest_id=0x1234,
            rreq_id=100,
            hop_count=5,
            metric=1234
        )

        parsed = codec.decode(pkt)
        assert parsed['pkt_type'] == PKT_RREP
        assert parsed['src_id'] == 0x5678
        assert parsed['dest_id'] == 0x1234
        assert parsed['hop_count'] == 5

        # Verify payload format
        payload = parsed['payload']
        assert len(payload) == 6
        rreq_id = (payload[0] << 8) | payload[1]
        hop_count = payload[2]
        metric = (payload[3] << 16) | (payload[4] << 8) | payload[5]
        assert rreq_id == 100
        assert hop_count == 5
        assert metric == 1234

    def test_make_ping_packet(self):
        """Test make_ping_packet helper."""
        codec = PacketCodec()

        pkt = make_ping_packet(
            src_id=0x1234,
            dest_id=0x5678,
            ping_id=99
        )

        parsed = codec.decode(pkt)
        assert parsed['pkt_type'] == PKT_PING
        assert parsed['src_id'] == 0x1234
        assert parsed['dest_id'] == 0x5678

        # Verify payload
        payload = parsed['payload']
        assert len(payload) == 2
        ping_id = (payload[0] << 8) | payload[1]
        assert ping_id == 99

    def test_make_pong_packet(self):
        """Test make_pong_packet helper."""
        codec = PacketCodec()

        pkt = make_pong_packet(
            src_id=0x5678,
            dest_id=0x1234,
            ping_id=99
        )

        parsed = codec.decode(pkt)
        assert parsed['pkt_type'] == PKT_PONG
        assert parsed['src_id'] == 0x5678
        assert parsed['dest_id'] == 0x1234

        # Verify payload
        payload = parsed['payload']
        assert len(payload) == 2
        ping_id = (payload[0] << 8) | payload[1]
        assert ping_id == 99

    def test_make_frag_packet(self):
        """Test make_frag_packet helper."""
        codec = PacketCodec()

        pkt = make_frag_packet(
            src_id=0x1234,
            dest_id=0x5678,
            payload=b'Fragment 1',
            pkt_id=10,
            frag_seq=5,
            frag_idx=0,
            frag_total=3,
            ack_req=True
        )

        parsed = codec.decode(pkt)
        assert parsed['pkt_type'] == PKT_DATA_FRAG
        assert parsed['src_id'] == 0x1234
        assert parsed['dest_id'] == 0x5678
        assert parsed['pkt_id'] == 10
        assert parsed['frag_seq'] == 5
        assert parsed['frag_idx'] == 0
        assert parsed['frag_total'] == 3
        assert parsed['flags'] & FLAG_FRAG
        assert parsed['flags'] & FLAG_ACK_REQ
        assert parsed['payload'] == b'Fragment 1'


class TestEdgeCases:
    """Test edge cases and boundary conditions."""

    def test_zero_node_ids(self):
        """Test with node ID = 0."""
        codec = PacketCodec()

        pkt = codec.encode(
            PKT_DATA,
            src_id=0,
            dest_id=0,
            pkt_id=1
        )

        parsed = codec.decode(pkt)
        assert parsed['src_id'] == 0
        assert parsed['dest_id'] == 0

    def test_max_node_ids(self):
        """Test with maximum node IDs (0xFFFF)."""
        codec = PacketCodec()

        pkt = codec.encode(
            PKT_DATA,
            src_id=0xFFFF,
            dest_id=0xFFFE,  # 0xFFFF is broadcast
            pkt_id=1
        )

        parsed = codec.decode(pkt)
        assert parsed['src_id'] == 0xFFFF
        assert parsed['dest_id'] == 0xFFFE

    def test_max_pkt_id(self):
        """Test with maximum packet ID."""
        codec = PacketCodec()

        pkt = codec.encode(
            PKT_DATA,
            src_id=0x1234,
            dest_id=0x5678,
            pkt_id=0xFFFF
        )

        parsed = codec.decode(pkt)
        assert parsed['pkt_id'] == 0xFFFF

    def test_multiple_flags(self):
        """Test with multiple flags combined."""
        codec = PacketCodec()

        flags = FLAG_ACK_REQ | FLAG_FRAG
        pkt = codec.encode(
            PKT_DATA,
            src_id=0x1234,
            dest_id=0x5678,
            payload=b'Test',
            pkt_id=1,
            flags=flags
        )

        parsed = codec.decode(pkt)
        assert parsed['flags'] & FLAG_ACK_REQ
        assert parsed['flags'] & FLAG_FRAG

    def test_reuse_codec(self):
        """Test that codec can be reused for multiple operations."""
        codec = PacketCodec()

        # Encode/decode first packet
        pkt1 = codec.encode(PKT_DATA, src_id=1, dest_id=2, payload=b'First', pkt_id=1)
        parsed1 = codec.decode(pkt1)
        assert parsed1['payload'] == b'First'

        # Encode/decode second packet
        pkt2 = codec.encode(PKT_HELLO, src_id=3, dest_id=BROADCAST_ID, pkt_id=2)
        parsed2 = codec.decode(pkt2)
        assert parsed2['pkt_type'] == PKT_HELLO

        # Verify first packet still decodes correctly
        parsed1_again = codec.decode(pkt1)
        assert parsed1_again['payload'] == b'First'


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
