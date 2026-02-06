"""
Peer Management Module

Manages ESP-NOW peer slots with LRU eviction strategy.
ESP-NOW has a hard limit of 20 registered peers (19 usable + 1 broadcast).

This module ensures peers are registered when needed and automatically
evicts least recently used peers when the table is full, protecting
peers that are on active routing paths.

This is Layer 2 infrastructure for the µMesh protocol stack.
"""

import time

# MicroPython compatibility
try:
    from micropython import const
except ImportError:
    # Fallback for desktop Python testing
    const = lambda x: x

# Time function compatibility
try:
    # MicroPython has ticks_ms
    _ticks_ms = time.ticks_ms
except AttributeError:
    # Desktop Python fallback - use milliseconds since epoch
    _ticks_ms = lambda: int(time.time() * 1000)

# Constants
MAX_PEERS = const(19)  # 20 total - 1 (broadcast always occupies 1 slot)
BROADCAST_MAC = b'\xff\xff\xff\xff\xff\xff'  # Protected from removal


class PeerSlot:
    """
    Lightweight container for peer metadata.

    Attributes:
        last_used_ms: Timestamp of last use (from time.ticks_ms())
        on_route: Whether this peer is on an active routing path (protected from eviction)
        node_id: Optional 16-bit node ID associated with this MAC
    """
    __slots__ = ('last_used_ms', 'on_route', 'node_id')

    def __init__(self, last_used_ms, on_route=False, node_id=None):
        self.last_used_ms = last_used_ms
        self.on_route = on_route
        self.node_id = node_id


class PeerManager:
    """
    Manages ESP-NOW peer slots with LRU eviction strategy.

    ESP-NOW allows maximum 20 peers, with broadcast peer always occupying
    one slot. This leaves 19 slots for unicast peers.

    When the peer table is full and a new peer needs to be added, the manager
    evicts the least recently used peer that is NOT marked as being on an
    active routing path.

    Args:
        adapter: ESPNowAdapter instance for add_peer/del_peer calls

    Example:
        adapter = ESPNowAdapter(channel=6)
        peer_mgr = PeerManager(adapter)

        # Ensure peer is registered before sending
        if await peer_mgr.ensure_peer(peer_mac):
            await adapter.send_unicast(peer_mac, data)

        # Mark peer as on active route (protected from eviction)
        peer_mgr.mark_on_route(peer_mac, True)

        # Touch peer to update LRU timestamp
        peer_mgr.touch(peer_mac)
    """

    def __init__(self, adapter):
        """
        Initialize PeerManager with adapter reference.

        Args:
            adapter: ESPNowAdapter instance for add_peer/del_peer calls
        """
        self._adapter = adapter
        self._slots = {}  # mac (bytes) -> PeerSlot
        self._lru_order = []  # List of MACs in LRU order (oldest first)
        self._stats = {
            'evictions': 0,
            'hits': 0,
            'misses': 0
        }
        # Node ID reverse lookup: node_id -> mac
        self._node_id_map = {}

    async def ensure_peer(self, mac: bytes) -> bool:
        """
        Ensure peer is registered for unicast communication.

        If peer is already registered, updates LRU timestamp.
        If not registered and slots are available, registers the peer.
        If slots are full, evicts least recently used peer (not on active route)
        and registers the new peer.

        Args:
            mac: 6-byte MAC address of peer to ensure

        Returns:
            True if peer is now registered, False if registration failed
        """
        if len(mac) != 6:
            raise ValueError("MAC address must be 6 bytes")

        # Check if already registered
        if mac in self._slots:
            self._stats['hits'] += 1
            self.touch(mac)
            return True

        self._stats['misses'] += 1

        # Check if we have space
        if len(self._slots) < MAX_PEERS:
            # Space available, try to add directly
            if self._adapter.add_peer(mac):
                now = _ticks_ms()
                self._slots[mac] = PeerSlot(last_used_ms=now, on_route=False)
                self._lru_order.append(mac)
                return True

            # Adapter rejected (peer table full despite our count)
            # This can happen if adapter has different limit or peers were added externally
            # Fall through to eviction logic

        # No space (or adapter rejected) - need to evict
        if not self._evict_one():
            # Could not evict anyone (all protected or eviction failed)
            return False

        # Try to add peer after eviction
        if self._adapter.add_peer(mac):
            now = _ticks_ms()
            self._slots[mac] = PeerSlot(last_used_ms=now, on_route=False)
            self._lru_order.append(mac)
            return True

        return False

    def touch(self, mac: bytes):
        """
        Update last-used timestamp and move to end of LRU list.

        Call this when a peer is actively used (packet sent/received).

        Args:
            mac: 6-byte MAC address of peer to touch
        """
        if mac not in self._slots:
            return

        # Update timestamp
        self._slots[mac].last_used_ms = _ticks_ms()

        # Move to end of LRU list (most recently used)
        try:
            self._lru_order.remove(mac)
        except ValueError:
            # mac was in _slots but not in _lru_order - state inconsistency
            # Log warning and recover by adding to LRU list
            print(f"Warning: peer {mac.hex()} in slots but missing from LRU order, recovering")

        self._lru_order.append(mac)

    def mark_on_route(self, mac: bytes, on_route: bool):
        """
        Mark peer as being on active routing path (protected from eviction).

        Peers marked as on_route=True will not be evicted by the LRU strategy.
        This protects active routing paths from being disrupted.

        Args:
            mac: 6-byte MAC address of peer
            on_route: True to protect from eviction, False to allow eviction
        """
        if mac in self._slots:
            self._slots[mac].on_route = on_route

    def get_node_id(self, mac: bytes) -> int:
        """
        Get node_id associated with MAC address.

        Args:
            mac: 6-byte MAC address

        Returns:
            16-bit node ID if known, None otherwise
        """
        slot = self._slots.get(mac)
        return slot.node_id if slot else None

    def set_node_id(self, mac: bytes, node_id: int):
        """
        Associate node_id with MAC address.

        This creates a bidirectional mapping between MAC and node_id.
        If the peer is not registered, this still stores the mapping
        for future use.

        Args:
            mac: 6-byte MAC address
            node_id: 16-bit node ID (0x0000-0xFFFE, 0xFFFF reserved for broadcast)
        """
        if node_id == 0xFFFF:
            raise ValueError("Node ID 0xFFFF is reserved for broadcast")

        # Update slot if exists
        if mac in self._slots:
            old_node_id = self._slots[mac].node_id
            self._slots[mac].node_id = node_id

            # Update reverse mapping
            if old_node_id is not None and old_node_id in self._node_id_map:
                del self._node_id_map[old_node_id]
        else:
            # Create slot for unknown peer (not yet registered in ESP-NOW)
            # This allows storing node_id before ensure_peer is called
            now = _ticks_ms()
            self._slots[mac] = PeerSlot(last_used_ms=now, on_route=False, node_id=node_id)

        # Update reverse mapping
        self._node_id_map[node_id] = mac

    def get_mac(self, node_id: int) -> bytes:
        """
        Reverse lookup: get MAC by node_id.

        Args:
            node_id: 16-bit node ID

        Returns:
            6-byte MAC address if known, None otherwise
        """
        return self._node_id_map.get(node_id)

    def remove(self, mac: bytes) -> bool:
        """
        Remove peer from slots and ESP-NOW registration.

        Args:
            mac: 6-byte MAC address to remove

        Returns:
            True if removed successfully, False otherwise
        """
        # Never remove broadcast peer
        if mac == BROADCAST_MAC:
            return False

        if mac not in self._slots:
            return False

        # Remove from reverse mapping
        slot = self._slots[mac]
        if slot.node_id is not None and slot.node_id in self._node_id_map:
            del self._node_id_map[slot.node_id]

        # Remove from ESP-NOW
        result = self._adapter.del_peer(mac)

        # Remove from our tracking
        del self._slots[mac]

        try:
            self._lru_order.remove(mac)
        except ValueError:
            pass  # Not in list

        return result

    def is_registered(self, mac: bytes) -> bool:
        """
        Check if MAC is currently registered as peer.

        Args:
            mac: 6-byte MAC address

        Returns:
            True if registered, False otherwise
        """
        return mac in self._slots

    @property
    def peer_count(self) -> int:
        """
        Number of currently registered peers (excluding broadcast).

        Returns:
            Count of registered peers
        """
        return len(self._slots)

    def get_stats(self) -> dict:
        """
        Return statistics about peer management.

        Returns:
            Dictionary with keys:
                - evictions: Number of peers evicted due to table full
                - hits: Number of ensure_peer calls for already-registered peers
                - misses: Number of ensure_peer calls for new peers
        """
        return self._stats.copy()

    def _evict_one(self) -> bool:
        """
        Evict oldest peer that is not on an active route.

        Iterates through LRU list from oldest to newest, evicting the
        first peer that is not marked as on_route=True.

        Returns:
            True if eviction succeeded, False if no peer could be evicted
        """
        # Iterate from oldest to newest (copy list to avoid modification during iteration)
        for mac in list(self._lru_order):
            slot = self._slots.get(mac)
            if slot is None:
                continue

            # Skip peers on active routes
            if slot.on_route:
                continue

            # Try to remove this peer
            if self.remove(mac):
                self._stats['evictions'] += 1
                return True

        # No peer could be evicted (all protected or removal failed)
        return False

    def get_all_peers(self) -> list:
        """
        Return list of all registered peer MACs.

        Returns:
            List of 6-byte MAC addresses currently registered
        """
        return list(self._slots.keys())

    def clear_stats(self):
        """Reset all statistics counters to zero."""
        self._stats = {
            'evictions': 0,
            'hits': 0,
            'misses': 0
        }

    def get_peer_info(self, mac: bytes) -> dict:
        """
        Get detailed information about a peer.

        Args:
            mac: 6-byte MAC address

        Returns:
            Dictionary with peer information or None if not registered
            Keys: last_used_ms, on_route, node_id
        """
        slot = self._slots.get(mac)
        if slot is None:
            return None

        return {
            'last_used_ms': slot.last_used_ms,
            'on_route': slot.on_route,
            'node_id': slot.node_id
        }
