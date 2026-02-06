"""
Optimized hot-path functions for µMesh protocol.

This module contains performance-critical functions decorated with
@micropython.viper and @micropython.native for maximum efficiency.

VIPER functions (5):
    - _dup_check: Duplicate packet detection in circular buffer
    - _crc8: CRC8/CCITT checksum calculation
    - _parse_header: Parse 14-byte packet header
    - _build_header: Build 14-byte packet header
    - _rssi_ewma: Exponentially weighted moving average for RSSI

NATIVE functions (3):
    - _route_lookup: Routing table lookup with expiry check
    - _calc_lqi: Link Quality Index calculation
    - _accumulate_metric: Route metric accumulation

Each viper function has a Python wrapper for testing and fallback.
"""

try:
    import micropython
    from array import array
    VIPER_AVAILABLE = True
except ImportError:
    VIPER_AVAILABLE = False


# =============================================================================
# VIPER FUNCTIONS - Maximum performance, low-level operations
# =============================================================================

if VIPER_AVAILABLE:
    @micropython.viper
    def _dup_check(buf: ptr8, buf_len: int, src_id: int, pkt_id: int) -> bool:
        """Check if (src_id, pkt_id) exists in circular buffer.

        Args:
            buf: Pre-allocated bytearray, 4 bytes per entry [src_hi, src_lo, pkt_hi, pkt_lo]
            buf_len: Length of buffer in bytes (must be multiple of 4)
            src_id: Source node ID (16-bit)
            pkt_id: Packet ID (16-bit)

        Returns:
            True if duplicate found, False otherwise
        """
        i = 0
        while i < buf_len:
            s = (int(buf[i]) << 8) | int(buf[i + 1])
            p = (int(buf[i + 2]) << 8) | int(buf[i + 3])
            if s == src_id and p == pkt_id:
                return True
            i += 4
        return False


    @micropython.viper
    def _crc8(data: ptr8, length: int) -> int:
        """CRC8/CCITT calculation on raw bytes.

        Polynomial: 0x31, Initial value: 0xFF

        Args:
            data: Pointer to byte array
            length: Number of bytes to process

        Returns:
            CRC8 checksum (0-255)
        """
        crc = 0xFF
        i = 0
        while i < length:
            crc ^= int(data[i])
            j = 0
            while j < 8:
                if crc & 0x80:
                    crc = (crc << 1) ^ 0x31
                else:
                    crc = crc << 1
                crc &= 0xFF
                j += 1
            i += 1
        return crc


    @micropython.viper
    def _parse_header(raw: ptr8, out: ptr32) -> bool:
        """Parse 14-byte header into pre-allocated int32 array.

        Header format (14 bytes):
            [0]    : version (4 bits) | pkt_type (4 bits)
            [1]    : flags (8 bits)
            [2-3]  : pkt_id (16 bits, big-endian)
            [4]    : ttl (8 bits)
            [5]    : hop_count (8 bits)
            [6-7]  : src_id (16 bits, big-endian)
            [8-9]  : dest_id (16 bits, big-endian)
            [10-12]: frag_info (24 bits, big-endian)
            [13]   : checksum (CRC8)

        Args:
            raw: Pointer to 14-byte header
            out: Pre-allocated int32 array [10 elements]
                 out[0]=version, out[1]=pkt_type, out[2]=flags, out[3]=pkt_id,
                 out[4]=ttl, out[5]=hop_count, out[6]=src_id, out[7]=dest_id,
                 out[8]=frag_info, out[9]=checksum

        Returns:
            False if CRC mismatch, True if valid
        """
        out[0] = (int(raw[0]) >> 4) & 0x0F          # version
        out[1] = int(raw[0]) & 0x0F                  # pkt_type
        out[2] = int(raw[1])                         # flags
        out[3] = (int(raw[2]) << 8) | int(raw[3])   # pkt_id
        out[4] = int(raw[4])                         # ttl
        out[5] = int(raw[5])                         # hop_count
        out[6] = (int(raw[6]) << 8) | int(raw[7])   # src_id
        out[7] = (int(raw[8]) << 8) | int(raw[9])   # dest_id
        out[8] = (int(raw[10]) << 16) | (int(raw[11]) << 8) | int(raw[12])  # frag_info
        out[9] = int(raw[13])                        # checksum

        # Inline CRC verification (viper cannot call viper functions)
        crc = 0xFF
        k = 0
        while k < 13:
            crc ^= int(raw[k])
            j = 0
            while j < 8:
                if crc & 0x80:
                    crc = (crc << 1) ^ 0x31
                else:
                    crc = crc << 1
                crc &= 0xFF
                j += 1
            k += 1
        return crc == int(raw[13])


    @micropython.viper
    def _build_header(buf: ptr8, version: int, pkt_type: int, flags: int,
                      pkt_id: int, ttl: int, hop_count: int,
                      src_id: int, dest_id: int, frag_info: int):
        """Build 14-byte header into pre-allocated buffer.

        Automatically calculates and stores CRC8 at buf[13].

        Args:
            buf: Pre-allocated bytearray (at least 14 bytes)
            version: Protocol version (0-15)
            pkt_type: Packet type (0-15)
            flags: Flags byte (0-255)
            pkt_id: Packet ID (0-65535)
            ttl: Time-to-live (0-255)
            hop_count: Hop counter (0-255)
            src_id: Source node ID (0-65535)
            dest_id: Destination node ID (0-65535)
            frag_info: Fragmentation info (24-bit value)
        """
        buf[0] = ((version & 0x0F) << 4) | (pkt_type & 0x0F)
        buf[1] = flags & 0xFF
        buf[2] = (pkt_id >> 8) & 0xFF
        buf[3] = pkt_id & 0xFF
        buf[4] = ttl & 0xFF
        buf[5] = hop_count & 0xFF
        buf[6] = (src_id >> 8) & 0xFF
        buf[7] = src_id & 0xFF
        buf[8] = (dest_id >> 8) & 0xFF
        buf[9] = dest_id & 0xFF
        buf[10] = (frag_info >> 16) & 0xFF
        buf[11] = (frag_info >> 8) & 0xFF
        buf[12] = frag_info & 0xFF

        # Inline CRC8 calculation
        crc = 0xFF
        k = 0
        while k < 13:
            crc ^= int(buf[k])
            j = 0
            while j < 8:
                if crc & 0x80:
                    crc = (crc << 1) ^ 0x31
                else:
                    crc = crc << 1
                crc &= 0xFF
                j += 1
            k += 1
        buf[13] = crc


    @micropython.viper
    def _rssi_ewma(old_avg: int, new_rssi: int, alpha_num: int,
                   alpha_den: int) -> int:
        """Integer EWMA for RSSI values.

        Calculates: result = old_avg * (1 - alpha) + new_rssi * alpha
        where alpha = alpha_num / alpha_den

        Handles signed values (-128 to 127) stored as uint8.

        Args:
            old_avg: Previous average RSSI (as uint8, but treated as signed)
            new_rssi: New RSSI measurement (as uint8, but treated as signed)
            alpha_num: Alpha numerator (e.g., 3 for alpha=0.375)
            alpha_den: Alpha denominator (e.g., 8 for alpha=0.375)

        Returns:
            Updated RSSI average (uint8)

        Example:
            Alpha = 3/8 = 0.375 means 37.5% new value, 62.5% old value
        """
        # Sign extension for negative RSSI values
        if old_avg > 127:
            old_avg = old_avg - 256
        if new_rssi > 127:
            new_rssi = new_rssi - 256

        # EWMA calculation
        result = (old_avg * (alpha_den - alpha_num) + new_rssi * alpha_num)

        # Integer division with rounding
        if result >= 0:
            result = result // alpha_den
        else:
            result = -1 * ((-result) // alpha_den)

        return result & 0xFF

else:
    # Fallback implementations when viper is not available
    def _dup_check(buf, buf_len, src_id, pkt_id):
        """Pure Python fallback for duplicate check."""
        for i in range(0, buf_len, 4):
            s = (buf[i] << 8) | buf[i + 1]
            p = (buf[i + 2] << 8) | buf[i + 3]
            if s == src_id and p == pkt_id:
                return True
        return False

    def _crc8(data, length):
        """Pure Python fallback for CRC8."""
        crc = 0xFF
        for i in range(length):
            crc ^= data[i]
            for _ in range(8):
                if crc & 0x80:
                    crc = (crc << 1) ^ 0x31
                else:
                    crc = crc << 1
                crc &= 0xFF
        return crc

    def _parse_header(raw, out):
        """Pure Python fallback for header parsing."""
        # Validate minimum header size
        if len(raw) < 14:
            return False

        out[0] = (raw[0] >> 4) & 0x0F
        out[1] = raw[0] & 0x0F
        out[2] = raw[1]
        out[3] = (raw[2] << 8) | raw[3]
        out[4] = raw[4]
        out[5] = raw[5]
        out[6] = (raw[6] << 8) | raw[7]
        out[7] = (raw[8] << 8) | raw[9]
        out[8] = (raw[10] << 16) | (raw[11] << 8) | raw[12]
        out[9] = raw[13]

        # CRC check
        crc = 0xFF
        for i in range(13):
            crc ^= raw[i]
            for _ in range(8):
                if crc & 0x80:
                    crc = (crc << 1) ^ 0x31
                else:
                    crc = crc << 1
                crc &= 0xFF
        return crc == raw[13]

    def _build_header(buf, version, pkt_type, flags, pkt_id, ttl, hop_count,
                      src_id, dest_id, frag_info):
        """Pure Python fallback for header building."""
        buf[0] = ((version & 0x0F) << 4) | (pkt_type & 0x0F)
        buf[1] = flags & 0xFF
        buf[2] = (pkt_id >> 8) & 0xFF
        buf[3] = pkt_id & 0xFF
        buf[4] = ttl & 0xFF
        buf[5] = hop_count & 0xFF
        buf[6] = (src_id >> 8) & 0xFF
        buf[7] = src_id & 0xFF
        buf[8] = (dest_id >> 8) & 0xFF
        buf[9] = dest_id & 0xFF
        buf[10] = (frag_info >> 16) & 0xFF
        buf[11] = (frag_info >> 8) & 0xFF
        buf[12] = frag_info & 0xFF

        # CRC calculation
        crc = 0xFF
        for i in range(13):
            crc ^= buf[i]
            for _ in range(8):
                if crc & 0x80:
                    crc = (crc << 1) ^ 0x31
                else:
                    crc = crc << 1
                crc &= 0xFF
        buf[13] = crc

    def _rssi_ewma(old_avg, new_rssi, alpha_num, alpha_den):
        """Pure Python fallback for RSSI EWMA."""
        # Sign extension
        if old_avg > 127:
            old_avg = old_avg - 256
        if new_rssi > 127:
            new_rssi = new_rssi - 256

        result = (old_avg * (alpha_den - alpha_num) + new_rssi * alpha_num)

        if result >= 0:
            result = result // alpha_den
        else:
            result = -1 * ((-result) // alpha_den)

        return result & 0xFF


# =============================================================================
# NATIVE FUNCTIONS - Moderate performance boost, full Python access
# =============================================================================

if VIPER_AVAILABLE:
    @micropython.native
    def _route_lookup(table: dict, dest_id: int, now_ms: int):
        """Look up route in routing table with expiry check.

        Args:
            table: Routing table dictionary {dest_id: [next_hop_id, next_hop_mac, metric, ..., expiry_ms]}
            dest_id: Destination node ID to look up
            now_ms: Current time in milliseconds

        Returns:
            Tuple (next_hop_id, next_hop_mac, metric) if route exists and valid
            None if no route or expired
        """
        entry = table.get(dest_id)
        if entry is None:
            return None
        if now_ms > entry[4]:  # expiry_ms is at index 4
            del table[dest_id]
            return None
        return (entry[0], entry[1], entry[2])  # next_hop_id, next_hop_mac, metric


    @micropython.native
    def _calc_lqi(rssi_avg: int, loss_pct: int, age_ms: int,
                  timeout_ms: int) -> int:
        """Calculate Link Quality Index (LQI) 0-255.

        Weights:
            - RSSI: 60%
            - Reliability (1 - loss): 30%
            - Freshness (time since last seen): 10%

        Args:
            rssi_avg: Average RSSI in dBm (typically -100 to -30)
            loss_pct: Packet loss percentage (0-100)
            age_ms: Time since last packet in milliseconds
            timeout_ms: Timeout threshold in milliseconds

        Returns:
            Link Quality Index 0-255 (higher is better)
        """
        # RSSI normalization: -100dBm -> 0, -30dBm -> 255
        rssi_n = rssi_avg + 100
        if rssi_n < 0:
            rssi_n = 0
        rssi_n = rssi_n * 255 // 70  # Scale -100..-30 to 0..255
        if rssi_n > 255:
            rssi_n = 255

        # Loss normalization: 0% loss -> 255, 100% loss -> 0
        loss_n = (100 - loss_pct) * 255 // 100

        # Freshness: just expired -> 0, just seen -> 255
        if age_ms >= timeout_ms:
            fresh_n = 0
        else:
            fresh_n = (timeout_ms - age_ms) * 255 // timeout_ms

        # Weighted sum: RSSI 60%, reliability 30%, freshness 10%
        lqi = (rssi_n * 60 + loss_n * 30 + fresh_n * 10) // 100
        if lqi > 255:
            return 255
        if lqi < 0:
            return 0
        return lqi


    @micropython.native
    def _accumulate_metric(current_metric: int, link_lqi: int) -> int:
        """Accumulate route metric (lower is better).

        Each hop adds a cost of (256 - LQI). Better links (higher LQI)
        add less cost to the total metric.

        Args:
            current_metric: Current accumulated metric
            link_lqi: Link Quality Index of the hop (0-255)

        Returns:
            Updated metric, capped at 65535
        """
        cost = 256 - link_lqi
        total = current_metric + cost
        if total > 65535:
            return 65535
        return total

else:
    # Fallback implementations
    def _route_lookup(table, dest_id, now_ms):
        """Pure Python fallback for route lookup."""
        entry = table.get(dest_id)
        if entry is None:
            return None
        if now_ms > entry[4]:
            del table[dest_id]
            return None
        return (entry[0], entry[1], entry[2])

    def _calc_lqi(rssi_avg, loss_pct, age_ms, timeout_ms):
        """Pure Python fallback for LQI calculation."""
        rssi_n = rssi_avg + 100
        if rssi_n < 0:
            rssi_n = 0
        rssi_n = rssi_n * 255 // 70
        if rssi_n > 255:
            rssi_n = 255

        loss_n = (100 - loss_pct) * 255 // 100

        if age_ms >= timeout_ms:
            fresh_n = 0
        else:
            fresh_n = (timeout_ms - age_ms) * 255 // timeout_ms

        lqi = (rssi_n * 60 + loss_n * 30 + fresh_n * 10) // 100
        if lqi > 255:
            return 255
        if lqi < 0:
            return 0
        return lqi

    def _accumulate_metric(current_metric, link_lqi):
        """Pure Python fallback for metric accumulation."""
        cost = 256 - link_lqi
        total = current_metric + cost
        if total > 65535:
            return 65535
        return total


# =============================================================================
# PYTHON WRAPPERS - For testing, debugging, and high-level API
# =============================================================================

def dup_check(buf: bytearray, src_id: int, pkt_id: int) -> bool:
    """Check if packet is a duplicate.

    Python wrapper around _dup_check with automatic buffer length.

    Args:
        buf: Circular buffer containing recent (src_id, pkt_id) pairs
        src_id: Source node ID (0-65535)
        pkt_id: Packet ID (0-65535)

    Returns:
        True if duplicate found, False otherwise
    """
    return _dup_check(buf, len(buf), src_id, pkt_id)


def crc8(data: bytes) -> int:
    """Calculate CRC8/CCITT checksum.

    Python wrapper around _crc8.

    Args:
        data: Bytes to checksum

    Returns:
        CRC8 value (0-255)
    """
    return _crc8(data, len(data))


def parse_header(raw: bytes) -> dict:
    """Parse packet header into dictionary.

    Python wrapper around _parse_header that converts to dict.

    Args:
        raw: 14-byte header

    Returns:
        Dictionary with parsed header fields

    Raises:
        ValueError: If CRC mismatch or invalid header
    """
    if len(raw) < 14:
        raise ValueError(f"Header too short: {len(raw)} bytes, expected 14")

    if VIPER_AVAILABLE:
        out = array('i', [0] * 10)
    else:
        out = [0] * 10

    ok = _parse_header(raw, out)
    if not ok:
        raise ValueError("CRC mismatch in packet header")

    return {
        'version': out[0],
        'pkt_type': out[1],
        'flags': out[2],
        'pkt_id': out[3],
        'ttl': out[4],
        'hop_count': out[5],
        'src_id': out[6],
        'dest_id': out[7],
        'frag_info': out[8],
        'checksum': out[9]
    }


def build_header(version: int, pkt_type: int, flags: int, pkt_id: int,
                 ttl: int, hop_count: int, src_id: int, dest_id: int,
                 frag_info: int = 0) -> bytes:
    """Build packet header.

    Python wrapper around _build_header that returns bytes.

    Args:
        version: Protocol version (0-15)
        pkt_type: Packet type (0-15)
        flags: Flags byte (0-255)
        pkt_id: Packet ID (0-65535)
        ttl: Time-to-live (0-255)
        hop_count: Hop counter (0-255)
        src_id: Source node ID (0-65535)
        dest_id: Destination node ID (0-65535)
        frag_info: Fragmentation info (0-16777215), default 0

    Returns:
        14-byte header as bytes
    """
    buf = bytearray(14)
    _build_header(buf, version, pkt_type, flags, pkt_id, ttl, hop_count,
                  src_id, dest_id, frag_info)
    return bytes(buf)


def rssi_ewma(old_avg: int, new_rssi: int, alpha: float = 0.375) -> int:
    """Calculate exponential moving average of RSSI.

    Python wrapper around _rssi_ewma with floating-point alpha.

    Args:
        old_avg: Previous average RSSI (-128 to 127)
        new_rssi: New RSSI measurement (-128 to 127)
        alpha: Smoothing factor (0.0 to 1.0), default 0.375
               Higher alpha = more weight on new value

    Returns:
        Updated RSSI average (-128 to 127)
    """
    # Convert float alpha to integer fraction
    # Common values: 0.125 (1/8), 0.25 (2/8), 0.375 (3/8), 0.5 (4/8)
    if alpha <= 0.0 or alpha >= 1.0:
        raise ValueError(f"Alpha must be between 0.0 and 1.0, got {alpha}")

    # Use denominator of 8 for common fractions
    alpha_den = 8
    alpha_num = int(alpha * alpha_den + 0.5)  # Round to nearest

    # Convert signed to unsigned
    old_u8 = old_avg & 0xFF
    new_u8 = new_rssi & 0xFF

    result_u8 = _rssi_ewma(old_u8, new_u8, alpha_num, alpha_den)

    # Convert back to signed
    if result_u8 > 127:
        return result_u8 - 256
    return result_u8


def route_lookup(table: dict, dest_id: int, now_ms: int):
    """Look up route in routing table.

    Python wrapper around _route_lookup (transparent passthrough).

    Args:
        table: Routing table dictionary
        dest_id: Destination node ID
        now_ms: Current time in milliseconds

    Returns:
        Tuple (next_hop_id, next_hop_mac, metric) or None
    """
    return _route_lookup(table, dest_id, now_ms)


def calc_lqi(rssi_avg: int, loss_pct: int = 0, age_ms: int = 0,
             timeout_ms: int = 30000) -> int:
    """Calculate Link Quality Index.

    Python wrapper around _calc_lqi with defaults.

    Args:
        rssi_avg: Average RSSI in dBm
        loss_pct: Packet loss percentage (0-100), default 0
        age_ms: Time since last packet in ms, default 0
        timeout_ms: Timeout threshold in ms, default 30000 (30 seconds)

    Returns:
        Link Quality Index 0-255
    """
    return _calc_lqi(rssi_avg, loss_pct, age_ms, timeout_ms)


def accumulate_metric(current_metric: int, link_lqi: int) -> int:
    """Accumulate route metric.

    Python wrapper around _accumulate_metric (transparent passthrough).

    Args:
        current_metric: Current accumulated metric
        link_lqi: Link Quality Index (0-255)

    Returns:
        Updated metric (0-65535)
    """
    return _accumulate_metric(current_metric, link_lqi)


# =============================================================================
# MODULE INFO
# =============================================================================

__all__ = [
    # Low-level viper/native functions (for advanced use)
    '_dup_check',
    '_crc8',
    '_parse_header',
    '_build_header',
    '_rssi_ewma',
    '_route_lookup',
    '_calc_lqi',
    '_accumulate_metric',

    # High-level Python wrappers (recommended API)
    'dup_check',
    'crc8',
    'parse_header',
    'build_header',
    'rssi_ewma',
    'route_lookup',
    'calc_lqi',
    'accumulate_metric',

    # Module info
    'VIPER_AVAILABLE',
]


def get_optimization_info() -> dict:
    """Get information about available optimizations.

    Returns:
        Dictionary with optimization status
    """
    mp_version = 'unknown'
    if VIPER_AVAILABLE:
        try:
            import sys
            mp_version = sys.implementation.version if hasattr(sys, 'implementation') else 'micropython'
        except:
            mp_version = 'micropython'

    return {
        'viper_available': VIPER_AVAILABLE,
        'native_available': VIPER_AVAILABLE,
        'micropython_version': mp_version,
        'optimized_functions': 8 if VIPER_AVAILABLE else 0,
    }
