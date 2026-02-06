"""
Tests for Mesh class (Simple API)

Note: These tests use mocks for ESP-NOW components to run on desktop Python.
Real hardware tests require actual ESP32 boards.
"""

import sys
import asyncio
from unittest.mock import Mock, MagicMock, AsyncMock, patch
import pytest

# Add parent directory to path
sys.path.insert(0, '..')

from umesh.const import PKT_DATA, PKT_HELLO, BROADCAST_ID


class MockAdapter:
    """Mock ESPNowAdapter for testing."""

    def __init__(self, channel=1, lr_mode=False, rx_buf=2048):
        self.channel = channel
        self.lr_mode = lr_mode
        self.rx_buf = rx_buf
        self.mac = b'\xAA\xBB\xCC\xDD\xEE\xFF'
        self._queue = asyncio.Queue()
        self._running = True

    async def send_broadcast(self, data):
        """Mock broadcast send."""
        return True

    async def send_unicast(self, mac, data):
        """Mock unicast send."""
        return True

    def add_peer(self, mac):
        """Mock add peer."""
        return True

    def del_peer(self, mac):
        """Mock del peer."""
        return True

    async def recv(self):
        """Mock receive."""
        return await self._queue.get()

    def inject_packet(self, mac, data, rssi=-50):
        """Inject a packet for testing."""
        try:
            self._queue.put_nowait((mac, data, rssi))
        except asyncio.QueueFull:
            pass

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._running:
            raise StopAsyncIteration
        return await self.recv()


def test_mesh_init_auto_config():
    """Test Mesh initialization with auto-configuration."""
    with patch('umesh.mesh.ESPNowAdapter', MockAdapter):
        from umesh.mesh import Mesh

        mesh = Mesh()

        # Check auto-derived node_id from mock MAC (last 2 bytes: 0xEEFF)
        assert mesh.node_id == 0xEEFF
        assert mesh.mac == b'\xAA\xBB\xCC\xDD\xEE\xFF'


def test_mesh_init_explicit_config():
    """Test Mesh initialization with explicit configuration."""
    with patch('umesh.mesh.ESPNowAdapter', MockAdapter):
        from umesh.mesh import Mesh
        from umesh.config import MeshConfig

        config = MeshConfig(node_id=0x0042, channel=6, lr_mode=True)
        mesh = Mesh(config)

        assert mesh.node_id == 0x0042
        assert mesh._config.channel == 6
        assert mesh._config.lr_mode is True


@pytest.mark.asyncio
async def test_mesh_start_stop():
    """Test mesh start and stop."""
    with patch('umesh.mesh.ESPNowAdapter', MockAdapter):
        from umesh.mesh import Mesh

        mesh = Mesh()

        # Start mesh
        await mesh.start()
        assert mesh._running is True
        assert len(mesh._tasks) == 3  # rx_loop, hello_loop, maintenance

        # Give tasks time to start
        await asyncio.sleep(0.1)

        # Stop mesh
        await mesh.stop()
        assert mesh._running is False
        assert len(mesh._tasks) == 0


@pytest.mark.asyncio
async def test_mesh_send_broadcast():
    """Test broadcast send."""
    with patch('umesh.mesh.ESPNowAdapter', MockAdapter):
        from umesh.mesh import Mesh

        mesh = Mesh()
        await mesh.start()

        # Send broadcast
        success = await mesh.send(BROADCAST_ID, b"Hello mesh!")
        assert success is True

        # Check stats
        assert mesh._stats['tx_packets'] >= 1  # At least 1 (could be more from HELLO)
        assert mesh._stats['tx_bytes'] > 0

        await mesh.stop()


@pytest.mark.asyncio
async def test_mesh_message_callback():
    """Test message callback functionality."""
    with patch('umesh.mesh.ESPNowAdapter', MockAdapter):
        from umesh.mesh import Mesh
        from umesh.packet import PacketCodec

        mesh = Mesh()

        # Track received messages
        received_messages = []

        def on_message(src_id, data):
            received_messages.append((src_id, data))

        mesh.on_message(on_message)

        await mesh.start()
        await asyncio.sleep(0.1)

        # Inject a DATA packet
        codec = PacketCodec()
        packet = codec.encode(
            pkt_type=PKT_DATA,
            src_id=0x1234,
            dest_id=mesh.node_id,
            payload=b"Test message",
            pkt_id=42
        )

        mesh._adapter.inject_packet(b'\x11\x22\x33\x44\x55\x66', packet, -50)

        # Wait for processing
        await asyncio.sleep(0.2)

        # Check callback was called
        assert len(received_messages) == 1
        assert received_messages[0][0] == 0x1234
        assert received_messages[0][1] == b"Test message"

        await mesh.stop()


@pytest.mark.asyncio
async def test_mesh_hello_handling():
    """Test HELLO packet handling and neighbor discovery."""
    with patch('umesh.mesh.ESPNowAdapter', MockAdapter):
        from umesh.mesh import Mesh
        from umesh.packet import PacketCodec

        mesh = Mesh()
        await mesh.start()
        await asyncio.sleep(0.1)

        # Inject a HELLO packet
        codec = PacketCodec()
        hello_payload = bytes([0x12, 0x34, 0])  # node_id=0x1234, hop_count=0

        packet = codec.encode(
            pkt_type=PKT_HELLO,
            src_id=0x1234,
            dest_id=BROADCAST_ID,
            payload=hello_payload,
            pkt_id=1,
            ttl=1
        )

        mesh._adapter.inject_packet(b'\x11\x22\x33\x44\x55\x66', packet, -60)

        # Wait for processing
        await asyncio.sleep(0.2)

        # Check neighbor was added
        neighbors = mesh.neighbors
        assert len(neighbors) >= 1

        # Find our neighbor
        neighbor_found = False
        for node_id, mac, rssi, lqi in neighbors:
            if node_id == 0x1234:
                neighbor_found = True
                assert mac == b'\x11\x22\x33\x44\x55\x66'
                assert rssi == -60
                break

        assert neighbor_found is True
        assert mesh._stats['hello_received'] >= 1

        await mesh.stop()


@pytest.mark.asyncio
async def test_mesh_duplicate_filtering():
    """Test duplicate packet filtering."""
    with patch('umesh.mesh.ESPNowAdapter', MockAdapter):
        from umesh.mesh import Mesh
        from umesh.packet import PacketCodec

        mesh = Mesh()

        received_count = [0]

        def on_message(src_id, data):
            received_count[0] += 1

        mesh.on_message(on_message)

        await mesh.start()
        await asyncio.sleep(0.1)

        # Send same packet twice
        codec = PacketCodec()
        packet = codec.encode(
            pkt_type=PKT_DATA,
            src_id=0x1234,
            dest_id=mesh.node_id,
            payload=b"Duplicate test",
            pkt_id=100  # Same packet ID
        )

        # Inject first time
        mesh._adapter.inject_packet(b'\x11\x22\x33\x44\x55\x66', packet, -50)
        await asyncio.sleep(0.1)

        # Inject second time (duplicate)
        mesh._adapter.inject_packet(b'\x11\x22\x33\x44\x55\x66', packet, -50)
        await asyncio.sleep(0.1)

        # Should only receive once
        assert received_count[0] == 1
        assert mesh._stats['dropped_duplicates'] >= 1

        await mesh.stop()


@pytest.mark.asyncio
async def test_mesh_async_iteration():
    """Test async iteration over mesh messages."""
    with patch('umesh.mesh.ESPNowAdapter', MockAdapter):
        from umesh.mesh import Mesh
        from umesh.packet import PacketCodec

        mesh = Mesh()
        await mesh.start()
        await asyncio.sleep(0.1)

        # Inject multiple packets
        codec = PacketCodec()

        for i in range(3):
            packet = codec.encode(
                pkt_type=PKT_DATA,
                src_id=0x1000 + i,
                dest_id=mesh.node_id,
                payload=f"Message {i}".encode(),
                pkt_id=i
            )
            mesh._adapter.inject_packet(b'\x11\x22\x33\x44\x55\x66', packet, -50)

        # Collect messages via async iteration
        received = []

        async def collect_messages():
            count = 0
            async for src_id, data in mesh:
                received.append((src_id, data))
                count += 1
                if count >= 3:
                    break

        # Run with timeout
        try:
            await asyncio.wait_for(collect_messages(), timeout=1.0)
        except asyncio.TimeoutError:
            pass

        # Check we received messages
        assert len(received) >= 3

        await mesh.stop()


def test_mesh_stats():
    """Test statistics tracking."""
    with patch('umesh.mesh.ESPNowAdapter', MockAdapter):
        from umesh.mesh import Mesh

        mesh = Mesh()

        stats = mesh.stats

        # Check all expected keys
        expected_keys = [
            'tx_packets', 'rx_packets', 'tx_bytes', 'rx_bytes',
            'dropped_duplicates', 'dropped_crc_fail',
            'hello_sent', 'hello_received'
        ]

        for key in expected_keys:
            assert key in stats
            assert isinstance(stats[key], int)


def test_mesh_neighbors_property():
    """Test neighbors property."""
    with patch('umesh.mesh.ESPNowAdapter', MockAdapter):
        from umesh.mesh import Mesh

        mesh = Mesh()

        # Initially empty
        neighbors = mesh.neighbors
        assert isinstance(neighbors, list)
        assert len(neighbors) == 0


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
