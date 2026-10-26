"""
Unit tests for umesh.file module.

Tests file transfer functionality including chunking, reassembly,
CRC32 verification, and progress tracking.
"""

import sys
import pytest
import asyncio
from unittest.mock import Mock, AsyncMock, MagicMock
from time import monotonic

# Desktop Python time compatibility
ticks_ms = lambda: int(monotonic() * 1000)
ticks_diff = lambda a, b: a - b

# Add umesh to path
sys.path.insert(0, '../')

from umesh.file import (
    FileTransfer,
    ReceiveContext,
    FILE_START, FILE_CHUNK, FILE_END, FILE_ACK,
    ACK_OK, ACK_CHECKSUM_ERROR, ACK_TIMEOUT,
)
from umesh.const import PKT_CTRL

try:
    from binascii import crc32
except ImportError:
    # Use fallback from file.py
    from umesh.file import crc32


# =============================================================================
# MOCK MESH
# =============================================================================

class MockMesh:
    """Mock mesh for testing."""

    def __init__(self, node_id=0x1234):
        self.node_id = node_id
        self.sent_packets = []

    async def send_raw(self, packet, dest_mac=None):
        """Record sent packet."""
        self.sent_packets.append(packet)


# =============================================================================
# RECEIVE CONTEXT TESTS
# =============================================================================

def test_receive_context_init():
    """Test ReceiveContext initialization."""
    ctx = ReceiveContext(
        src_id=0x1234,
        transfer_id=42,
        filename="test.txt",
        total_chunks=3,
        total_size=500
    )

    assert ctx.src_id == 0x1234
    assert ctx.transfer_id == 42
    assert ctx.filename == "test.txt"
    assert ctx.total_chunks == 3
    assert ctx.total_size == 500
    assert len(ctx.chunks) == 3
    assert ctx.received_mask == 0
    assert not ctx.is_complete()


def test_receive_context_add_chunk():
    """Test adding chunks to receive context."""
    ctx = ReceiveContext(0x1234, 42, "test.txt", 3, 500)

    # Add first chunk
    complete = ctx.add_chunk(0, b'chunk0')
    assert not complete
    assert ctx.chunks[0] == b'chunk0'
    assert ctx.received_mask == 0b001

    # Add third chunk
    complete = ctx.add_chunk(2, b'chunk2')
    assert not complete
    assert ctx.chunks[2] == b'chunk2'
    assert ctx.received_mask == 0b101

    # Add second chunk (completes transfer)
    complete = ctx.add_chunk(1, b'chunk1')
    assert complete
    assert ctx.chunks[1] == b'chunk1'
    assert ctx.received_mask == 0b111
    assert ctx.is_complete()


def test_receive_context_duplicate_chunk():
    """Test handling duplicate chunks."""
    ctx = ReceiveContext(0x1234, 42, "test.txt", 2, 200)

    # Add chunk 0
    complete = ctx.add_chunk(0, b'first')
    assert not complete

    # Add chunk 0 again (duplicate)
    complete = ctx.add_chunk(0, b'duplicate')
    assert not complete
    # Original data should remain
    assert ctx.chunks[0] == b'first'


def test_receive_context_invalid_chunk_index():
    """Test invalid chunk index."""
    ctx = ReceiveContext(0x1234, 42, "test.txt", 2, 200)

    with pytest.raises(ValueError):
        ctx.add_chunk(2, b'invalid')  # Only 0-1 valid

    with pytest.raises(ValueError):
        ctx.add_chunk(-1, b'invalid')


def test_receive_context_assemble():
    """Test assembling chunks into complete file."""
    ctx = ReceiveContext(0x1234, 42, "test.txt", 3, 18)

    ctx.add_chunk(0, b'Hello ')
    ctx.add_chunk(1, b'World')
    ctx.add_chunk(2, b'!')

    assert ctx.is_complete()

    data = ctx.assemble()
    assert data == b'Hello World!'


def test_receive_context_assemble_incomplete():
    """Test assembling incomplete transfer."""
    ctx = ReceiveContext(0x1234, 42, "test.txt", 3, 18)

    ctx.add_chunk(0, b'Hello ')
    # Missing chunks 1 and 2

    assert not ctx.is_complete()

    with pytest.raises(RuntimeError):
        ctx.assemble()


def test_receive_context_get_missing_indices():
    """Test getting missing chunk indices."""
    ctx = ReceiveContext(0x1234, 42, "test.txt", 4, 400)

    # Add chunks 0 and 2
    ctx.add_chunk(0, b'chunk0')
    ctx.add_chunk(2, b'chunk2')

    missing = ctx.get_missing_indices()
    assert missing == [1, 3]


def test_receive_context_bytes_received():
    """Test calculating bytes received."""
    ctx = ReceiveContext(0x1234, 42, "test.txt", 3, 18)

    assert ctx.bytes_received() == 0

    ctx.add_chunk(0, b'Hello ')  # 6 bytes
    assert ctx.bytes_received() == 6

    ctx.add_chunk(2, b'!')  # 1 byte
    assert ctx.bytes_received() == 7

    ctx.add_chunk(1, b'World')  # 5 bytes
    assert ctx.bytes_received() == 12


# =============================================================================
# FILE TRANSFER TESTS
# =============================================================================

def test_file_transfer_init():
    """Test FileTransfer initialization."""
    mesh = MockMesh()
    ft = FileTransfer(mesh)

    assert ft._mesh is mesh
    assert ft._transport is None
    assert ft._next_transfer_id == 0
    assert len(ft._pending_recv) == 0


def test_file_transfer_init_with_transport():
    """Test FileTransfer initialization with transport."""
    mesh = MockMesh()
    transport = Mock()
    ft = FileTransfer(mesh, transport)

    assert ft._transport is transport


@pytest.mark.asyncio
async def test_send_file_simple():
    """Test sending a simple file."""
    mesh = MockMesh()
    ft = FileTransfer(mesh)

    # Auto-ACK: background task that signals ACK once pending_send is populated
    async def auto_ack():
        while True:
            await asyncio.sleep(0.01)
            for tid, pending in list(ft._pending_send.items()):
                pending['status'] = ACK_OK
                pending['event'].set()
                return

    ack_task = asyncio.create_task(auto_ack())

    # Small file (fits in one chunk)
    data = b'Hello, World!'
    success = await ft.send_file(0x5678, "test.txt", data)

    await ack_task

    assert success
    # Should send: FILE_START, FILE_CHUNK, FILE_END
    assert len(mesh.sent_packets) == 3


@pytest.mark.asyncio
async def test_send_file_multiple_chunks():
    """Test sending file with multiple chunks."""
    mesh = MockMesh()
    ft = FileTransfer(mesh)

    # Auto-ACK: background task that signals ACK once pending_send is populated
    async def auto_ack():
        while True:
            await asyncio.sleep(0.01)
            for tid, pending in list(ft._pending_send.items()):
                pending['status'] = ACK_OK
                pending['event'].set()
                return

    ack_task = asyncio.create_task(auto_ack())

    # Large file (requires 3 chunks)
    data = b'A' * 500  # 500 bytes = 3 chunks @ 200 bytes/chunk

    progress_calls = []
    def on_progress(sent, total):
        progress_calls.append((sent, total))

    success = await ft.send_file(0x5678, "large.bin", data, on_progress)

    await ack_task

    assert success
    # Should send: FILE_START, 3x FILE_CHUNK, FILE_END
    assert len(mesh.sent_packets) == 5

    # Check progress callbacks
    assert len(progress_calls) == 3
    assert progress_calls[0] == (200, 500)
    assert progress_calls[1] == (400, 500)
    assert progress_calls[2] == (500, 500)


@pytest.mark.asyncio
async def test_send_file_empty_data():
    """Test sending empty file."""
    mesh = MockMesh()
    ft = FileTransfer(mesh)

    with pytest.raises(ValueError, match="Cannot send empty file"):
        await ft.send_file(0x5678, "empty.txt", b'')


@pytest.mark.asyncio
async def test_send_file_long_filename():
    """Test sending file with too long filename."""
    mesh = MockMesh()
    ft = FileTransfer(mesh)

    # Filename > 255 bytes
    long_name = 'A' * 256 + '.txt'

    with pytest.raises(ValueError, match="Filename too long"):
        await ft.send_file(0x5678, long_name, b'test')


@pytest.mark.asyncio
async def test_recv_file_timeout():
    """Test recv_file timeout."""
    mesh = MockMesh()
    ft = FileTransfer(mesh)

    with pytest.raises(asyncio.TimeoutError):
        await ft.recv_file(timeout=100)  # 100ms timeout


@pytest.mark.asyncio
async def test_on_file_received_callback():
    """Test file received callback."""
    mesh = MockMesh()
    ft = FileTransfer(mesh)

    received_files = []
    def callback(src_id, filename, data):
        received_files.append((src_id, filename, data))

    ft.on_file_received(callback)

    # Simulate receiving a file transfer
    # FILE_START
    src_id = 0x5678
    transfer_id = 0
    total_chunks = 1
    total_size = 5
    filename = "test.txt"

    import struct
    filename_bytes = filename.encode('utf-8')
    start_payload = bytearray(10 + len(filename_bytes))
    start_payload[0] = FILE_START
    struct.pack_into('>HHI', start_payload, 1, transfer_id, total_chunks, total_size)
    start_payload[9] = len(filename_bytes)
    start_payload[10:] = filename_bytes

    ft.handle_file_packet(src_id, bytes(start_payload))

    # FILE_CHUNK
    chunk_data = b'Hello'
    chunk_payload = bytearray(5 + len(chunk_data))
    chunk_payload[0] = FILE_CHUNK
    struct.pack_into('>HH', chunk_payload, 1, transfer_id, 0)
    chunk_payload[5:] = chunk_data

    ft.handle_file_packet(src_id, bytes(chunk_payload))

    # FILE_END
    checksum = crc32(chunk_data) & 0xFFFFFFFF
    end_payload = bytearray(7)
    end_payload[0] = FILE_END
    struct.pack_into('>HI', end_payload, 1, transfer_id, checksum)

    ft.handle_file_packet(src_id, bytes(end_payload))

    # Check callback was invoked
    assert len(received_files) == 1
    assert received_files[0] == (src_id, filename, chunk_data)


@pytest.mark.asyncio
async def test_recv_file_integration():
    """Test recv_file() with simulated transfer."""
    mesh = MockMesh()
    ft = FileTransfer(mesh)

    # Start async task to receive
    async def receive_task():
        filename, data = await ft.recv_file(timeout=5000)
        return filename, data

    recv_coro = receive_task()

    # Give it a moment to start waiting
    await asyncio.sleep(0.01)

    # Simulate receiving a file transfer
    src_id = 0x5678
    transfer_id = 0
    filename = "test.txt"
    file_data = b'Hello, World!'

    # FILE_START
    import struct
    filename_bytes = filename.encode('utf-8')
    total_chunks = 1
    total_size = len(file_data)

    start_payload = bytearray(10 + len(filename_bytes))
    start_payload[0] = FILE_START
    struct.pack_into('>HHI', start_payload, 1, transfer_id, total_chunks, total_size)
    start_payload[9] = len(filename_bytes)
    start_payload[10:] = filename_bytes

    ft.handle_file_packet(src_id, bytes(start_payload))

    # FILE_CHUNK
    chunk_payload = bytearray(5 + len(file_data))
    chunk_payload[0] = FILE_CHUNK
    struct.pack_into('>HH', chunk_payload, 1, transfer_id, 0)
    chunk_payload[5:] = file_data

    ft.handle_file_packet(src_id, bytes(chunk_payload))

    # FILE_END
    checksum = crc32(file_data) & 0xFFFFFFFF
    end_payload = bytearray(7)
    end_payload[0] = FILE_END
    struct.pack_into('>HI', end_payload, 1, transfer_id, checksum)

    ft.handle_file_packet(src_id, bytes(end_payload))

    # Wait for receive task to complete
    result = await recv_coro

    assert result == (filename, file_data)


def test_handle_file_start():
    """Test handling FILE_START packet."""
    mesh = MockMesh()
    ft = FileTransfer(mesh)

    src_id = 0x5678
    transfer_id = 42
    total_chunks = 3
    total_size = 500
    filename = "test.txt"

    # Build FILE_START payload
    import struct
    filename_bytes = filename.encode('utf-8')
    payload = bytearray(10 + len(filename_bytes))
    payload[0] = FILE_START
    struct.pack_into('>HHI', payload, 1, transfer_id, total_chunks, total_size)
    payload[9] = len(filename_bytes)
    payload[10:] = filename_bytes

    ft.handle_file_packet(src_id, bytes(payload))

    # Check receive context created
    assert transfer_id in ft._pending_recv
    ctx = ft._pending_recv[transfer_id]
    assert ctx.src_id == src_id
    assert ctx.filename == filename
    assert ctx.total_chunks == total_chunks
    assert ctx.total_size == total_size


def test_handle_file_chunk():
    """Test handling FILE_CHUNK packet."""
    mesh = MockMesh()
    ft = FileTransfer(mesh)

    src_id = 0x5678
    transfer_id = 42

    # Create receive context manually
    ctx = ft._start_receive(src_id, transfer_id, 2, 10, "test.txt")

    # Build FILE_CHUNK payload
    import struct
    chunk_data = b'Hello'
    payload = bytearray(5 + len(chunk_data))
    payload[0] = FILE_CHUNK
    struct.pack_into('>HH', payload, 1, transfer_id, 0)
    payload[5:] = chunk_data

    ft.handle_file_packet(src_id, bytes(payload))

    # Check chunk was added
    assert ctx.chunks[0] == chunk_data
    assert ctx.received_mask == 0b01


@pytest.mark.asyncio
async def test_handle_file_end_checksum_error():
    """Test handling FILE_END with checksum error."""
    mesh = MockMesh()
    ft = FileTransfer(mesh)

    src_id = 0x5678
    transfer_id = 42
    file_data = b'Hello'

    # Create complete receive context
    ctx = ft._start_receive(src_id, transfer_id, 1, len(file_data), "test.txt")
    ctx.add_chunk(0, file_data)

    # Build FILE_END with WRONG checksum
    import struct
    wrong_checksum = 0xDEADBEEF
    payload = bytearray(7)
    payload[0] = FILE_END
    struct.pack_into('>HI', payload, 1, transfer_id, wrong_checksum)

    ft.handle_file_packet(src_id, bytes(payload))

    # Allow created tasks to run
    await asyncio.sleep(0)

    # Transfer should be cleaned up after checksum error
    assert transfer_id not in ft._pending_recv


def test_prune_stale():
    """Test pruning stale transfers."""
    mesh = MockMesh()
    ft = FileTransfer(mesh)

    # Create old receive context
    ctx = ft._start_receive(0x5678, 42, 1, 100, "test.txt")

    # Simulate old timestamp
    ctx.started_ms = ticks_ms() - ft.TRANSFER_TIMEOUT - 1000

    # Prune
    count = ft.prune_stale()

    assert count == 1
    assert 42 not in ft._pending_recv


def test_transfer_id_wrapping():
    """Test transfer ID counter wrapping."""
    mesh = MockMesh()
    ft = FileTransfer(mesh)

    # Set counter near max
    ft._next_transfer_id = 0xFFFF

    # Get next ID
    id1 = ft._get_next_transfer_id()
    assert id1 == 0xFFFF

    # Should wrap to 0
    id2 = ft._get_next_transfer_id()
    assert id2 == 0


def test_handle_invalid_packets():
    """Test handling various invalid packets."""
    mesh = MockMesh()
    ft = FileTransfer(mesh)

    # Empty payload
    ft.handle_file_packet(0x1234, b'')

    # FILE_START too short
    ft.handle_file_packet(0x1234, bytes([FILE_START, 0, 0]))

    # FILE_CHUNK too short
    ft.handle_file_packet(0x1234, bytes([FILE_CHUNK, 0]))

    # FILE_END too short
    ft.handle_file_packet(0x1234, bytes([FILE_END, 0, 0]))

    # FILE_ACK too short
    ft.handle_file_packet(0x1234, bytes([FILE_ACK, 0]))

    # Should not crash


def test_chunk_from_unknown_transfer():
    """Test handling chunk for unknown transfer ID."""
    mesh = MockMesh()
    ft = FileTransfer(mesh)

    # FILE_CHUNK for non-existent transfer
    import struct
    payload = bytearray(5 + 10)
    payload[0] = FILE_CHUNK
    struct.pack_into('>HH', payload, 1, 999, 0)  # Unknown transfer_id=999
    payload[5:] = b'some data!'

    # Should not crash
    ft.handle_file_packet(0x1234, bytes(payload))


def test_chunk_from_wrong_source():
    """Test handling chunk from wrong source node."""
    mesh = MockMesh()
    ft = FileTransfer(mesh)

    transfer_id = 42
    correct_src = 0x5678
    wrong_src = 0x9ABC

    # Create context for correct source
    ctx = ft._start_receive(correct_src, transfer_id, 1, 10, "test.txt")

    # Send chunk from wrong source
    import struct
    payload = bytearray(5 + 5)
    payload[0] = FILE_CHUNK
    struct.pack_into('>HH', payload, 1, transfer_id, 0)
    payload[5:] = b'chunk'

    ft.handle_file_packet(wrong_src, bytes(payload))

    # Chunk should not be added
    assert ctx.chunks[0] is None


# =============================================================================
# CRC32 TESTS
# =============================================================================

def test_crc32():
    """Test CRC32 calculation."""
    # Known CRC32 values (using standard polynomial)
    assert (crc32(b'') & 0xFFFFFFFF) == 0

    # Test with simple data
    data = b'Hello, World!'
    checksum = crc32(data)
    assert isinstance(checksum, int)
    assert 0 <= checksum <= 0xFFFFFFFF

    # Same data should produce same checksum
    assert crc32(data) == crc32(data)

    # Different data should produce different checksum (usually)
    assert crc32(b'Hello') != crc32(b'World')


# =============================================================================
# INTEGRATION TESTS
# =============================================================================

@pytest.mark.asyncio
async def test_full_transfer_cycle():
    """Deliver packets while the sender is awaiting the receiver's ACK."""
    from umesh.packet import PacketCodec
    sender_mesh = MockMesh(node_id=0x1111)
    receiver_mesh = MockMesh(node_id=0x2222)
    sender_ft = FileTransfer(sender_mesh)
    receiver_ft = FileTransfer(receiver_mesh)
    codec = PacketCodec()

    async def deliver_to_receiver(packet, dest_mac=None):
        sender_mesh.sent_packets.append(packet)
        parsed = codec.decode(packet)
        receiver_ft.handle_file_packet(0x1111, parsed['payload'])

    async def deliver_ack(packet, dest_mac=None):
        receiver_mesh.sent_packets.append(packet)
        parsed = codec.decode(packet)
        sender_ft.handle_file_packet(0x2222, parsed['payload'])

    sender_mesh.send_raw = deliver_to_receiver
    receiver_mesh.send_raw = deliver_ack
    received = []
    receiver_ft.on_file_received(lambda src, name, data: received.append((src, name, data)))
    payload = b'A' * 500
    assert await sender_ft.send_file(0x2222, 'test.bin', payload)
    assert received == [(0x1111, 'test.bin', payload)]


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
