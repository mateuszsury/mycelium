"""
Unit tests for umesh.transport module.

Tests fragmentation, reassembly, and reliable transport functionality.
"""

import sys
import pytest
from unittest.mock import Mock, AsyncMock, patch
from time import monotonic

# Desktop Python time compatibility
ticks_ms = lambda: int(monotonic() * 1000)
ticks_diff = lambda a, b: a - b

# Add umesh to path
sys.path.insert(0, '../')

from umesh.transport import (
    Fragmenter,
    Reassembler,
    ReassemblyContext,
    ReliableTransport,
    PendingAck,
)
from umesh.const import (
    PKT_DATA, PKT_DATA_FRAG, PKT_ACK, PKT_NACK,
    MAX_PAYLOAD, MAX_FRAGMENTS, MAX_MESSAGE_SIZE,
    FLAG_FRAG, FLAG_ACK_REQ,
)
from umesh.packet import PacketCodec


# =============================================================================
# FRAGMENTER TESTS
# =============================================================================

def test_fragmenter_init():
    """Test fragmenter initialization."""
    frag = Fragmenter()
    assert frag._frag_seq == 0
    assert frag.MAX_PAYLOAD == MAX_PAYLOAD
    assert frag.MAX_FRAGMENTS == MAX_FRAGMENTS
    assert frag.MAX_MESSAGE == MAX_MESSAGE_SIZE


def test_needs_fragmentation():
    """Test fragmentation detection."""
    frag = Fragmenter()

    # Small data - no fragmentation needed
    small_data = b'x' * 236
    assert not frag.needs_fragmentation(small_data)

    # Large data - fragmentation needed
    large_data = b'x' * 237
    assert frag.needs_fragmentation(large_data)


def test_fragment_basic():
    """Test basic fragmentation."""
    frag = Fragmenter()

    # Data requiring 2 fragments
    data = b'x' * 472  # 2 × 236
    packets = frag.fragment(data, src_id=0x1234, dest_id=0x5678, pkt_id=42)

    assert len(packets) == 2

    # Parse both fragments
    codec = PacketCodec()
    p1 = codec.decode(packets[0])
    p2 = codec.decode(packets[1])

    # Check fragment 0
    assert p1['pkt_type'] == PKT_DATA_FRAG
    assert p1['src_id'] == 0x1234
    assert p1['dest_id'] == 0x5678
    assert p1['pkt_id'] == 42
    assert p1['flags'] & FLAG_FRAG
    assert p1['frag_idx'] == 0
    assert p1['frag_total'] == 2
    assert len(p1['payload']) == 236

    # Check fragment 1
    assert p2['pkt_type'] == PKT_DATA_FRAG
    assert p2['frag_idx'] == 1
    assert p2['frag_total'] == 2
    assert len(p2['payload']) == 236

    # Same frag_seq for both
    assert p1['frag_seq'] == p2['frag_seq']


def test_fragment_max_size():
    """Test fragmentation at maximum size."""
    frag = Fragmenter()

    # Maximum allowed: 15 × 236 = 3540 bytes
    data = b'x' * 3540
    packets = frag.fragment(data, src_id=0x1111, dest_id=0x2222, pkt_id=99)

    assert len(packets) == 15

    # Parse all fragments
    codec = PacketCodec()
    total_payload = 0
    for i, pkt in enumerate(packets):
        p = codec.decode(pkt)
        assert p['frag_idx'] == i
        assert p['frag_total'] == 15
        total_payload += len(p['payload'])

    assert total_payload == 3540


def test_fragment_too_large():
    """Test fragmentation with oversized data."""
    frag = Fragmenter()

    # Too large: 3541 bytes
    data = b'x' * 3541

    with pytest.raises(ValueError, match="Message too large"):
        frag.fragment(data, src_id=0x1111, dest_id=0x2222, pkt_id=1)


def test_fragment_too_small():
    """Test fragmentation with data that doesn't need it."""
    frag = Fragmenter()

    # Too small to fragment
    data = b'x' * 236

    with pytest.raises(ValueError, match="Data too small to fragment"):
        frag.fragment(data, src_id=0x1111, dest_id=0x2222, pkt_id=1)


def test_fragment_ack_req_flag():
    """Test FLAG_ACK_REQ is preserved in fragments."""
    frag = Fragmenter()

    data = b'x' * 500
    packets = frag.fragment(
        data,
        src_id=0x1111,
        dest_id=0x2222,
        pkt_id=1,
        flags=FLAG_ACK_REQ
    )

    codec = PacketCodec()
    for pkt in packets:
        p = codec.decode(pkt)
        assert p['flags'] & FLAG_ACK_REQ
        assert p['flags'] & FLAG_FRAG


def test_fragment_seq_increment():
    """Test fragment sequence number increments."""
    frag = Fragmenter()

    data = b'x' * 500

    # First message
    packets1 = frag.fragment(data, src_id=0x1111, dest_id=0x2222, pkt_id=1)
    codec = PacketCodec()
    seq1 = codec.decode(packets1[0])['frag_seq']

    # Second message
    packets2 = frag.fragment(data, src_id=0x1111, dest_id=0x2222, pkt_id=2)
    seq2 = codec.decode(packets2[0])['frag_seq']

    # Sequence should increment
    assert seq2 == (seq1 + 1) & 0xFFF


def test_fragment_seq_wrap():
    """Test fragment sequence wraps at 4095."""
    frag = Fragmenter()
    frag._frag_seq = 4095  # Set to max

    data = b'x' * 500
    packets = frag.fragment(data, src_id=0x1111, dest_id=0x2222, pkt_id=1)

    codec = PacketCodec()
    seq = codec.decode(packets[0])['frag_seq']
    assert seq == 4095

    # Next should wrap to 0
    packets2 = frag.fragment(data, src_id=0x1111, dest_id=0x2222, pkt_id=2)
    seq2 = codec.decode(packets2[0])['frag_seq']
    assert seq2 == 0


# =============================================================================
# REASSEMBLY CONTEXT TESTS
# =============================================================================

def test_reassembly_context_init():
    """Test ReassemblyContext initialization."""
    ctx = ReassemblyContext(src_id=0x1234, frag_seq=42, frag_total=3)

    assert ctx.src_id == 0x1234
    assert ctx.frag_seq == 42
    assert ctx.frag_total == 3
    assert ctx.received == 0
    assert len(ctx.fragments) == 3
    assert all(f is None for f in ctx.fragments)


def test_reassembly_context_add():
    """Test adding fragments."""
    ctx = ReassemblyContext(src_id=0x1234, frag_seq=42, frag_total=3)

    # Add first fragment
    new = ctx.add(0, b'AAA')
    assert new is True
    assert ctx.fragments[0] == b'AAA'
    assert ctx.received == 0b001

    # Add second fragment
    new = ctx.add(1, b'BBB')
    assert new is True
    assert ctx.fragments[1] == b'BBB'
    assert ctx.received == 0b011

    # Add duplicate
    new = ctx.add(0, b'AAA')
    assert new is False
    assert ctx.received == 0b011  # Unchanged


def test_reassembly_context_is_complete():
    """Test completion detection."""
    ctx = ReassemblyContext(src_id=0x1234, frag_seq=42, frag_total=3)

    assert not ctx.is_complete()

    ctx.add(0, b'AAA')
    assert not ctx.is_complete()

    ctx.add(1, b'BBB')
    assert not ctx.is_complete()

    ctx.add(2, b'CCC')
    assert ctx.is_complete()


def test_reassembly_context_assemble():
    """Test message assembly."""
    ctx = ReassemblyContext(src_id=0x1234, frag_seq=42, frag_total=3)

    ctx.add(0, b'AAA')
    ctx.add(1, b'BBB')
    ctx.add(2, b'CCC')

    msg = ctx.assemble()
    assert msg == b'AAABBBCCC'


def test_reassembly_context_assemble_incomplete():
    """Test assembly with incomplete fragments."""
    ctx = ReassemblyContext(src_id=0x1234, frag_seq=42, frag_total=3)

    ctx.add(0, b'AAA')
    ctx.add(2, b'CCC')  # Missing fragment 1

    with pytest.raises(RuntimeError, match="Cannot assemble incomplete message"):
        ctx.assemble()


def test_reassembly_context_out_of_range():
    """Test fragment index validation."""
    ctx = ReassemblyContext(src_id=0x1234, frag_seq=42, frag_total=3)

    with pytest.raises(ValueError, match="Fragment index out of range"):
        ctx.add(3, b'XXX')  # Index 3 for frag_total=3

    with pytest.raises(ValueError, match="Fragment index out of range"):
        ctx.add(-1, b'XXX')  # Negative index


def test_reassembly_context_get_missing_indices():
    """Test getting missing fragment indices."""
    ctx = ReassemblyContext(src_id=0x1234, frag_seq=42, frag_total=5)

    # All missing
    assert ctx.get_missing_indices() == [0, 1, 2, 3, 4]

    # Add some fragments
    ctx.add(0, b'A')
    ctx.add(2, b'C')
    ctx.add(4, b'E')

    # Missing 1 and 3
    assert ctx.get_missing_indices() == [1, 3]


# =============================================================================
# REASSEMBLER TESTS
# =============================================================================

def test_reassembler_init():
    """Test Reassembler initialization."""
    reasm = Reassembler()
    assert reasm._timeout == reasm.REASSEMBLY_TIMEOUT
    assert reasm._max_pending == reasm.MAX_PENDING
    assert len(reasm._pending) == 0


def test_reassembler_custom_params():
    """Test Reassembler with custom parameters."""
    reasm = Reassembler(timeout=5000, max_pending=4)
    assert reasm._timeout == 5000
    assert reasm._max_pending == 4


def test_reassembler_add_fragment_simple():
    """Test adding fragments to reassembler."""
    reasm = Reassembler()

    # First fragment
    msg = reasm.add_fragment(0x1234, 42, 0, 2, b'AAA')
    assert msg is None  # Not complete yet
    assert reasm.pending_count == 1

    # Second fragment - completes message
    msg = reasm.add_fragment(0x1234, 42, 1, 2, b'BBB')
    assert msg == b'AAABBB'
    assert reasm.pending_count == 0  # Cleaned up


def test_reassembler_multiple_messages():
    """Test reassembling multiple concurrent messages."""
    reasm = Reassembler()

    # Message 1 from node 0x1111
    reasm.add_fragment(0x1111, 10, 0, 2, b'A1')
    assert reasm.pending_count == 1

    # Message 2 from node 0x2222
    reasm.add_fragment(0x2222, 20, 0, 3, b'B1')
    assert reasm.pending_count == 2

    # Complete message 1
    msg1 = reasm.add_fragment(0x1111, 10, 1, 2, b'A2')
    assert msg1 == b'A1A2'
    assert reasm.pending_count == 1

    # Message 2 still pending
    reasm.add_fragment(0x2222, 20, 1, 3, b'B2')
    assert reasm.pending_count == 1

    # Complete message 2
    msg2 = reasm.add_fragment(0x2222, 20, 2, 3, b'B3')
    assert msg2 == b'B1B2B3'
    assert reasm.pending_count == 0


def test_reassembler_frag_total_mismatch():
    """Test error when frag_total changes."""
    reasm = Reassembler()

    # First fragment: frag_total=2
    reasm.add_fragment(0x1234, 42, 0, 2, b'AAA')

    # Second fragment: frag_total=3 (mismatch!)
    with pytest.raises(ValueError, match="Fragment total mismatch"):
        reasm.add_fragment(0x1234, 42, 1, 3, b'BBB')


def test_reassembler_max_pending():
    """Test MAX_PENDING limit."""
    reasm = Reassembler(max_pending=2)

    # Add first message
    reasm.add_fragment(0x1111, 10, 0, 2, b'A')
    assert reasm.pending_count == 1

    # Add second message
    reasm.add_fragment(0x2222, 20, 0, 2, b'B')
    assert reasm.pending_count == 2

    # Third message should fail
    with pytest.raises(MemoryError, match="Too many pending reassemblies"):
        reasm.add_fragment(0x3333, 30, 0, 2, b'C')


def test_reassembler_prune_stale():
    """Test pruning stale reassemblies."""
    reasm = Reassembler(timeout=100)  # 100ms timeout

    # Add incomplete message
    reasm.add_fragment(0x1234, 42, 0, 2, b'AAA')
    assert reasm.pending_count == 1

    # Prune immediately - should not remove (too fresh)
    pruned = reasm.prune_stale()
    assert pruned == 0
    assert reasm.pending_count == 1

    # Wait for timeout (mock ticks_ms to simulate time passing)
    import umesh.transport as transport_module
    original_ticks_ms = transport_module.ticks_ms
    original_ticks_diff = transport_module.ticks_diff

    try:
        # Simulate 150ms passing
        start_time = original_ticks_ms()
        transport_module.ticks_ms = lambda: start_time + 150
        transport_module.ticks_diff = lambda a, b: a - b

        # Prune again - should remove stale entry
        pruned = reasm.prune_stale()
        assert pruned == 1
        assert reasm.pending_count == 0

    finally:
        transport_module.ticks_ms = original_ticks_ms
        transport_module.ticks_diff = original_ticks_diff


def test_reassembler_cancel():
    """Test canceling reassembly."""
    reasm = Reassembler()

    # Add incomplete message
    reasm.add_fragment(0x1234, 42, 0, 2, b'AAA')
    assert reasm.pending_count == 1

    # Cancel it
    reasm.cancel(0x1234, 42)
    assert reasm.pending_count == 0

    # Cancel non-existent (should not raise)
    reasm.cancel(0x5678, 99)


def test_reassembler_get_context():
    """Test getting reassembly context."""
    reasm = Reassembler()

    # No context initially
    ctx = reasm.get_context(0x1234, 42)
    assert ctx is None

    # Add fragment
    reasm.add_fragment(0x1234, 42, 0, 2, b'AAA')

    # Get context
    ctx = reasm.get_context(0x1234, 42)
    assert ctx is not None
    assert ctx.src_id == 0x1234
    assert ctx.frag_seq == 42


# =============================================================================
# RELIABLE TRANSPORT TESTS
# =============================================================================

@pytest.fixture
def mock_mesh():
    """Create mock mesh for testing."""
    mesh = Mock()
    mesh.node_id = 0x1234
    mesh.send_raw = AsyncMock()
    mesh.neighbors = Mock()
    mesh.neighbors.get = Mock(return_value=None)
    return mesh


@pytest.mark.asyncio
async def test_reliable_transport_init(mock_mesh):
    """Test ReliableTransport initialization."""
    transport = ReliableTransport(mock_mesh)
    assert transport._mesh is mock_mesh
    assert transport.MAX_RETRIES == 3
    assert transport.ACK_TIMEOUT == 500


@pytest.mark.asyncio
async def test_send_unreliable_small(mock_mesh):
    """Test unreliable send with small message."""
    transport = ReliableTransport(mock_mesh)

    data = b'Hello mesh!'
    success = await transport.send_unreliable(0x5678, data)

    assert success is True
    assert mock_mesh.send_raw.call_count == 1  # Single packet


@pytest.mark.asyncio
async def test_send_unreliable_fragmented(mock_mesh):
    """Test unreliable send with fragmented message."""
    transport = ReliableTransport(mock_mesh)

    data = b'x' * 500  # Requires 3 fragments
    success = await transport.send_unreliable(0x5678, data)

    assert success is True
    assert mock_mesh.send_raw.call_count == 3  # Three fragments


@pytest.mark.asyncio
async def test_send_unreliable_too_large(mock_mesh):
    """Test unreliable send with oversized message."""
    transport = ReliableTransport(mock_mesh)

    data = b'x' * 4000  # Exceeds MAX_MESSAGE_SIZE
    with pytest.raises(ValueError, match="Message too large"):
        await transport.send_unreliable(0x5678, data)


@pytest.mark.asyncio
async def test_send_reliable_success(mock_mesh):
    """Test reliable send with ACK received."""
    transport = ReliableTransport(mock_mesh)

    data = b'Hello mesh!'

    # Create task to send ACK after short delay
    async def send_ack():
        import asyncio
        await asyncio.sleep(0.01)  # 10ms delay
        # Simulate ACK received
        pkt_id = list(transport._pending_acks.keys())[0]
        await transport.handle_ack(0x5678, pkt_id)

    # Start ACK sender
    import asyncio
    ack_task = asyncio.create_task(send_ack())

    # Send message
    success = await transport.send_reliable(0x5678, data, timeout=100)

    await ack_task

    assert success is True
    assert mock_mesh.send_raw.call_count == 1  # No retries needed


@pytest.mark.asyncio
async def test_send_reliable_timeout(mock_mesh):
    """Test reliable send with timeout (no ACK)."""
    transport = ReliableTransport(mock_mesh)

    data = b'Hello mesh!'

    # Send without ACK - should timeout and retry
    success = await transport.send_reliable(0x5678, data, timeout=10)

    assert success is False
    # Should try MAX_RETRIES + 1 times (initial + 3 retries)
    assert mock_mesh.send_raw.call_count == 4


@pytest.mark.asyncio
async def test_send_reliable_fragmented(mock_mesh):
    """Test reliable send with fragmented message."""
    transport = ReliableTransport(mock_mesh)

    data = b'x' * 500  # Requires 3 fragments

    # Send ACK after first transmission
    async def send_ack():
        import asyncio
        await asyncio.sleep(0.01)
        pkt_id = list(transport._pending_acks.keys())[0]
        await transport.handle_ack(0x5678, pkt_id)

    import asyncio
    ack_task = asyncio.create_task(send_ack())

    success = await transport.send_reliable(0x5678, data, timeout=100)

    await ack_task

    assert success is True
    assert mock_mesh.send_raw.call_count == 3  # Three fragments


@pytest.mark.asyncio
async def test_handle_ack_wrong_source(mock_mesh):
    """Test ACK from wrong source is ignored."""
    transport = ReliableTransport(mock_mesh)

    data = b'Hello mesh!'

    # Send ACK from wrong node
    async def send_wrong_ack():
        import asyncio
        await asyncio.sleep(0.01)
        pkt_id = list(transport._pending_acks.keys())[0]
        # ACK from wrong node (0x9999 instead of 0x5678)
        await transport.handle_ack(0x9999, pkt_id)

    import asyncio
    ack_task = asyncio.create_task(send_wrong_ack())

    # Should timeout because ACK is from wrong source
    success = await transport.send_reliable(0x5678, data, timeout=50)

    await ack_task

    assert success is False


def test_calc_timeout_direct_neighbor(mock_mesh):
    """Test timeout calculation for direct neighbor."""
    transport = ReliableTransport(mock_mesh)

    # Mock neighbor with hop_count=0
    neighbor = Mock()
    neighbor.hop_count = 0
    mock_mesh.neighbors.get.return_value = neighbor

    timeout = transport._calc_timeout(0x5678, 500)
    assert timeout == 500  # base * (0 + 1)


def test_calc_timeout_multi_hop(mock_mesh):
    """Test timeout calculation for multi-hop route."""
    transport = ReliableTransport(mock_mesh)

    # Mock neighbor with hop_count=3
    neighbor = Mock()
    neighbor.hop_count = 3
    mock_mesh.neighbors.get.return_value = neighbor

    timeout = transport._calc_timeout(0x5678, 500)
    assert timeout == 2000  # base * (3 + 1)


def test_calc_timeout_unknown_node(mock_mesh):
    """Test timeout calculation for unknown node."""
    transport = ReliableTransport(mock_mesh)

    # No neighbor info
    mock_mesh.neighbors.get.return_value = None

    timeout = transport._calc_timeout(0x5678, 500)
    assert timeout == 500  # base * (0 + 1)


def test_packet_id_increment(mock_mesh):
    """Test packet ID counter increments."""
    transport = ReliableTransport(mock_mesh)

    pkt_id1 = transport._get_next_pkt_id()
    pkt_id2 = transport._get_next_pkt_id()

    assert pkt_id2 == pkt_id1 + 1


def test_packet_id_wrap(mock_mesh):
    """Test packet ID wraps at 65535."""
    transport = ReliableTransport(mock_mesh)

    transport._next_pkt_id = 0xFFFF
    pkt_id1 = transport._get_next_pkt_id()
    assert pkt_id1 == 0xFFFF

    pkt_id2 = transport._get_next_pkt_id()
    assert pkt_id2 == 0


# =============================================================================
# PENDING ACK TESTS
# =============================================================================

def test_pending_ack_init():
    """Test PendingAck initialization."""
    packets = [b'pkt1', b'pkt2']
    pending = PendingAck(pkt_id=42, dest_id=0x5678, data=b'test', packets=packets)

    assert pending.pkt_id == 42
    assert pending.dest_id == 0x5678
    assert pending.data == b'test'
    assert pending.packets == packets
    assert pending.retries == 0
    assert not pending.event.is_set()


# =============================================================================
# INTEGRATION TESTS
# =============================================================================

def test_fragment_and_reassemble():
    """Test full fragmentation and reassembly cycle."""
    frag = Fragmenter()
    reasm = Reassembler()

    # Create large message
    original = b'x' * 1000

    # Fragment it
    packets = frag.fragment(original, src_id=0x1234, dest_id=0x5678, pkt_id=99)

    # Parse and reassemble
    codec = PacketCodec()
    for pkt in packets:
        parsed = codec.decode(pkt)
        msg = reasm.add_fragment(
            parsed['src_id'],
            parsed['frag_seq'],
            parsed['frag_idx'],
            parsed['frag_total'],
            parsed['payload']
        )

    # Should get complete message on last fragment
    assert msg == original


def test_fragment_and_reassemble_out_of_order():
    """Test reassembly with out-of-order fragments."""
    frag = Fragmenter()
    reasm = Reassembler()

    # Create large message
    original = b'ABCDEFGHIJKLMNOPQRSTUVWXYZ' * 50  # 1300 bytes

    # Fragment it
    packets = frag.fragment(original, src_id=0x1234, dest_id=0x5678, pkt_id=99)

    # Shuffle packets to simulate out-of-order delivery
    import random
    shuffled = packets.copy()
    random.shuffle(shuffled)

    # Reassemble in shuffled order
    codec = PacketCodec()
    msg = None
    for pkt in shuffled:
        parsed = codec.decode(pkt)
        msg = reasm.add_fragment(
            parsed['src_id'],
            parsed['frag_seq'],
            parsed['frag_idx'],
            parsed['frag_total'],
            parsed['payload']
        )
        if msg:
            break

    # Should still get complete message
    assert msg == original


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
