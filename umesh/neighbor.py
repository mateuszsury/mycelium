"""
Neighbor Table and HELLO Beacon Management
===========================================

Tracks direct (1-hop) neighbors discovered via HELLO beacons with link quality metrics.

The neighbor table maintains information about nearby nodes including:
- MAC address and node ID mapping
- RSSI smoothed using EWMA (Exponentially Weighted Moving Average)
- Packet loss rate based on expected vs received HELLO count
- Last seen timestamp for freshness tracking
- Link Quality Index (LQI) combining RSSI, reliability, and freshness

This is a critical component of the mesh routing layer, providing the foundation
for route discovery and next-hop selection.
"""

try:
    import time
    # Check if MicroPython time functions are available
    if not hasattr(time, 'ticks_ms'):
        # Fallback for CPython testing - use milliseconds since epoch
        import time as _time
        time.ticks_ms = lambda: int(_time.time() * 1000)
        time.ticks_diff = lambda a, b: a - b
        time.sleep_ms = lambda ms: _time.sleep(ms / 1000.0)
except ImportError:
    # Should not happen, but handle gracefully
    import time as time

try:
    from micropython import const
except ImportError:
    # Fallback const for CPython testing
    const = lambda x: x

from .const import NEIGHBOR_TIMEOUT, HELLO_INTERVAL
from ._fast import rssi_ewma, calc_lqi


# Constants
_DEFAULT_RSSI = const(-90)        # Initial RSSI for new neighbors (dBm)
_RSSI_ALPHA = 0.375               # EWMA smoothing factor (37.5% new, 62.5% old)
_MAX_NEIGHBORS = const(64)        # Maximum tracked neighbors


class NeighborEntry:
    """Single neighbor entry with link quality metrics.

    Attributes:
        mac: 6-byte MAC address
        node_id: 16-bit node ID
        rssi_avg: Average RSSI (stored as uint8, interpreted as signed -128 to 127)
        last_seen: Timestamp in milliseconds (ticks_ms)
        hello_count: Number of received HELLO packets
        expected_hellos: Number of expected HELLO packets (based on time elapsed)
        hop_count: Hop count from this neighbor (0 for direct neighbor)
    """
    __slots__ = ('mac', 'node_id', 'rssi_avg', 'last_seen', 'hello_count',
                 'expected_hellos', 'hop_count')

    def __init__(self, mac: bytes, node_id: int, rssi: int, now_ms: int, hop_count: int = 0):
        """Initialize new neighbor entry.

        Args:
            mac: 6-byte MAC address
            node_id: 16-bit node ID
            rssi: Initial RSSI in dBm (typically -100 to -30)
            now_ms: Current timestamp in milliseconds
            hop_count: Hop count (0 for direct neighbor)
        """
        self.mac = mac
        self.node_id = node_id
        # Store RSSI as unsigned byte using two's complement representation
        # Negative values: -50 -> 206 (0xCE), read back as 206-256 = -50
        # Positive values stored directly (though RSSI is typically negative)
        self.rssi_avg = rssi if rssi >= 0 else (rssi + 256) & 0xFF
        self.last_seen = now_ms
        self.hello_count = 1
        self.expected_hellos = 1
        self.hop_count = hop_count

    @property
    def rssi_signed(self) -> int:
        """Get RSSI as signed value (-128 to 127).

        Converts from internal two's complement uint8 storage back to
        signed integer for external use and calculations.

        Returns:
            Signed RSSI value in dBm (typically -100 to -30)
        """
        return self.rssi_avg if self.rssi_avg < 128 else self.rssi_avg - 256


class NeighborTable:
    """Table of direct neighbors with link quality metrics.

    Maintains a mapping of node_id -> NeighborEntry and MAC -> node_id.
    Provides link quality calculation, best neighbor selection, and dead neighbor pruning.

    Args:
        timeout: Neighbor timeout in milliseconds, default NEIGHBOR_TIMEOUT (60s)
        hello_interval: Expected HELLO interval in milliseconds, default HELLO_INTERVAL (10s)
        max_neighbors: Maximum number of tracked neighbors, default 64
    """

    def __init__(self, timeout: int = NEIGHBOR_TIMEOUT,
                 hello_interval: int = HELLO_INTERVAL,
                 max_neighbors: int = _MAX_NEIGHBORS):
        """Initialize neighbor table."""
        self._timeout = timeout
        self._hello_interval = hello_interval
        self._max_neighbors = max_neighbors

        # Main storage: node_id -> NeighborEntry
        self._neighbors = {}

        # Reverse lookup: mac -> node_id (for MAC-based queries)
        self._mac_to_id = {}

    def update(self, mac: bytes, node_id: int, rssi: int, hop_count: int = 0) -> bool:
        """Update neighbor info from received HELLO/packet.

        Creates new entry if first time seeing this neighbor. Updates RSSI using
        EWMA smoothing and increments HELLO count.

        Args:
            mac: 6-byte MAC address of neighbor
            node_id: 16-bit node ID
            rssi: Current RSSI in dBm (typically -100 to -30)
            hop_count: Hop count reported by neighbor (0 for direct)

        Returns:
            True if this is a new neighbor, False if existing neighbor updated

        Raises:
            MemoryError: If max_neighbors limit reached and this is a new neighbor
        """
        now_ms = time.ticks_ms()

        # Check if existing neighbor
        entry = self._neighbors.get(node_id)

        if entry is None:
            # New neighbor
            if len(self._neighbors) >= self._max_neighbors:
                raise MemoryError(f"Neighbor table full ({self._max_neighbors} neighbors)")

            # Create new entry
            entry = NeighborEntry(mac, node_id, rssi, now_ms, hop_count)
            self._neighbors[node_id] = entry
            self._mac_to_id[mac] = node_id
            return True

        else:
            # Existing neighbor - update metrics
            elapsed_ms = time.ticks_diff(now_ms, entry.last_seen)

            # Update RSSI using EWMA - convert from unsigned byte back to signed for calculation
            current_rssi = entry.rssi_avg if entry.rssi_avg < 128 else entry.rssi_avg - 256
            new_avg = rssi_ewma(current_rssi, rssi, _RSSI_ALPHA)
            # Store back as unsigned byte using two's complement
            entry.rssi_avg = new_avg if new_avg >= 0 else (new_avg + 256) & 0xFF

            # Update HELLO count and expected count with overflow protection
            entry.hello_count = min(entry.hello_count + 1, 65535)
            entry.expected_hellos = min(
                entry.expected_hellos + max(1, elapsed_ms // self._hello_interval),
                65535
            )

            # Update timestamp and hop count
            entry.last_seen = now_ms
            entry.hop_count = hop_count

            # Update MAC if changed (rare, but handle node ID reuse)
            if entry.mac != mac:
                del self._mac_to_id[entry.mac]
                entry.mac = mac
                self._mac_to_id[mac] = node_id

            return False

    def get_link_quality(self, node_id: int) -> int:
        """Get Link Quality Index (0-255) for neighbor.

        LQI combines RSSI, packet loss rate, and freshness:
        - RSSI: 60% weight, normalized from -100dBm (0) to -30dBm (255)
        - Reliability: 30% weight, based on received vs expected HELLOs
        - Freshness: 10% weight, based on time since last seen

        Args:
            node_id: Node ID to query

        Returns:
            Link Quality Index 0-255 (higher is better), or 0 if neighbor unknown
        """
        entry = self._neighbors.get(node_id)
        if entry is None:
            return 0

        now_ms = time.ticks_ms()
        age_ms = time.ticks_diff(now_ms, entry.last_seen)

        # Convert uint8 RSSI back to signed
        rssi_avg = entry.rssi_avg if entry.rssi_avg < 128 else entry.rssi_avg - 256

        # Calculate loss percentage
        loss_pct = self._calc_loss_rate(entry)

        # Use optimized LQI calculation from _fast.py
        return calc_lqi(rssi_avg, loss_pct, age_ms, self._timeout)

    def get_best_neighbors(self, n: int = 5) -> list:
        """Return top N neighbors sorted by LQI (highest first).

        Args:
            n: Number of neighbors to return (default 5)

        Returns:
            List of tuples (node_id, mac, lqi) sorted by LQI descending
        """
        if not self._neighbors:
            return []

        # Calculate LQI for all neighbors
        neighbor_lqi = []
        now_ms = time.ticks_ms()

        for node_id, entry in self._neighbors.items():
            age_ms = time.ticks_diff(now_ms, entry.last_seen)

            # Skip dead neighbors
            if age_ms >= self._timeout:
                continue

            rssi_avg = entry.rssi_avg if entry.rssi_avg < 128 else entry.rssi_avg - 256
            loss_pct = self._calc_loss_rate(entry)
            lqi = calc_lqi(rssi_avg, loss_pct, age_ms, self._timeout)

            neighbor_lqi.append((node_id, entry.mac, lqi))

        # Sort by LQI descending
        neighbor_lqi.sort(key=lambda x: x[2], reverse=True)

        return neighbor_lqi[:n]

    def get_neighbor(self, node_id: int) -> dict:
        """Get full info for neighbor.

        Args:
            node_id: Node ID to query

        Returns:
            Dictionary with keys: mac, rssi_avg, last_seen, lqi, hop_count,
            hello_count, expected_hellos, loss_pct
            Returns None if neighbor not found
        """
        entry = self._neighbors.get(node_id)
        if entry is None:
            return None

        now_ms = time.ticks_ms()
        age_ms = time.ticks_diff(now_ms, entry.last_seen)
        rssi_avg = entry.rssi_avg if entry.rssi_avg < 128 else entry.rssi_avg - 256
        loss_pct = self._calc_loss_rate(entry)
        lqi = calc_lqi(rssi_avg, loss_pct, age_ms, self._timeout)

        return {
            'mac': entry.mac,
            'rssi_avg': rssi_avg,
            'last_seen': entry.last_seen,
            'lqi': lqi,
            'hop_count': entry.hop_count,
            'hello_count': entry.hello_count,
            'expected_hellos': entry.expected_hellos,
            'loss_pct': loss_pct,
            'age_ms': age_ms
        }

    def get_by_mac(self, mac: bytes) -> dict:
        """Get neighbor info by MAC address.

        Args:
            mac: 6-byte MAC address

        Returns:
            Dictionary with neighbor info (same as get_neighbor), or None if not found
        """
        node_id = self._mac_to_id.get(mac)
        if node_id is None:
            return None
        return self.get_neighbor(node_id)

    def prune_dead(self) -> list:
        """Remove neighbors not seen for NEIGHBOR_TIMEOUT.

        Should be called periodically (e.g., every 30-60 seconds) to clean up
        dead neighbors and free memory.

        Returns:
            List of pruned node_ids (for callback notifications)
        """
        now_ms = time.ticks_ms()
        pruned = []

        # Find dead neighbors
        for node_id, entry in list(self._neighbors.items()):
            age_ms = time.ticks_diff(now_ms, entry.last_seen)
            if age_ms >= self._timeout:
                pruned.append(node_id)
                del self._neighbors[node_id]
                del self._mac_to_id[entry.mac]

        return pruned

    def is_neighbor(self, node_id: int) -> bool:
        """Check if node_id is a known direct neighbor.

        Args:
            node_id: Node ID to check

        Returns:
            True if node is in neighbor table, False otherwise
        """
        return node_id in self._neighbors

    def get_mac(self, node_id: int) -> bytes:
        """Get MAC address for node_id.

        Args:
            node_id: Node ID to query

        Returns:
            6-byte MAC address, or None if neighbor unknown
        """
        entry = self._neighbors.get(node_id)
        if entry is None:
            return None
        return entry.mac

    @property
    def count(self) -> int:
        """Number of known neighbors.

        Returns:
            Count of neighbors in table
        """
        return len(self._neighbors)

    def all_neighbors(self) -> list:
        """Return list of all neighbor node_ids.

        Returns:
            List of node IDs (may include dead neighbors, call prune_dead first)
        """
        return list(self._neighbors.keys())

    def _calc_loss_rate(self, entry: NeighborEntry) -> int:
        """Calculate packet loss percentage (0-100).

        Based on expected vs received HELLO count. If a neighbor sends HELLOs
        every 10 seconds and we've tracked them for 60 seconds, we expect ~6 HELLOs.
        If we only received 4, loss rate is 33%.

        Args:
            entry: Neighbor entry to calculate loss for

        Returns:
            Loss percentage 0-100 (0 = perfect, 100 = all lost)
        """
        if entry.expected_hellos == 0:
            return 0

        # Clamp hello_count to expected (in case of timing edge cases)
        received = min(entry.hello_count, entry.expected_hellos)

        loss_pct = ((entry.expected_hellos - received) * 100) // entry.expected_hellos

        # Clamp to 0-100
        if loss_pct < 0:
            return 0
        if loss_pct > 100:
            return 100
        return loss_pct

    def __repr__(self) -> str:
        """String representation of neighbor table."""
        return f"<NeighborTable: {self.count} neighbors, timeout={self._timeout}ms>"
