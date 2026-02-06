"""
µMesh Utility Functions
=======================

Helper functions and fallbacks for the µMesh protocol.

This module provides:
- CRC8 pure Python fallback (when viper is unavailable)
- MAC address formatting and conversion
- Time helpers with CPython fallbacks
- Byte manipulation utilities
- Random number generation
- Memory helpers
- Debug utilities (hexdump)
"""

# =============================================================================
# Platform detection and fallbacks
# =============================================================================

try:
    import micropython
    MICROPYTHON = True
except ImportError:
    MICROPYTHON = False

try:
    import time
    # Check if MicroPython time functions are available
    if not hasattr(time, 'ticks_ms'):
        # Fallback for CPython testing
        import time as _time

        def _ticks_ms():
            """CPython fallback for ticks_ms (30-bit wraparound)."""
            return int(_time.time() * 1000) & 0x3FFFFFFF

        def _ticks_diff(end, start):
            """CPython fallback for ticks_diff (handles 30-bit wraparound)."""
            diff = (end - start) & 0x3FFFFFFF
            if diff >= 0x20000000:
                diff -= 0x40000000
            return diff

        def _ticks_add(ticks, delta):
            """CPython fallback for ticks_add (30-bit wraparound)."""
            return (ticks + delta) & 0x3FFFFFFF

        def _sleep_ms(ms):
            """CPython fallback for sleep_ms."""
            _time.sleep(ms / 1000.0)

        time.ticks_ms = _ticks_ms
        time.ticks_diff = _ticks_diff
        time.ticks_add = _ticks_add
        time.sleep_ms = _sleep_ms

    # Ensure ticks_add is available
    if not hasattr(time, 'ticks_add'):
        def _ticks_add(ticks, delta):
            """CPython fallback for ticks_add (30-bit wraparound)."""
            return (ticks + delta) & 0x3FFFFFFF
        time.ticks_add = _ticks_add
except ImportError:
    import time


# =============================================================================
# CRC8 Python fallback (for platforms without viper)
# =============================================================================

def crc8_python(data: bytes) -> int:
    """CRC8/CCITT calculation in pure Python.

    Polynomial: 0x31 (x^8 + x^5 + x^4 + 1)
    Initial: 0xFF

    This is the fallback when viper is not available.
    Use _fast._crc8() for optimal performance on MicroPython.

    Args:
        data: Bytes to checksum

    Returns:
        CRC8 checksum (0-255)
    """
    crc = 0xFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 0x80:
                crc = ((crc << 1) ^ 0x31) & 0xFF
            else:
                crc = (crc << 1) & 0xFF
    return crc


# =============================================================================
# MAC address formatting
# =============================================================================

def mac_to_str(mac: bytes) -> str:
    """Convert 6-byte MAC to string format 'AA:BB:CC:DD:EE:FF'.

    Args:
        mac: 6-byte MAC address

    Returns:
        Formatted MAC string with colon separators

    Raises:
        ValueError: If MAC is not 6 bytes
    """
    if len(mac) != 6:
        raise ValueError(f"MAC must be 6 bytes, got {len(mac)}")
    return ':'.join('{:02X}'.format(b) for b in mac)


def str_to_mac(mac_str: str) -> bytes:
    """Convert string 'AA:BB:CC:DD:EE:FF' to 6-byte MAC.

    Accepts with or without colons/dashes/spaces as separators.

    Args:
        mac_str: MAC address string

    Returns:
        6-byte MAC address

    Raises:
        ValueError: If string format is invalid
    """
    # Remove separators
    clean = mac_str.replace(':', '').replace('-', '').replace(' ', '')
    if len(clean) != 12:
        raise ValueError(f"Invalid MAC address length: {mac_str}")
    try:
        return bytes.fromhex(clean)
    except ValueError as e:
        raise ValueError(f"Invalid MAC address format: {mac_str}") from e


def mac_to_node_id(mac: bytes) -> int:
    """Extract node_id from MAC address (last 2 bytes as uint16).

    This is the standard µMesh convention for auto node_id derivation.

    Args:
        mac: 6-byte MAC address

    Returns:
        16-bit node ID (mac[4] << 8 | mac[5])

    Raises:
        ValueError: If MAC is not 6 bytes
    """
    if len(mac) != 6:
        raise ValueError(f"MAC must be 6 bytes, got {len(mac)}")
    return (mac[4] << 8) | mac[5]


# =============================================================================
# Time helpers (wrappers for ticks functions)
# =============================================================================

def ticks_ms() -> int:
    """Get current time in milliseconds.

    Uses time.ticks_ms() on MicroPython with automatic fallback to CPython.
    Wraps every ~49 days on MicroPython (30 bits), use ticks_diff() for durations.

    Returns:
        Current time in milliseconds
    """
    return time.ticks_ms()


def ticks_diff(end: int, start: int) -> int:
    """Calculate time difference handling wraparound.

    Always use this for duration calculations, never subtract ticks directly.

    Args:
        end: End time from ticks_ms()
        start: Start time from ticks_ms()

    Returns:
        Time difference in milliseconds (can be negative if end < start)
    """
    return time.ticks_diff(end, start)


def ticks_add(ticks: int, delta: int) -> int:
    """Add delta to ticks handling wraparound.

    Args:
        ticks: Current ticks value
        delta: Milliseconds to add

    Returns:
        New ticks value with wraparound handling
    """
    return time.ticks_add(ticks, delta)


# =============================================================================
# Byte manipulation helpers
# =============================================================================

def bytes_to_hex(data: bytes, sep: str = ' ') -> str:
    """Convert bytes to hex string with separator.

    Args:
        data: Bytes to convert
        sep: Separator between bytes (default: space)

    Returns:
        Hex string (e.g., "AA BB CC" with sep=' ')
    """
    return sep.join('{:02X}'.format(b) for b in data)


def hex_to_bytes(hex_str: str) -> bytes:
    """Convert hex string to bytes (removes whitespace and separators).

    Args:
        hex_str: Hex string (e.g., "AABBCC", "AA BB CC", "AA:BB:CC")

    Returns:
        Bytes object

    Raises:
        ValueError: If hex string is invalid
    """
    # Remove whitespace and common separators
    clean = hex_str.replace(':', '').replace('-', '').replace(' ', '')
    try:
        return bytes.fromhex(clean)
    except ValueError as e:
        raise ValueError(f"Invalid hex string: {hex_str}") from e


def uint16_to_bytes(value: int) -> bytes:
    """Convert uint16 to 2 bytes (big-endian).

    Args:
        value: Integer (0-65535)

    Returns:
        2-byte big-endian representation
    """
    return bytes([(value >> 8) & 0xFF, value & 0xFF])


def bytes_to_uint16(data: bytes, offset: int = 0) -> int:
    """Extract uint16 from bytes at offset (big-endian).

    Args:
        data: Bytes to read from
        offset: Byte offset (default: 0)

    Returns:
        16-bit unsigned integer

    Raises:
        IndexError: If offset is out of bounds
    """
    return (data[offset] << 8) | data[offset + 1]


def uint32_to_bytes(value: int) -> bytes:
    """Convert uint32 to 4 bytes (big-endian).

    Args:
        value: Integer (0-4294967295)

    Returns:
        4-byte big-endian representation
    """
    return bytes([
        (value >> 24) & 0xFF,
        (value >> 16) & 0xFF,
        (value >> 8) & 0xFF,
        value & 0xFF
    ])


def bytes_to_uint32(data: bytes, offset: int = 0) -> int:
    """Extract uint32 from bytes at offset (big-endian).

    Args:
        data: Bytes to read from
        offset: Byte offset (default: 0)

    Returns:
        32-bit unsigned integer

    Raises:
        IndexError: If offset is out of bounds
    """
    return ((data[offset] << 24) |
            (data[offset + 1] << 16) |
            (data[offset + 2] << 8) |
            data[offset + 3])


# =============================================================================
# Random helpers
# =============================================================================

def random_bytes(n: int) -> bytes:
    """Generate n random bytes.

    Uses os.urandom() on platforms that support it, otherwise random module.

    Args:
        n: Number of random bytes to generate

    Returns:
        n random bytes
    """
    try:
        import os
        return os.urandom(n)
    except (ImportError, AttributeError):
        # Fallback using random module
        import random
        return bytes(random.getrandbits(8) for _ in range(n))


def random_int(min_val: int, max_val: int) -> int:
    """Generate random integer in range [min_val, max_val].

    Args:
        min_val: Minimum value (inclusive)
        max_val: Maximum value (inclusive)

    Returns:
        Random integer in range
    """
    try:
        import random
        return random.randint(min_val, max_val)
    except ImportError:
        # Emergency fallback using os.urandom
        import os
        r = int.from_bytes(os.urandom(4), 'big')
        return min_val + (r % (max_val - min_val + 1))


# =============================================================================
# Memory helpers
# =============================================================================

def mem_info() -> dict:
    """Get memory information.

    Returns dict with 'free', 'alloc', 'total', 'percent_free' keys (in bytes).
    Returns zeros on platforms without gc.mem_free() support.

    Returns:
        Dictionary with memory statistics:
        - free: Free RAM in bytes
        - alloc: Allocated RAM in bytes
        - total: Total RAM in bytes
        - percent_free: Free memory percentage (0-100)
    """
    try:
        import gc
        gc.collect()
        free = gc.mem_free()
        alloc = gc.mem_alloc()
        return {
            'free': free,
            'alloc': alloc,
            'total': free + alloc,
            'percent_free': (free * 100) // (free + alloc)
        }
    except (ImportError, AttributeError):
        return {'free': 0, 'alloc': 0, 'total': 0, 'percent_free': 0}


def gc_collect() -> int:
    """Run garbage collection, return bytes freed.

    Returns:
        Bytes freed by garbage collection (0 if gc unavailable)
    """
    try:
        import gc
        before = gc.mem_free()
        gc.collect()
        return gc.mem_free() - before
    except (ImportError, AttributeError):
        return 0


# =============================================================================
# Debug helpers
# =============================================================================

def hexdump(data: bytes, width: int = 16) -> str:
    """Create hex dump of data for debugging.

    Format:
        0000  41 42 43 44 45 46 47 48  49 4A 4B 4C 4D 4E 4F 50  ABCDEFGHIJKLMNOP
        0010  51 52 53 54 55 56 57 58  59 5A                    QRSTUVWXYZ

    Args:
        data: Bytes to dump
        width: Bytes per line (default: 16)

    Returns:
        Formatted hex dump string
    """
    lines = []
    for i in range(0, len(data), width):
        chunk = data[i:i+width]
        # Hex part with spacing every 8 bytes
        hex_parts = []
        for j in range(0, len(chunk), 8):
            hex_parts.append(' '.join('{:02X}'.format(b) for b in chunk[j:j+8]))
        hex_part = '  '.join(hex_parts)

        # ASCII part
        ascii_part = ''.join(chr(b) if 32 <= b < 127 else '.' for b in chunk)

        # Pad hex part to fixed width
        hex_width = width * 3 + (width // 8 - 1) * 2  # 3 chars per byte + spacing
        lines.append('{:04X}  {:{}s}  {}'.format(i, hex_part, hex_width, ascii_part))

    return '\n'.join(lines)


def format_uptime(ms: int) -> str:
    """Format milliseconds as human-readable uptime string.

    Args:
        ms: Milliseconds

    Returns:
        Formatted string (e.g., "1d 2h 3m 4s" or "5m 30s")
    """
    if ms < 1000:
        return f"{ms}ms"

    s = ms // 1000
    if s < 60:
        return f"{s}s"

    m = s // 60
    s = s % 60
    if m < 60:
        return f"{m}m {s}s" if s > 0 else f"{m}m"

    h = m // 60
    m = m % 60
    if h < 24:
        return f"{h}h {m}m" if m > 0 else f"{h}h"

    d = h // 24
    h = h % 24
    return f"{d}d {h}h" if h > 0 else f"{d}d"


def format_bytes(n: int) -> str:
    """Format byte count as human-readable string.

    Args:
        n: Number of bytes

    Returns:
        Formatted string (e.g., "1.5 KB", "2.3 MB")
    """
    if n < 1024:
        return f"{n} B"
    elif n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    elif n < 1024 * 1024 * 1024:
        return f"{n / (1024 * 1024):.1f} MB"
    else:
        return f"{n / (1024 * 1024 * 1024):.1f} GB"


# =============================================================================
# Exports
# =============================================================================

__all__ = [
    # CRC
    'crc8_python',

    # MAC address
    'mac_to_str',
    'str_to_mac',
    'mac_to_node_id',

    # Time
    'ticks_ms',
    'ticks_diff',
    'ticks_add',

    # Byte manipulation
    'bytes_to_hex',
    'hex_to_bytes',
    'uint16_to_bytes',
    'bytes_to_uint16',
    'uint32_to_bytes',
    'bytes_to_uint32',

    # Random
    'random_bytes',
    'random_int',

    # Memory
    'mem_info',
    'gc_collect',

    # Debug
    'hexdump',
    'format_uptime',
    'format_bytes',
]
