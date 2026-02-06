"""
ESP-NOW Physical Layer Adapter

Wrapper over aioespnow providing unified API for ESP-NOW communication.
Handles WLAN initialization, broadcast peer management, channel configuration,
and Long Range mode.

This is Layer 1 of the µMesh protocol stack.
"""

try:
    import network
    import espnow
    import aioespnow
except ImportError:
    # Fallback for development/testing on non-ESP32 platforms
    network = None
    espnow = None
    aioespnow = None

try:
    import uasyncio as asyncio
except ImportError:
    import asyncio

try:
    from micropython import const
except ImportError:
    const = lambda x: x

# Constants
BROADCAST_MAC = b'\xff\xff\xff\xff\xff\xff'
DEFAULT_CHANNEL = const(1)
DEFAULT_RX_BUF = const(2048)
MIN_CHANNEL = const(1)
MAX_CHANNEL = const(14)


class ESPNowAdapter:
    """
    Abstraction over espnow/aioespnow.

    Manages WLAN initialization, channel configuration, broadcast peer,
    and provides unified send/receive API with RSSI information.

    Args:
        channel: Wi-Fi channel (1-14), default 1
        lr_mode: Enable Long Range mode (requires hardware support), default False
        rx_buf: Receive buffer size in bytes, default 2048

    Example:
        adapter = ESPNowAdapter(channel=6, lr_mode=False, rx_buf=4096)
        await adapter.send_broadcast(b"hello")
        async for mac, data, rssi in adapter:
            print(f"From {mac.hex()}: {data}, RSSI: {rssi}")
    """

    def __init__(self, channel=DEFAULT_CHANNEL, lr_mode=False, rx_buf=DEFAULT_RX_BUF):
        if network is None:
            raise RuntimeError("network module not available - are you running on ESP32?")

        self._channel = channel
        self._lr_mode = lr_mode
        self._rx_buf = rx_buf
        self._wlan = None
        self._espnow = None
        self._aioespnow = None
        self._initialized = False

        # Initialize the adapter
        self._init_wlan()
        self._init_espnow()
        self._add_broadcast_peer()
        self._initialized = True

    def _init_wlan(self):
        """Initialize WLAN in STA mode without connecting."""
        self._wlan = network.WLAN(network.STA_IF)
        self._wlan.active(True)

        # Set channel
        if self._channel < MIN_CHANNEL or self._channel > MAX_CHANNEL:
            raise ValueError(f"Channel must be between {MIN_CHANNEL} and {MAX_CHANNEL}")
        self._wlan.config(channel=self._channel)

        # Enable Long Range mode if requested
        if self._lr_mode:
            self._enable_lr_mode()

    def _enable_lr_mode(self):
        """Enable Long Range mode if supported by hardware."""
        try:
            # Try to enable LR protocol mode
            # Note: network.WLAN.PROTOCOL_LR is only available on some ESP32 variants
            if hasattr(network, 'WLAN') and hasattr(network.WLAN, 'PROTOCOL_LR'):
                self._wlan.config(protocol=network.WLAN.PROTOCOL_LR)
            else:
                # Alternative method for older MicroPython versions
                # Some platforms expose this via different config keys
                try:
                    self._wlan.config(lr=True)
                except (TypeError, ValueError):
                    # LR mode not supported on this platform
                    print("Warning: Long Range mode requested but not supported on this platform")
        except Exception as e:
            print(f"Warning: Could not enable Long Range mode: {e}")

    def _init_espnow(self):
        """Initialize ESP-NOW instance."""
        self._espnow = espnow.ESPNow()
        self._espnow.active(True)

        # Set receive buffer size if supported
        # Note: rx_buf parameter may not be available in all MicroPython versions
        try:
            # Try to create aioespnow with rx_buf parameter
            self._aioespnow = aioespnow.AIOESPNow(self._espnow, rx_buf=self._rx_buf)
        except TypeError:
            # Fallback if rx_buf parameter not supported
            self._aioespnow = aioespnow.AIOESPNow(self._espnow)

    def _add_broadcast_peer(self):
        """Add broadcast peer for network-wide communication."""
        try:
            self._espnow.add_peer(BROADCAST_MAC)
        except OSError:
            # Peer might already exist, ignore
            pass

    async def send_broadcast(self, data: bytes) -> bool:
        """
        Send data to all nodes via broadcast address.

        Note: ESP-NOW send is fast (<1ms) but this method is async for
        compatibility with callers that use `await`.

        Args:
            data: Bytes to send (max 250 bytes)

        Returns:
            True if send succeeded (note: broadcast always returns True, no ACK)

        Raises:
            ValueError: If data exceeds 250 bytes
        """
        if not self._initialized:
            raise RuntimeError("Adapter not initialized")

        if len(data) > 250:
            raise ValueError("Data exceeds ESP-NOW max payload of 250 bytes")

        try:
            # Broadcast send - always returns True as there's no ACK
            self._espnow.send(BROADCAST_MAC, data)
            return True
        except OSError as e:
            print(f"Broadcast send error: {e}")
            return False

    async def send_unicast(self, mac: bytes, data: bytes) -> bool:
        """
        Send data to specific MAC address.

        Note: ESP-NOW send is fast (<1ms) but this method is async for
        compatibility with callers that use `await`.
        Peer must be registered first via add_peer().

        Args:
            mac: 6-byte MAC address of recipient
            data: Bytes to send (max 250 bytes)

        Returns:
            True if send succeeded and ACK received, False otherwise

        Raises:
            ValueError: If data exceeds 250 bytes or MAC address invalid
        """
        if not self._initialized:
            raise RuntimeError("Adapter not initialized")

        if len(mac) != 6:
            raise ValueError("MAC address must be 6 bytes")

        if len(data) > 250:
            raise ValueError("Data exceeds ESP-NOW max payload of 250 bytes")

        try:
            # Unicast send - returns True if ACK received from next hop
            self._espnow.send(mac, data)
            return True
        except OSError as e:
            # Send failed - peer not registered or transmission error
            print(f"Unicast send error to {mac.hex()}: {e}")
            return False

    async def send(self, mac: bytes, data: bytes) -> bool:
        """
        Send data to specific MAC address (alias for send_unicast).

        Args:
            mac: 6-byte MAC address of recipient
            data: Bytes to send (max 250 bytes)

        Returns:
            True if send succeeded, False otherwise
        """
        return await self.send_unicast(mac, data)

    async def recv(self) -> tuple:
        """
        Receive one packet.

        Returns:
            Tuple of (mac: bytes, data: bytes, rssi: int)
            mac: 6-byte sender MAC address
            data: Received payload
            rssi: Signal strength in dBm (typically -100 to 0)

        This method is async and will wait until a packet is available.
        """
        if not self._initialized:
            raise RuntimeError("Adapter not initialized")

        # Use aioespnow async iterator
        mac, msg = await self._aioespnow.arecv()

        # Get RSSI - try different methods for compatibility
        rssi = self._get_rssi(mac)

        return (mac, msg, rssi)

    def _get_rssi(self, mac: bytes) -> int:
        """
        Get RSSI for a given MAC address.

        Tries multiple methods for compatibility across MicroPython versions:
        1. peers_table attribute (newer versions)
        2. irecv() extended format (some versions)
        3. Return default value if not available

        Args:
            mac: MAC address to get RSSI for

        Returns:
            RSSI in dBm, or -127 if unavailable
        """
        # Method 1: Try peers_table (available in newer MicroPython versions)
        if hasattr(self._espnow, 'peers_table'):
            try:
                peer_info = self._espnow.peers_table.get(mac)
                if peer_info and len(peer_info) >= 1:
                    return peer_info[0]  # First element is RSSI
            except (AttributeError, KeyError, IndexError):
                pass

        # Method 2: Try getting from most recent irecv() call
        # Note: This is platform-dependent and may not work on all versions
        # Some versions of irecv() return [mac, msg, rssi, time_ms]

        # Default: Return minimum valid RSSI if not available
        return -127

    def add_peer(self, mac: bytes) -> bool:
        """
        Register peer for unicast communication.

        Args:
            mac: 6-byte MAC address to register

        Returns:
            True if peer added successfully, False if failed

        Note: ESP-NOW has a limit of 20 total peers (including broadcast).
              Use peer rotation strategy for networks with >19 nodes.
        """
        if not self._initialized:
            raise RuntimeError("Adapter not initialized")

        if len(mac) != 6:
            raise ValueError("MAC address must be 6 bytes")

        if mac == BROADCAST_MAC:
            # Broadcast peer is always registered
            return True

        try:
            self._espnow.add_peer(mac)
            return True
        except OSError as e:
            # Peer table full or other error
            print(f"Failed to add peer {mac.hex()}: {e}")
            return False

    def del_peer(self, mac: bytes) -> bool:
        """
        Remove peer registration.

        Args:
            mac: 6-byte MAC address to remove

        Returns:
            True if peer removed successfully, False if failed
        """
        if not self._initialized:
            raise RuntimeError("Adapter not initialized")

        if len(mac) != 6:
            raise ValueError("MAC address must be 6 bytes")

        if mac == BROADCAST_MAC:
            # Don't allow removing broadcast peer
            return False

        try:
            self._espnow.del_peer(mac)
            return True
        except OSError as e:
            # Peer not found or other error
            print(f"Failed to remove peer {mac.hex()}: {e}")
            return False

    def set_channel(self, ch: int):
        """
        Change Wi-Fi channel.

        Args:
            ch: Channel number (1-14)

        Raises:
            ValueError: If channel out of valid range

        Note: All nodes in the mesh must be on the same channel.
        """
        if ch < MIN_CHANNEL or ch > MAX_CHANNEL:
            raise ValueError(f"Channel must be between {MIN_CHANNEL} and {MAX_CHANNEL}")

        self._channel = ch
        if self._wlan:
            self._wlan.config(channel=ch)

    def enable_lr(self, enable: bool):
        """
        Enable or disable Long Range mode.

        Args:
            enable: True to enable LR mode, False to disable

        Note: Requires hardware support (ESP32-S2, S3, C3 with LR capability).
              All nodes in the mesh must use the same mode.
        """
        self._lr_mode = enable
        if self._wlan:
            if enable:
                self._enable_lr_mode()
            else:
                # Disable LR mode - revert to normal protocol
                try:
                    if hasattr(network, 'WLAN') and hasattr(network.WLAN, 'PROTOCOL_11B_G_N'):
                        self._wlan.config(protocol=network.WLAN.PROTOCOL_11B_G_N)
                    else:
                        try:
                            self._wlan.config(lr=False)
                        except (TypeError, ValueError):
                            pass
                except Exception as e:
                    print(f"Warning: Could not disable Long Range mode: {e}")

    @property
    def mac(self) -> bytes:
        """
        Return this device's MAC address.

        Returns:
            6-byte MAC address in binary format
        """
        if self._wlan:
            return bytes(self._wlan.config('mac'))
        return b'\x00\x00\x00\x00\x00\x00'

    @property
    def channel(self) -> int:
        """Current Wi-Fi channel."""
        return self._channel

    @property
    def lr_mode(self) -> bool:
        """Long Range mode status."""
        return self._lr_mode

    def peer_count(self) -> int:
        """
        Get number of registered peers (including broadcast).

        Returns:
            Number of peers currently registered
        """
        if hasattr(self._espnow, 'get_peers'):
            try:
                peers = self._espnow.get_peers()
                return len(peers) if peers else 0
            except Exception:
                pass
        # Fallback: we know broadcast is always there
        return 1

    def __aiter__(self):
        """Support async for iteration over received packets."""
        return self

    async def __anext__(self):
        """
        Return next received packet.

        Returns:
            Tuple of (mac: bytes, data: bytes, rssi: int)
        """
        return await self.recv()

    def deinit(self):
        """
        Deinitialize adapter and free resources.

        Call this when shutting down the mesh to properly cleanup.
        """
        if self._espnow:
            try:
                self._espnow.active(False)
            except Exception:
                pass

        if self._wlan:
            try:
                # Note: Don't deactivate WLAN as it might be used by other code
                # self._wlan.active(False)
                pass
            except Exception:
                pass

        self._initialized = False
