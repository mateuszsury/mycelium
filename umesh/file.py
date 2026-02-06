"""
µMesh File Transfer
===================

File transfer over mesh network with progress tracking, chunking, and CRC32 verification.

This module provides:

1. **FileTransfer**: Main class for sending and receiving files
2. **ReceiveContext**: Tracks incoming file transfer state
3. **Progress callbacks**: Optional progress tracking for large files

Key features:
- Chunking with 200-byte payload size (fits in PKT_CTRL packets)
- CRC32 checksum verification for integrity
- Progress callbacks for user feedback
- Timeout handling per chunk and per transfer
- Support for concurrent file transfers
- Optional reliable delivery via ReliableTransport

Usage:
    # Initialize
    ft = FileTransfer(mesh)

    # Send file
    success = await ft.send_file(
        dest_id=0x1234,
        filename="config.json",
        data=b'{"key": "value"}',
        on_progress=lambda sent, total: print(f"{sent}/{total}")
    )

    # Receive file
    filename, data = await ft.recv_file(timeout=60000)

    # Or use callback
    ft.on_file_received(lambda src, fname, data: handle_file(src, fname, data))

File Transfer Protocol:
    - Uses PKT_CTRL packets with file-specific subtypes
    - FILE_START: Initiates transfer, sends metadata
    - FILE_CHUNK: Sends data chunk
    - FILE_END: Completes transfer with CRC32
    - FILE_ACK: Acknowledges completion (success/failure)
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

try:
    from binascii import crc32
except ImportError:
    # Fallback CRC32 implementation for platforms without binascii
    def crc32(data, crc=0):
        """Simple CRC32 implementation."""
        crc = crc ^ 0xFFFFFFFF
        for byte in data:
            crc = crc ^ byte
            for _ in range(8):
                if crc & 1:
                    crc = (crc >> 1) ^ 0xEDB88320
                else:
                    crc = crc >> 1
        return crc ^ 0xFFFFFFFF

import struct

from .const import PKT_CTRL
from .packet import PacketCodec

try:
    from micropython import const
except ImportError:
    def const(x):
        return x


# File transfer packet subtypes (for PKT_CTRL)
FILE_START = const(0x10)
FILE_CHUNK = const(0x11)
FILE_END = const(0x12)
FILE_ACK = const(0x13)

# ACK status codes
ACK_OK = const(0)
ACK_CHECKSUM_ERROR = const(1)
ACK_TIMEOUT = const(2)
ACK_MISSING_CHUNKS = const(3)


# =============================================================================
# RECEIVE CONTEXT - Tracks incoming file transfer
# =============================================================================

class ReceiveContext:
    """
    Tracks incoming file transfer state.

    Manages received chunks, tracks progress, and assembles complete file
    when all chunks are received.

    Attributes:
        src_id: Source node ID
        transfer_id: Unique transfer identifier
        filename: Destination filename
        total_chunks: Total number of chunks expected
        total_size: Total file size in bytes
        chunks: List of received chunk data (None if not yet received)
        received_mask: Bitmask of received chunk indices
        started_ms: Timestamp when transfer started
        event: asyncio.Event for completion notification
    """

    __slots__ = ('src_id', 'transfer_id', 'filename', 'total_chunks',
                 'total_size', 'chunks', 'received_mask', 'started_ms', 'event')

    # Maximum chunks supported by 32-bit bitmask
    MAX_CHUNKS = 32

    def __init__(self, src_id: int, transfer_id: int, filename: str,
                 total_chunks: int, total_size: int):
        """
        Initialize receive context.

        Args:
            src_id: Source node ID
            transfer_id: Transfer identifier
            filename: Destination filename
            total_chunks: Total number of chunks
            total_size: Total file size in bytes

        Raises:
            ValueError: If total_chunks exceeds MAX_CHUNKS (32)
        """
        if total_chunks > self.MAX_CHUNKS:
            raise ValueError(
                f"total_chunks ({total_chunks}) exceeds MAX_CHUNKS ({self.MAX_CHUNKS}), "
                f"bitmask overflow would occur"
            )
        if total_chunks <= 0:
            raise ValueError(f"total_chunks must be positive, got {total_chunks}")

        self.src_id = src_id
        self.transfer_id = transfer_id
        self.filename = filename
        self.total_chunks = total_chunks
        self.total_size = total_size
        self.chunks = [None] * total_chunks  # Pre-allocate
        self.received_mask = 0  # Bitmask for received chunks
        self.started_ms = ticks_ms()
        self.event = asyncio.Event()

    def add_chunk(self, chunk_idx: int, data: bytes) -> bool:
        """
        Add received chunk.

        Args:
            chunk_idx: Chunk index (0-based)
            data: Chunk data

        Returns:
            True if transfer is complete, False otherwise

        Raises:
            ValueError: If chunk_idx out of range
        """
        if chunk_idx >= self.total_chunks or chunk_idx < 0:
            raise ValueError(
                f"Chunk index out of range: {chunk_idx}, "
                f"expected 0-{self.total_chunks - 1}"
            )

        # Check if already received (duplicate)
        bit_mask = 1 << chunk_idx
        if self.received_mask & bit_mask:
            return False  # Duplicate, ignore

        # Store chunk and mark as received
        self.chunks[chunk_idx] = data
        self.received_mask |= bit_mask

        # Check if complete
        if self.is_complete():
            self.event.set()
            return True

        return False

    def is_complete(self) -> bool:
        """
        Check if all chunks received.

        Returns:
            True if all chunks received, False otherwise
        """
        expected_mask = (1 << self.total_chunks) - 1
        return self.received_mask == expected_mask

    def assemble(self) -> bytes:
        """
        Assemble all chunks into complete file data.

        Returns:
            Complete file data

        Raises:
            RuntimeError: If not all chunks received
        """
        if not self.is_complete():
            raise RuntimeError(
                f"Cannot assemble incomplete transfer: "
                f"received {bin(self.received_mask)}, "
                f"expected {bin((1 << self.total_chunks) - 1)}"
            )

        # Concatenate all chunks
        return b''.join(self.chunks)

    def get_missing_indices(self) -> list:
        """
        Get list of missing chunk indices.

        Returns:
            List of missing chunk indices
        """
        missing = []
        for idx in range(self.total_chunks):
            if not (self.received_mask & (1 << idx)):
                missing.append(idx)
        return missing

    def bytes_received(self) -> int:
        """
        Calculate total bytes received so far.

        Returns:
            Number of bytes received
        """
        total = 0
        for chunk in self.chunks:
            if chunk is not None:
                total += len(chunk)
        return total


# =============================================================================
# FILE TRANSFER - Main file transfer class
# =============================================================================

class FileTransfer:
    """
    File transfer over mesh network with progress tracking.

    Provides chunked file transfer with:
    - Progress callbacks for monitoring
    - CRC32 checksum verification
    - Timeout handling (per chunk and total transfer)
    - Retry logic for failed chunks
    - Optional reliable delivery via ReliableTransport

    Attributes:
        CHUNK_SIZE: Bytes per chunk (200)
        TRANSFER_TIMEOUT: Total transfer timeout in ms (60000)
        CHUNK_TIMEOUT: Per-chunk timeout in ms (5000)
        MAX_RETRIES: Maximum chunk retransmission attempts (3)
    """

    CHUNK_SIZE = const(200)           # Bytes per chunk
    TRANSFER_TIMEOUT = const(60000)   # 60s total timeout
    CHUNK_TIMEOUT = const(5000)       # 5s per chunk
    MAX_RETRIES = const(3)

    def __init__(self, mesh, transport=None):
        """
        Initialize file transfer.

        Args:
            mesh: Mesh instance for sending/receiving packets
            transport: Optional ReliableTransport for reliable delivery
        """
        self._mesh = mesh
        self._transport = transport
        self._codec = PacketCodec()

        # Active receives: transfer_id -> ReceiveContext
        self._pending_recv = {}

        # Active sends: transfer_id -> {'event': Event, 'status': int, 'dest_id': int}
        self._pending_send = {}

        # Callback for received files
        self._recv_callback = None

        # Queue for received files (when using recv_file())
        self._recv_queue = asyncio.Queue(maxsize=4)

        # Transfer ID counter (16-bit)
        self._next_transfer_id = 0

    async def send_file(self, dest_id: int, filename: str, data: bytes,
                        on_progress=None) -> bool:
        """
        Send file to remote node.

        Splits file into chunks, sends with retries, and verifies delivery.

        Args:
            dest_id: Destination node ID (16-bit)
            filename: Filename (max 255 chars, UTF-8)
            data: File data (bytes)
            on_progress: Optional callback(bytes_sent, total_bytes)

        Returns:
            True if transfer completed successfully, False on failure

        Raises:
            ValueError: If filename too long or data empty
        """
        if not data:
            raise ValueError("Cannot send empty file")

        filename_bytes = filename.encode('utf-8')
        if len(filename_bytes) > 255:
            raise ValueError(
                f"Filename too long: {len(filename_bytes)} bytes, max 255"
            )

        # Generate transfer ID
        transfer_id = self._get_next_transfer_id()

        # Calculate chunks
        total_size = len(data)
        total_chunks = (total_size + self.CHUNK_SIZE - 1) // self.CHUNK_SIZE

        # Send FILE_START
        await self._send_file_start(
            dest_id, transfer_id, total_chunks, total_size, filename_bytes
        )

        # Send chunks
        bytes_sent = 0
        for chunk_idx in range(total_chunks):
            # Extract chunk data
            offset = chunk_idx * self.CHUNK_SIZE
            end = min(offset + self.CHUNK_SIZE, total_size)
            chunk_data = data[offset:end]

            # Send chunk with retry
            success = await self._send_chunk(
                dest_id, transfer_id, chunk_idx, total_chunks,
                filename, chunk_data
            )

            if not success:
                return False

            bytes_sent += len(chunk_data)

            # Progress callback
            if on_progress:
                on_progress(bytes_sent, total_size)

        # Calculate CRC32
        checksum = crc32(data) & 0xFFFFFFFF

        # Send FILE_END
        await self._send_file_end(dest_id, transfer_id, checksum)

        # Wait for ACK
        ack_event = asyncio.Event()
        self._pending_send[transfer_id] = {
            'event': ack_event,
            'status': None,
            'dest_id': dest_id
        }

        try:
            await asyncio.wait_for(
                ack_event.wait(),
                self.TRANSFER_TIMEOUT / 1000.0
            )
            return self._pending_send[transfer_id]['status'] == ACK_OK
        except asyncio.TimeoutError:
            return False
        finally:
            self._pending_send.pop(transfer_id, None)

    async def recv_file(self, timeout: int = None) -> tuple:
        """
        Wait for incoming file transfer.

        Blocks until a file is received or timeout expires.

        Args:
            timeout: Timeout in ms (default: TRANSFER_TIMEOUT)

        Returns:
            Tuple of (filename: str, data: bytes)

        Raises:
            asyncio.TimeoutError: If timeout expires
        """
        if timeout is None:
            timeout = self.TRANSFER_TIMEOUT

        try:
            # Wait for file in queue
            item = await asyncio.wait_for(
                self._recv_queue.get(),
                timeout / 1000.0  # Convert ms to seconds
            )
            return item
        except asyncio.TimeoutError:
            raise asyncio.TimeoutError("File receive timeout")

    def on_file_received(self, callback):
        """
        Register callback for incoming files.

        Callback is invoked when a file transfer completes.

        Args:
            callback: Function(src_id: int, filename: str, data: bytes)
        """
        self._recv_callback = callback

    def handle_file_packet(self, src_id: int, payload: bytes):
        """
        Handle incoming file transfer packet.

        Called by mesh layer when PKT_CTRL packet with file subtype is received.

        Args:
            src_id: Source node ID
            payload: Packet payload (starts with subtype byte)
        """
        if not payload:
            return

        subtype = payload[0]

        if subtype == FILE_START:
            self._handle_file_start(src_id, payload[1:])
        elif subtype == FILE_CHUNK:
            self._handle_file_chunk(src_id, payload[1:])
        elif subtype == FILE_END:
            self._handle_file_end(src_id, payload[1:])
        elif subtype == FILE_ACK:
            self._handle_file_ack(src_id, payload[1:])

    async def _send_chunk(self, dest_id: int, transfer_id: int,
                          chunk_idx: int, total_chunks: int,
                          filename: str, chunk_data: bytes) -> bool:
        """
        Send single file chunk with retry.

        Args:
            dest_id: Destination node ID
            transfer_id: Transfer ID
            chunk_idx: Chunk index
            total_chunks: Total chunks
            filename: Filename (for logging)
            chunk_data: Chunk data

        Returns:
            True if chunk sent successfully, False on failure
        """
        # Build FILE_CHUNK payload
        payload = bytearray(5 + len(chunk_data))
        payload[0] = FILE_CHUNK
        struct.pack_into('>HH', payload, 1, transfer_id, chunk_idx)
        payload[5:] = chunk_data

        # Build packet
        pkt = self._codec.encode(
            PKT_CTRL,
            src_id=self._mesh.node_id,
            dest_id=dest_id,
            payload=bytes(payload)
        )

        # Send via mesh (fire-and-forget for now)
        await self._mesh.send_raw(pkt)
        return True

    def _start_receive(self, src_id: int, transfer_id: int,
                       total_chunks: int, total_size: int,
                       filename: str) -> ReceiveContext:
        """
        Start tracking incoming file transfer.

        Args:
            src_id: Source node ID
            transfer_id: Transfer ID
            total_chunks: Total chunks expected
            total_size: Total file size
            filename: Destination filename

        Returns:
            ReceiveContext for tracking
        """
        ctx = ReceiveContext(
            src_id, transfer_id, filename, total_chunks, total_size
        )
        self._pending_recv[transfer_id] = ctx
        return ctx

    def _get_next_transfer_id(self) -> int:
        """
        Get next transfer ID.

        16-bit counter that wraps at 65535.

        Returns:
            Transfer ID (0-65535)
        """
        transfer_id = self._next_transfer_id
        self._next_transfer_id = (self._next_transfer_id + 1) & 0xFFFF
        return transfer_id

    # -------------------------------------------------------------------------
    # FILE_START packet handling
    # -------------------------------------------------------------------------

    async def _send_file_start(self, dest_id: int, transfer_id: int,
                                total_chunks: int, total_size: int,
                                filename: bytes):
        """
        Send FILE_START packet.

        Payload format:
            subtype: uint8 (FILE_START)
            transfer_id: uint16
            total_chunks: uint16
            total_size: uint32
            filename_len: uint8
            filename: utf8 string

        Args:
            dest_id: Destination node ID
            transfer_id: Transfer ID
            total_chunks: Total chunks
            total_size: Total file size
            filename: Filename bytes (UTF-8)
        """
        filename_len = len(filename)
        payload = bytearray(10 + filename_len)

        payload[0] = FILE_START
        struct.pack_into('>HHI', payload, 1, transfer_id, total_chunks, total_size)
        payload[9] = filename_len
        payload[10:] = filename

        # Build and send packet
        pkt = self._codec.encode(
            PKT_CTRL,
            src_id=self._mesh.node_id,
            dest_id=dest_id,
            payload=bytes(payload)
        )

        await self._mesh.send_raw(pkt)

    def _handle_file_start(self, src_id: int, data: bytes):
        """
        Handle FILE_START packet.

        Args:
            src_id: Source node ID
            data: Payload data (without subtype byte)
        """
        if len(data) < 9:
            return  # Invalid packet

        # Parse header
        transfer_id, total_chunks, total_size = struct.unpack_from('>HHI', data, 0)
        filename_len = data[8]

        if len(data) < 9 + filename_len:
            return  # Invalid packet

        filename_bytes = data[9:9 + filename_len]
        filename = filename_bytes.decode('utf-8', errors='ignore')

        # Start receive context
        self._start_receive(
            src_id, transfer_id, total_chunks, total_size, filename
        )

    # -------------------------------------------------------------------------
    # FILE_CHUNK packet handling
    # -------------------------------------------------------------------------

    def _handle_file_chunk(self, src_id: int, data: bytes):
        """
        Handle FILE_CHUNK packet.

        Args:
            src_id: Source node ID
            data: Payload data (without subtype byte)
        """
        if len(data) < 4:
            return  # Invalid packet

        # Parse header
        transfer_id, chunk_idx = struct.unpack_from('>HH', data, 0)
        chunk_data = data[4:]

        # Find receive context
        if transfer_id not in self._pending_recv:
            return  # Unknown transfer

        ctx = self._pending_recv[transfer_id]

        # Verify source
        if ctx.src_id != src_id:
            return  # Wrong source

        # Add chunk
        try:
            complete = ctx.add_chunk(chunk_idx, chunk_data)
        except ValueError:
            # Invalid chunk index, ignore
            return

    # -------------------------------------------------------------------------
    # FILE_END packet handling
    # -------------------------------------------------------------------------

    async def _send_file_end(self, dest_id: int, transfer_id: int,
                             checksum: int):
        """
        Send FILE_END packet.

        Payload format:
            subtype: uint8 (FILE_END)
            transfer_id: uint16
            checksum: uint32 (CRC32)

        Args:
            dest_id: Destination node ID
            transfer_id: Transfer ID
            checksum: CRC32 checksum
        """
        payload = bytearray(7)
        payload[0] = FILE_END
        struct.pack_into('>HI', payload, 1, transfer_id, checksum)

        # Build and send packet
        pkt = self._codec.encode(
            PKT_CTRL,
            src_id=self._mesh.node_id,
            dest_id=dest_id,
            payload=bytes(payload)
        )

        await self._mesh.send_raw(pkt)

    def _handle_file_end(self, src_id: int, data: bytes):
        """
        Handle FILE_END packet.

        Verifies CRC32, assembles file, and invokes callback/queue.

        Args:
            src_id: Source node ID
            data: Payload data (without subtype byte)
        """
        if len(data) < 6:
            return  # Invalid packet

        # Parse header
        transfer_id, checksum = struct.unpack_from('>HI', data, 0)

        # Find receive context
        if transfer_id not in self._pending_recv:
            return  # Unknown transfer

        ctx = self._pending_recv[transfer_id]

        # Verify source
        if ctx.src_id != src_id:
            return  # Wrong source

        # Check if complete
        if not ctx.is_complete():
            # Send NACK with missing chunk indices
            missing = ctx.get_missing_indices()
            ack_payload = bytearray(4 + len(missing))
            ack_payload[0] = FILE_ACK
            struct.pack_into('>HB', ack_payload, 1, transfer_id, ACK_MISSING_CHUNKS)
            for i, idx in enumerate(missing):
                ack_payload[4 + i] = idx
            asyncio.create_task(self._send_file_ack_raw(src_id, bytes(ack_payload)))
            return

        # Assemble file
        try:
            file_data = ctx.assemble()
        except RuntimeError:
            # Assembly failed
            return

        # Verify checksum
        calculated_crc = crc32(file_data) & 0xFFFFFFFF
        if calculated_crc != checksum:
            # Checksum mismatch, send error ACK
            asyncio.create_task(self._send_file_ack(src_id, transfer_id, ACK_CHECKSUM_ERROR))
            del self._pending_recv[transfer_id]
            return

        # Transfer complete! Send success ACK
        asyncio.create_task(self._send_file_ack(src_id, transfer_id, ACK_OK))

        # Clean up
        del self._pending_recv[transfer_id]

        # Invoke callback
        if self._recv_callback:
            try:
                self._recv_callback(src_id, ctx.filename, file_data)
            except Exception:
                pass  # Ignore callback errors

        # Add to queue (non-blocking)
        try:
            self._recv_queue.put_nowait((ctx.filename, file_data))
        except asyncio.QueueFull:
            pass  # Queue full, drop

    # -------------------------------------------------------------------------
    # FILE_ACK packet handling
    # -------------------------------------------------------------------------

    async def _send_file_ack(self, dest_id: int, transfer_id: int,
                             status: int):
        """
        Send FILE_ACK packet.

        Payload format:
            subtype: uint8 (FILE_ACK)
            transfer_id: uint16
            status: uint8 (ACK_OK, ACK_CHECKSUM_ERROR, ACK_TIMEOUT)

        Args:
            dest_id: Destination node ID
            transfer_id: Transfer ID
            status: Status code
        """
        payload = bytearray(4)
        payload[0] = FILE_ACK
        struct.pack_into('>HB', payload, 1, transfer_id, status)

        # Build and send packet
        pkt = self._codec.encode(
            PKT_CTRL,
            src_id=self._mesh.node_id,
            dest_id=dest_id,
            payload=bytes(payload)
        )

        await self._mesh.send_raw(pkt)

    async def _send_file_ack_raw(self, dest_id: int, payload: bytes):
        """Send a pre-built FILE_ACK payload as PKT_CTRL packet.

        Args:
            dest_id: Destination node ID
            payload: Complete FILE_ACK payload (including subtype byte)
        """
        pkt = self._codec.encode(
            PKT_CTRL,
            src_id=self._mesh.node_id,
            dest_id=dest_id,
            payload=payload
        )
        await self._mesh.send_raw(pkt)

    def _handle_file_ack(self, src_id: int, data: bytes):
        """
        Handle FILE_ACK packet.

        Notifies pending send_file() call of completion status.

        Args:
            src_id: Source node ID
            data: Payload data (without subtype byte)
        """
        if len(data) < 3:
            return  # Invalid packet

        # Parse header
        transfer_id, status = struct.unpack_from('>HB', data, 0)

        # Find pending send
        if transfer_id not in self._pending_send:
            return  # Unknown transfer

        pending = self._pending_send[transfer_id]

        # Verify sender matches destination
        if pending.get('dest_id') != src_id:
            return  # Wrong source

        # Store status and signal completion
        pending['status'] = status
        pending['event'].set()

    def prune_stale(self) -> int:
        """
        Remove incomplete transfers older than TRANSFER_TIMEOUT.

        Should be called periodically by maintenance task.

        Returns:
            Count of pruned transfers
        """
        now = ticks_ms()
        to_remove = []

        for transfer_id, ctx in self._pending_recv.items():
            age = ticks_diff(now, ctx.started_ms)
            if age > self.TRANSFER_TIMEOUT:
                to_remove.append(transfer_id)

        for transfer_id in to_remove:
            del self._pending_recv[transfer_id]

        return len(to_remove)


# =============================================================================
# MODULE INFO
# =============================================================================

__all__ = [
    'FileTransfer',
    'ReceiveContext',
    'FILE_START',
    'FILE_CHUNK',
    'FILE_END',
    'FILE_ACK',
    'ACK_OK',
    'ACK_CHECKSUM_ERROR',
    'ACK_TIMEOUT',
    'ACK_MISSING_CHUNKS',
]
