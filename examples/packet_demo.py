"""
µMesh Packet Encoding/Decoding Demo
====================================

This script demonstrates how to use the PacketCodec class and helper functions
to encode and decode various types of mesh packets.

Run this on desktop Python for testing, or on MicroPython ESP32 for production.
"""

from umesh.packet import (
    PacketCodec,
    make_data_packet,
    make_hello_packet,
    make_ack_packet,
    make_rreq_packet,
    make_rrep_packet,
    make_ping_packet,
    make_pong_packet,
)
from umesh.const import (
    PKT_DATA, PKT_HELLO, PKT_ACK, PKT_RREQ, PKT_RREP,
    PKT_PING, PKT_PONG, BROADCAST_ID
)


def demo_basic_codec():
    """Demonstrate basic PacketCodec usage."""
    print("=" * 60)
    print("DEMO: Basic PacketCodec Usage")
    print("=" * 60)

    codec = PacketCodec()

    # Encode a simple data packet
    print("\n1. Encoding DATA packet...")
    pkt = codec.encode(
        pkt_type=PKT_DATA,
        src_id=0x1234,
        dest_id=0x5678,
        payload=b'Hello mesh network!',
        pkt_id=42,
        ttl=7
    )
    print(f"   Encoded {len(pkt)} bytes: {pkt.hex()}")

    # Decode it back
    print("\n2. Decoding packet...")
    parsed = codec.decode(pkt)
    if parsed:
        print(f"   Version:  {parsed['version']}")
        print(f"   Type:     {parsed['pkt_type']} (DATA)")
        print(f"   From:     0x{parsed['src_id']:04X}")
        print(f"   To:       0x{parsed['dest_id']:04X}")
        print(f"   Pkt ID:   {parsed['pkt_id']}")
        print(f"   TTL:      {parsed['ttl']}")
        print(f"   Payload:  {parsed['payload'].decode('utf-8')}")
        print(f"   Checksum: 0x{parsed['checksum']:02X}")
    else:
        print("   ERROR: Failed to decode packet!")

    # Validate without full decode
    print("\n3. Quick validation...")
    is_valid = codec.validate(pkt)
    print(f"   Valid: {is_valid}")

    # Extract payload quickly
    print("\n4. Quick payload extraction...")
    payload = codec.get_payload(pkt)
    print(f"   Payload: {payload.decode('utf-8')}")


def demo_helper_functions():
    """Demonstrate helper functions for common packet types."""
    print("\n" + "=" * 60)
    print("DEMO: Helper Functions")
    print("=" * 60)

    codec = PacketCodec()

    # HELLO beacon
    print("\n1. HELLO beacon (neighbor discovery)...")
    hello_pkt = make_hello_packet(src_id=0x1234, hop_count=0)
    parsed = codec.decode(hello_pkt)
    print(f"   Type: HELLO, From: 0x{parsed['src_id']:04X}, To: 0x{parsed['dest_id']:04X} (broadcast)")

    # DATA packet with ACK request
    print("\n2. DATA packet with ACK request...")
    data_pkt = make_data_packet(
        src_id=0x1234,
        dest_id=0x5678,
        payload=b'Important data',
        pkt_id=100,
        ack_req=True
    )
    parsed = codec.decode(data_pkt)
    print(f"   Type: DATA, Pkt ID: {parsed['pkt_id']}, ACK requested: {bool(parsed['flags'] & 0x02)}")

    # ACK packet
    print("\n3. ACK packet (acknowledge pkt_id=100)...")
    ack_pkt = make_ack_packet(
        src_id=0x5678,
        dest_id=0x1234,
        ack_pkt_id=100
    )
    parsed = codec.decode(ack_pkt)
    ack_id = (parsed['payload'][0] << 8) | parsed['payload'][1]
    print(f"   Type: ACK, Acknowledging pkt_id: {ack_id}")

    # Route Request
    print("\n4. RREQ packet (route discovery)...")
    rreq_pkt = make_rreq_packet(
        src_id=0x1234,
        dest_id=0x9ABC,
        rreq_id=50,
        src_seq=200
    )
    parsed = codec.decode(rreq_pkt)
    payload = parsed['payload']
    rreq_id = (payload[0] << 8) | payload[1]
    src_seq = (payload[2] << 8) | payload[3]
    print(f"   Type: RREQ, Looking for: 0x{parsed['dest_id']:04X}, RREQ ID: {rreq_id}, Seq: {src_seq}")

    # Route Reply
    print("\n5. RREP packet (route reply)...")
    rrep_pkt = make_rrep_packet(
        src_id=0x9ABC,
        dest_id=0x1234,
        rreq_id=50,
        hop_count=3,
        metric=768
    )
    parsed = codec.decode(rrep_pkt)
    payload = parsed['payload']
    rreq_id = (payload[0] << 8) | payload[1]
    hop_count = payload[2]
    metric = (payload[3] << 16) | (payload[4] << 8) | payload[5]
    print(f"   Type: RREP, Replying to RREQ: {rreq_id}, Hops: {hop_count}, Metric: {metric}")

    # PING packet
    print("\n6. PING packet (connectivity test)...")
    ping_pkt = make_ping_packet(
        src_id=0x1234,
        dest_id=0x5678,
        ping_id=99
    )
    parsed = codec.decode(ping_pkt)
    ping_id = (parsed['payload'][0] << 8) | parsed['payload'][1]
    print(f"   Type: PING, Ping ID: {ping_id}")

    # PONG packet
    print("\n7. PONG packet (ping reply)...")
    pong_pkt = make_pong_packet(
        src_id=0x5678,
        dest_id=0x1234,
        ping_id=99
    )
    parsed = codec.decode(pong_pkt)
    ping_id = (parsed['payload'][0] << 8) | parsed['payload'][1]
    print(f"   Type: PONG, Responding to Ping ID: {ping_id}")


def demo_fragmentation():
    """Demonstrate fragmentation support."""
    print("\n" + "=" * 60)
    print("DEMO: Fragmentation")
    print("=" * 60)

    codec = PacketCodec()

    # Large message that needs fragmentation
    large_msg = b"This is a large message that needs to be split into multiple fragments."

    # Split into fragments (simulate)
    frag_size = 30  # Small fragments for demo
    fragments = [large_msg[i:i+frag_size] for i in range(0, len(large_msg), frag_size)]
    total_frags = len(fragments)

    print(f"\nOriginal message ({len(large_msg)} bytes): {large_msg.decode('utf-8')}")
    print(f"Split into {total_frags} fragments of ~{frag_size} bytes each")

    # Encode fragments
    print("\nEncoding fragments...")
    frag_packets = []
    for idx, frag in enumerate(fragments):
        pkt = codec.encode(
            pkt_type=PKT_DATA,
            src_id=0x1234,
            dest_id=0x5678,
            payload=frag,
            pkt_id=200 + idx,
            flags=0x01,  # FLAG_FRAG
            frag_seq=42,
            frag_idx=idx,
            frag_total=total_frags
        )
        frag_packets.append(pkt)
        print(f"  Fragment {idx}/{total_frags-1}: {len(pkt)} bytes")

    # Decode fragments
    print("\nDecoding fragments...")
    reassembled = bytearray()
    for idx, pkt in enumerate(frag_packets):
        parsed = codec.decode(pkt)
        print(f"  Fragment {parsed['frag_idx']}/{parsed['frag_total']-1}: seq={parsed['frag_seq']}, "
              f"payload={len(parsed['payload'])} bytes")
        reassembled.extend(parsed['payload'])

    print(f"\nReassembled message ({len(reassembled)} bytes): {reassembled.decode('utf-8')}")
    print(f"Match: {reassembled == large_msg}")


def demo_performance():
    """Demonstrate performance with many packets."""
    print("\n" + "=" * 60)
    print("DEMO: Performance Test")
    print("=" * 60)

    import time

    codec = PacketCodec()
    iterations = 1000

    # Encode performance
    print(f"\nEncoding {iterations} packets...")
    start = time.time()
    for i in range(iterations):
        pkt = codec.encode(
            PKT_DATA,
            src_id=0x1234,
            dest_id=0x5678,
            payload=b'Test payload',
            pkt_id=i
        )
    encode_time = time.time() - start
    print(f"  Total time: {encode_time*1000:.2f} ms")
    print(f"  Per packet: {encode_time*1000000/iterations:.2f} µs")
    print(f"  Throughput: {iterations/encode_time:.0f} packets/sec")

    # Decode performance
    print(f"\nDecoding {iterations} packets...")
    test_pkt = codec.encode(PKT_DATA, src_id=0x1234, dest_id=0x5678,
                            payload=b'Test payload', pkt_id=1)
    start = time.time()
    for i in range(iterations):
        parsed = codec.decode(test_pkt)
    decode_time = time.time() - start
    print(f"  Total time: {decode_time*1000:.2f} ms")
    print(f"  Per packet: {decode_time*1000000/iterations:.2f} µs")
    print(f"  Throughput: {iterations/decode_time:.0f} packets/sec")

    # Validate performance (fastest operation)
    print(f"\nValidating {iterations} packets...")
    start = time.time()
    for i in range(iterations):
        valid = codec.validate(test_pkt)
    validate_time = time.time() - start
    print(f"  Total time: {validate_time*1000:.2f} ms")
    print(f"  Per packet: {validate_time*1000000/iterations:.2f} µs")
    print(f"  Throughput: {iterations/validate_time:.0f} packets/sec")


if __name__ == '__main__':
    demo_basic_codec()
    demo_helper_functions()
    demo_fragmentation()
    demo_performance()

    print("\n" + "=" * 60)
    print("All demos completed successfully!")
    print("=" * 60)
