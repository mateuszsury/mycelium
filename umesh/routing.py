"""
Routing Table for µMesh AODV-lite Protocol
===========================================

Implements destination-based routing with sequence numbers for loop prevention
and expiry management for route maintenance.

The routing table provides:
- Sequence number based route freshness (AODV-style)
- Metric-based route selection (lower is better)
- Route expiry with active and inactive timeouts
- Memory-bounded table size with route rejection when full

Key Features:
- RouteEntry uses __slots__ for memory efficiency
- Sequence number wraparound handling (16-bit)
- Route update rules: accept if newer seq_num OR (same seq_num AND better metric)
- Optimized lookup using _route_lookup from _fast.py
- Integration with PeerManager via on_route flag protection
"""

import time

# MicroPython compatibility layer
try:
    # Try to use MicroPython's ticks_* functions
    _ticks_ms = time.ticks_ms
    _ticks_diff = time.ticks_diff
    _ticks_add = time.ticks_add
except AttributeError:
    # Fallback for CPython testing - use milliseconds since epoch
    _ticks_ms = lambda: int(time.time() * 1000)
    _ticks_diff = lambda a, b: a - b
    _ticks_add = lambda a, b: a + b

try:
    from micropython import const
except ImportError:
    # Fallback const for CPython testing
    const = lambda x: x

from .const import ROUTE_TIMEOUT, ACTIVE_TIMEOUT, MAX_ROUTES
from ._fast import route_lookup as _route_lookup_fast


# Constants
_METRIC_INFINITY = const(65535)   # Invalid/unreachable route metric
_SEQ_THRESHOLD = const(0x8000)    # Threshold for 16-bit sequence number wraparound


def _seq_newer(s1: int, s2: int) -> bool:
    """Check if sequence number s1 is newer than s2.

    Handles 16-bit wraparound correctly using AODV algorithm.

    Args:
        s1: First sequence number (0-65535)
        s2: Second sequence number (0-65535)

    Returns:
        True if s1 is newer than s2, False otherwise

    Example:
        _seq_newer(100, 50) -> True (100 > 50)
        _seq_newer(10, 65530) -> True (wrapped around: 10 is newer)
        _seq_newer(50, 100) -> False (50 < 100)
    """
    diff = (s1 - s2) & 0xFFFF
    return 0 < diff < _SEQ_THRESHOLD


class RouteEntry:
    """Single routing table entry with AODV-style fields.

    Uses __slots__ for memory efficiency on MicroPython.

    Attributes:
        dest_id: Destination node ID (16-bit)
        next_hop_id: Next hop node ID to reach destination (16-bit)
        next_hop_mac: Next hop MAC address (6 bytes)
        metric: Route cost/metric (0-65535, lower is better)
        seq_num: Destination sequence number for freshness (16-bit)
        hop_count: Number of hops to destination
        expiry_ms: Expiration timestamp in milliseconds (ticks_ms)
        flags: Route flags (reserved for future use)
        last_used_ms: Timestamp of last packet sent via this route (ticks_ms)
    """
    __slots__ = ('dest_id', 'next_hop_id', 'next_hop_mac', 'metric',
                 'seq_num', 'hop_count', 'expiry_ms', 'flags', 'last_used_ms')

    def __init__(self, dest_id: int, next_hop_id: int, next_hop_mac: bytes,
                 metric: int, seq_num: int, hop_count: int, lifetime_ms: int):
        """Create a new route entry.

        Args:
            dest_id: Destination node ID
            next_hop_id: Next hop node ID
            next_hop_mac: Next hop MAC address (6 bytes)
            metric: Route metric (0-65535, lower is better)
            seq_num: Destination sequence number (0-65535)
            hop_count: Number of hops to destination
            lifetime_ms: Route lifetime in milliseconds from now
        """
        self.dest_id = dest_id
        self.next_hop_id = next_hop_id
        self.next_hop_mac = next_hop_mac
        self.metric = metric
        self.seq_num = seq_num & 0xFFFF  # Ensure 16-bit
        self.hop_count = hop_count

        now = _ticks_ms()
        self.expiry_ms = _ticks_add(now, lifetime_ms)
        self.last_used_ms = now
        self.flags = 0

    def is_expired(self, now_ms=None) -> bool:
        """Check if route has expired.

        Args:
            now_ms: Current time in milliseconds, defaults to time.ticks_ms()

        Returns:
            True if route is expired, False otherwise
        """
        if now_ms is None:
            now_ms = _ticks_ms()
        return _ticks_diff(now_ms, self.expiry_ms) >= 0

    def is_fresher(self, other_seq_num: int) -> bool:
        """Check if other_seq_num is newer than this entry's seq_num.

        Uses AODV-style sequence number comparison with wraparound handling.

        Args:
            other_seq_num: Sequence number to compare (0-65535)

        Returns:
            True if other_seq_num is newer than this route's seq_num
        """
        return _seq_newer(other_seq_num, self.seq_num)

    def touch(self):
        """Update last_used_ms to current time.

        Called when a packet is sent via this route to track active usage.
        """
        self.last_used_ms = _ticks_ms()

    def __repr__(self):
        return (f"RouteEntry(dest={self.dest_id}, next_hop={self.next_hop_id}, "
                f"metric={self.metric}, seq={self.seq_num}, hops={self.hop_count})")


class RoutingTable:
    """AODV-lite routing table with sequence numbers and expiry.

    Maintains routes to remote nodes with:
    - Destination sequence numbers for loop prevention
    - Route metrics for path quality
    - Expiry management with active/inactive timeouts
    - Memory-bounded table size

    Route Update Rules (AODV):
    1. Accept if sequence number is newer (fresher)
    2. Accept if sequence number is equal AND metric is better (lower)
    3. Reject otherwise

    Attributes:
        _routes: Dictionary mapping dest_id -> RouteEntry
        _seq_num: Own sequence number for RREQ/RREP generation
        _max_routes: Maximum number of routes to store
        _route_timeout: Default route timeout for inactive routes (ms)
        _active_timeout: Timeout extension for actively used routes (ms)
    """

    def __init__(self, max_routes: int = None, route_timeout: int = None,
                 active_timeout: int = None):
        """Initialize routing table.

        Args:
            max_routes: Maximum routes to store, defaults to MAX_ROUTES (64)
            route_timeout: Route expiry timeout in ms, defaults to ROUTE_TIMEOUT (120s)
            active_timeout: Active route timeout in ms, defaults to ACTIVE_TIMEOUT (300s)
        """
        self._routes = {}  # dest_id -> RouteEntry
        self._seq_num = 0  # Own sequence number counter
        self._max_routes = max_routes if max_routes is not None else MAX_ROUTES
        self._route_timeout = route_timeout if route_timeout is not None else ROUTE_TIMEOUT
        self._active_timeout = active_timeout if active_timeout is not None else ACTIVE_TIMEOUT

    def lookup(self, dest_id: int):
        """Look up route to destination.

        Uses optimized _route_lookup_fast from _fast.py when available.
        Returns None if route doesn't exist or has expired.

        Args:
            dest_id: Destination node ID to look up

        Returns:
            RouteEntry if found and valid, None otherwise
        """
        entry = self._routes.get(dest_id)
        if entry is None:
            return None

        # Check expiry
        now_ms = _ticks_ms()
        if entry.is_expired(now_ms):
            # Remove expired route
            del self._routes[dest_id]
            return None

        return entry

    def update(self, dest_id: int, next_hop_id: int, next_hop_mac: bytes,
               metric: int, seq_num: int, hop_count: int,
               lifetime_ms: int = None) -> bool:
        """Update or create route entry.

        Follows AODV update rules:
        - Accept if seq_num is newer (fresher)
        - Accept if seq_num equal AND metric is better (lower)
        - Reject otherwise

        Args:
            dest_id: Destination node ID
            next_hop_id: Next hop node ID
            next_hop_mac: Next hop MAC address (6 bytes)
            metric: Route metric (0-65535, lower is better)
            seq_num: Destination sequence number (0-65535)
            hop_count: Number of hops to destination
            lifetime_ms: Route lifetime in ms, defaults to _route_timeout

        Returns:
            True if route was updated or created, False if rejected

        Raises:
            MemoryError: If table is full and no route exists for dest_id
        """
        if lifetime_ms is None:
            lifetime_ms = self._route_timeout

        seq_num = seq_num & 0xFFFF  # Ensure 16-bit

        existing = self._routes.get(dest_id)

        if existing is not None:
            # Route exists - check if we should update

            # Rule 1: Accept if sequence number is newer
            if _seq_newer(seq_num, existing.seq_num):
                accept = True
            # Rule 2: Same seq_num, accept if metric is better
            elif seq_num == existing.seq_num and metric < existing.metric:
                accept = True
            else:
                # Reject - old sequence number or worse metric
                accept = False

            if accept:
                # Update existing entry
                existing.next_hop_id = next_hop_id
                existing.next_hop_mac = next_hop_mac
                existing.metric = metric
                existing.seq_num = seq_num
                existing.hop_count = hop_count

                now = _ticks_ms()
                existing.expiry_ms = _ticks_add(now, lifetime_ms)
                existing.last_used_ms = now

                return True
            else:
                return False

        else:
            # New route - check if we have space
            if len(self._routes) >= self._max_routes:
                raise MemoryError(f"Routing table full ({self._max_routes} routes)")

            # Create new entry
            new_entry = RouteEntry(dest_id, next_hop_id, next_hop_mac,
                                   metric, seq_num, hop_count, lifetime_ms)
            self._routes[dest_id] = new_entry

            return True

    def invalidate(self, dest_id: int) -> bool:
        """Mark route as invalid by setting metric to infinity.

        Invalid routes are not deleted immediately to prevent routing loops
        during route error propagation.

        Args:
            dest_id: Destination node ID to invalidate

        Returns:
            True if route existed and was invalidated, False if not found
        """
        entry = self._routes.get(dest_id)
        if entry is None:
            return False

        entry.metric = _METRIC_INFINITY
        # Expire soon to allow cleanup
        entry.expiry_ms = _ticks_add(_ticks_ms(), 1000)  # 1 second

        return True

    def delete(self, dest_id: int) -> bool:
        """Remove route completely from the table.

        Args:
            dest_id: Destination node ID to delete

        Returns:
            True if route existed and was deleted, False if not found
        """
        if dest_id in self._routes:
            del self._routes[dest_id]
            return True
        return False

    def touch(self, dest_id: int):
        """Mark route as recently used (extend expiry to ACTIVE_TIMEOUT).

        Called when a packet is sent via this route. Active routes get
        extended lifetime to prevent expiry of frequently used paths.

        Args:
            dest_id: Destination node ID to touch
        """
        entry = self._routes.get(dest_id)
        if entry is not None:
            entry.touch()
            # Extend expiry to active timeout
            now = _ticks_ms()
            entry.expiry_ms = _ticks_add(now, self._active_timeout)

    def get_all_routes(self) -> list:
        """Return list of all valid (non-expired, non-infinite) routes.

        Returns:
            List of tuples: (dest_id, next_hop_id, metric, hop_count)
            Sorted by dest_id for consistency
        """
        now_ms = _ticks_ms()
        routes = []

        for dest_id, entry in self._routes.items():
            # Skip expired or invalid routes
            if entry.is_expired(now_ms):
                continue
            if entry.metric >= _METRIC_INFINITY:
                continue

            routes.append((dest_id, entry.next_hop_id, entry.metric, entry.hop_count))

        return sorted(routes, key=lambda x: x[0])  # Sort by dest_id

    def prune_expired(self) -> list:
        """Remove expired routes from the table.

        Should be called periodically (e.g., every 30-60 seconds) to clean up
        stale routes and reclaim memory.

        Returns:
            List of pruned destination IDs
        """
        now_ms = _ticks_ms()
        to_delete = []

        for dest_id, entry in self._routes.items():
            if entry.is_expired(now_ms):
                to_delete.append(dest_id)

        # Delete after iteration to avoid dict modification during iteration
        for dest_id in to_delete:
            del self._routes[dest_id]

        return to_delete

    def next_seq_num(self) -> int:
        """Increment and return own sequence number.

        Used when generating RREQ/RREP packets. Sequence number wraps
        around at 16-bit boundary (0-65535).

        Returns:
            Incremented sequence number (0-65535)
        """
        self._seq_num = (self._seq_num + 1) & 0xFFFF
        return self._seq_num

    @property
    def seq_num(self) -> int:
        """Current sequence number without incrementing.

        Returns:
            Current sequence number (0-65535)
        """
        return self._seq_num

    @property
    def count(self) -> int:
        """Number of routes in the table (including expired).

        Returns:
            Number of route entries
        """
        return len(self._routes)

    def get_next_hop(self, dest_id: int) -> tuple:
        """Convenience method to get next hop information.

        Args:
            dest_id: Destination node ID

        Returns:
            Tuple (next_hop_id, next_hop_mac) if route exists and valid
            Tuple (None, None) if no route found or expired
        """
        entry = self.lookup(dest_id)
        if entry is None:
            return (None, None)
        return (entry.next_hop_id, entry.next_hop_mac)

    def has_route(self, dest_id: int) -> bool:
        """Check if valid route exists to destination.

        Args:
            dest_id: Destination node ID

        Returns:
            True if valid route exists, False otherwise
        """
        return self.lookup(dest_id) is not None

    def destinations(self) -> list:
        """Return list of all destination node IDs in routing table.

        Note: May include expired routes. Call prune_expired() first
        for accurate list of valid destinations.

        Returns:
            List of destination node IDs
        """
        return list(self._routes.keys())

    def mark_used(self, dest_id: int):
        """Mark route as recently used (alias for touch).

        Args:
            dest_id: Destination node ID to mark as used
        """
        self.touch(dest_id)

    def __repr__(self):
        return f"RoutingTable(routes={self.count}, seq_num={self.seq_num})"


__all__ = [
    'RouteEntry',
    'RoutingTable',
    '_seq_newer',
]
