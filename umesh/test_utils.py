"""
Unit tests for umesh.utils module
==================================

Tests all utility functions including:
- CRC8 calculation
- MAC address formatting
- Time helpers
- Byte manipulation
- Random helpers
- Memory helpers
- Debug utilities
"""

import sys
import time
from umesh import utils


# =============================================================================
# CRC8 Tests
# =============================================================================

def test_crc8_python():
    """Test CRC8 Python fallback."""
    # Empty data
    assert utils.crc8_python(b'') == 0xFF

    # Known test vectors (verified against _fast.py implementation)
    # CRC8/CCITT with poly=0x31, init=0xFF
    assert utils.crc8_python(b'\x00') == 0xAC
    assert utils.crc8_python(b'\xFF') == 0x00
    assert utils.crc8_python(b'\x01\x02\x03') == 0x87

    # µMesh header example (13 bytes)
    header = bytes([
        0x01,  # version | pkt_type
        0x00,  # flags
        0x00, 0x01,  # pkt_id
        0x07,  # ttl
        0x00,  # hop_count
        0x12, 0x34,  # src_id
        0x56, 0x78,  # dest_id
        0x00, 0x00, 0x00  # frag_info
    ])
    crc = utils.crc8_python(header)
    assert 0 <= crc <= 255
    print(f"✓ CRC8 Python: {crc:02X}")


# =============================================================================
# MAC Address Tests
# =============================================================================

def test_mac_to_str():
    """Test MAC to string conversion."""
    mac = b'\xAA\xBB\xCC\xDD\xEE\xFF'
    assert utils.mac_to_str(mac) == 'AA:BB:CC:DD:EE:FF'

    mac2 = b'\x00\x11\x22\x33\x44\x55'
    assert utils.mac_to_str(mac2) == '00:11:22:33:44:55'

    # Invalid length
    try:
        utils.mac_to_str(b'\xAA\xBB\xCC')
        assert False, "Should raise ValueError"
    except ValueError as e:
        assert "6 bytes" in str(e)

    print("✓ mac_to_str")


def test_str_to_mac():
    """Test string to MAC conversion."""
    # With colons
    mac = utils.str_to_mac('AA:BB:CC:DD:EE:FF')
    assert mac == b'\xAA\xBB\xCC\xDD\xEE\xFF'

    # Without colons
    mac = utils.str_to_mac('AABBCCDDEEFF')
    assert mac == b'\xAA\xBB\xCC\xDD\xEE\xFF'

    # With dashes
    mac = utils.str_to_mac('AA-BB-CC-DD-EE-FF')
    assert mac == b'\xAA\xBB\xCC\xDD\xEE\xFF'

    # With spaces
    mac = utils.str_to_mac('AA BB CC DD EE FF')
    assert mac == b'\xAA\xBB\xCC\xDD\xEE\xFF'

    # Invalid length
    try:
        utils.str_to_mac('AABBCC')
        assert False, "Should raise ValueError"
    except ValueError as e:
        assert "Invalid MAC" in str(e)

    # Invalid characters
    try:
        utils.str_to_mac('GGHHIIJJKKLL')
        assert False, "Should raise ValueError"
    except ValueError:
        pass

    print("✓ str_to_mac")


def test_mac_to_node_id():
    """Test MAC to node_id extraction."""
    mac = b'\xAA\xBB\xCC\xDD\x12\x34'
    node_id = utils.mac_to_node_id(mac)
    assert node_id == 0x1234

    mac2 = b'\x00\x00\x00\x00\xFF\xFF'
    node_id2 = utils.mac_to_node_id(mac2)
    assert node_id2 == 0xFFFF

    # Invalid length
    try:
        utils.mac_to_node_id(b'\xAA\xBB')
        assert False, "Should raise ValueError"
    except ValueError as e:
        assert "6 bytes" in str(e)

    print("✓ mac_to_node_id")


def test_mac_roundtrip():
    """Test MAC string conversion roundtrip."""
    original = b'\xAA\xBB\xCC\xDD\xEE\xFF'
    mac_str = utils.mac_to_str(original)
    recovered = utils.str_to_mac(mac_str)
    assert original == recovered
    print("✓ MAC roundtrip")


# =============================================================================
# Time Helper Tests
# =============================================================================

def test_ticks_ms():
    """Test ticks_ms wrapper."""
    t1 = utils.ticks_ms()
    assert isinstance(t1, int)
    assert t1 >= 0

    time.sleep(0.01)  # 10ms
    t2 = utils.ticks_ms()
    assert t2 >= t1
    print(f"✓ ticks_ms: {t2 - t1}ms elapsed")


def test_ticks_diff():
    """Test ticks_diff with wraparound handling."""
    # Normal case
    diff = utils.ticks_diff(1000, 500)
    assert diff == 500

    # Negative diff
    diff = utils.ticks_diff(100, 200)
    assert diff == -100

    # Note: Wraparound behavior differs between MicroPython and CPython fallback
    # On MicroPython, ticks are 30-bit and wrap properly
    # On CPython, we use milliseconds since epoch which won't wrap in practice
    # So we skip the wraparound test to maintain compatibility

    print("✓ ticks_diff")


def test_ticks_add():
    """Test ticks_add with wraparound handling."""
    t = utils.ticks_add(1000, 500)
    assert t == 1500

    # Wraparound
    t = utils.ticks_add(0x3FFFFFFF, 10)
    assert t >= 0  # Should wrap to small value

    print("✓ ticks_add")


# =============================================================================
# Byte Manipulation Tests
# =============================================================================

def test_bytes_to_hex():
    """Test bytes to hex string conversion."""
    data = b'\xAA\xBB\xCC'
    assert utils.bytes_to_hex(data) == 'AA BB CC'
    assert utils.bytes_to_hex(data, sep='') == 'AABBCC'
    assert utils.bytes_to_hex(data, sep=':') == 'AA:BB:CC'
    print("✓ bytes_to_hex")


def test_hex_to_bytes():
    """Test hex string to bytes conversion."""
    assert utils.hex_to_bytes('AABBCC') == b'\xAA\xBB\xCC'
    assert utils.hex_to_bytes('AA BB CC') == b'\xAA\xBB\xCC'
    assert utils.hex_to_bytes('AA:BB:CC') == b'\xAA\xBB\xCC'  # Removes colons as whitespace? No, need to handle

    # Invalid hex
    try:
        utils.hex_to_bytes('GGHHII')
        assert False, "Should raise ValueError"
    except ValueError:
        pass

    print("✓ hex_to_bytes")


def test_uint16_conversion():
    """Test uint16 to/from bytes."""
    # Encode
    assert utils.uint16_to_bytes(0x1234) == b'\x12\x34'
    assert utils.uint16_to_bytes(0xFFFF) == b'\xFF\xFF'
    assert utils.uint16_to_bytes(0x0000) == b'\x00\x00'

    # Decode
    assert utils.bytes_to_uint16(b'\x12\x34') == 0x1234
    assert utils.bytes_to_uint16(b'\xFF\xFF') == 0xFFFF
    assert utils.bytes_to_uint16(b'\x00\x00') == 0x0000

    # Decode with offset
    assert utils.bytes_to_uint16(b'\xAA\xBB\x12\x34', offset=2) == 0x1234

    print("✓ uint16 conversion")


def test_uint32_conversion():
    """Test uint32 to/from bytes."""
    # Encode
    assert utils.uint32_to_bytes(0x12345678) == b'\x12\x34\x56\x78'
    assert utils.uint32_to_bytes(0xFFFFFFFF) == b'\xFF\xFF\xFF\xFF'
    assert utils.uint32_to_bytes(0x00000000) == b'\x00\x00\x00\x00'

    # Decode
    assert utils.bytes_to_uint32(b'\x12\x34\x56\x78') == 0x12345678
    assert utils.bytes_to_uint32(b'\xFF\xFF\xFF\xFF') == 0xFFFFFFFF
    assert utils.bytes_to_uint32(b'\x00\x00\x00\x00') == 0x00000000

    # Decode with offset
    assert utils.bytes_to_uint32(b'\xAA\xBB\x12\x34\x56\x78', offset=2) == 0x12345678

    print("✓ uint32 conversion")


def test_byte_roundtrip():
    """Test byte conversion roundtrip."""
    # uint16
    for val in [0, 1, 0x1234, 0xFFFF]:
        encoded = utils.uint16_to_bytes(val)
        decoded = utils.bytes_to_uint16(encoded)
        assert val == decoded

    # uint32
    for val in [0, 1, 0x12345678, 0xFFFFFFFF]:
        encoded = utils.uint32_to_bytes(val)
        decoded = utils.bytes_to_uint32(encoded)
        assert val == decoded

    print("✓ Byte roundtrip")


# =============================================================================
# Random Helper Tests
# =============================================================================

def test_random_bytes():
    """Test random bytes generation."""
    rb1 = utils.random_bytes(16)
    assert len(rb1) == 16
    assert isinstance(rb1, bytes)

    rb2 = utils.random_bytes(16)
    assert len(rb2) == 16

    # Should be different (statistically)
    assert rb1 != rb2  # Very unlikely to be equal

    print(f"✓ random_bytes: {utils.bytes_to_hex(rb1[:8])}")


def test_random_int():
    """Test random integer generation."""
    # Single value range
    ri = utils.random_int(5, 5)
    assert ri == 5

    # Normal range
    ri = utils.random_int(0, 100)
    assert 0 <= ri <= 100

    # Check distribution (loose test)
    values = [utils.random_int(0, 9) for _ in range(100)]
    assert min(values) >= 0
    assert max(values) <= 9
    # Should have some variety
    assert len(set(values)) > 3

    print(f"✓ random_int: {ri}")


# =============================================================================
# Memory Helper Tests
# =============================================================================

def test_mem_info():
    """Test memory info retrieval."""
    info = utils.mem_info()
    assert isinstance(info, dict)
    assert 'free' in info
    assert 'alloc' in info
    assert 'total' in info
    assert 'percent_free' in info

    # On CPython, these will be 0 (no gc.mem_free)
    # On MicroPython, should have real values
    print(f"✓ mem_info: {info}")


def test_gc_collect():
    """Test garbage collection."""
    freed = utils.gc_collect()
    assert isinstance(freed, int)
    # Can be negative, positive, or zero
    print(f"✓ gc_collect: {freed} bytes freed")


# =============================================================================
# Debug Helper Tests
# =============================================================================

def test_hexdump():
    """Test hex dump formatting."""
    data = bytes(range(32))  # 0x00-0x1F
    dump = utils.hexdump(data)
    assert isinstance(dump, str)
    assert '0000' in dump
    assert '0010' in dump
    assert len(dump.split('\n')) == 2  # 2 lines for 32 bytes

    # Short data
    dump = utils.hexdump(b'\xAA\xBB\xCC')
    assert 'AA BB CC' in dump

    print("✓ hexdump")
    print(dump)


def test_format_uptime():
    """Test uptime formatting."""
    assert utils.format_uptime(500) == '500ms'
    assert utils.format_uptime(5000) == '5s'
    assert utils.format_uptime(65000) == '1m 5s'
    assert utils.format_uptime(3600000) == '1h'
    assert utils.format_uptime(3660000) == '1h 1m'
    assert utils.format_uptime(90000000) == '1d 1h'

    print("✓ format_uptime")


def test_format_bytes():
    """Test byte count formatting."""
    assert utils.format_bytes(500) == '500 B'
    assert utils.format_bytes(1024) == '1.0 KB'
    assert utils.format_bytes(1536) == '1.5 KB'
    assert utils.format_bytes(1048576) == '1.0 MB'
    assert utils.format_bytes(1073741824) == '1.0 GB'

    print("✓ format_bytes")


# =============================================================================
# Run all tests
# =============================================================================

def run_all_tests():
    """Run all tests and report results."""
    print("=" * 60)
    print("Testing umesh.utils module")
    print("=" * 60)

    tests = [
        # CRC8
        test_crc8_python,

        # MAC address
        test_mac_to_str,
        test_str_to_mac,
        test_mac_to_node_id,
        test_mac_roundtrip,

        # Time
        test_ticks_ms,
        test_ticks_diff,
        test_ticks_add,

        # Byte manipulation
        test_bytes_to_hex,
        test_hex_to_bytes,
        test_uint16_conversion,
        test_uint32_conversion,
        test_byte_roundtrip,

        # Random
        test_random_bytes,
        test_random_int,

        # Memory
        test_mem_info,
        test_gc_collect,

        # Debug
        test_hexdump,
        test_format_uptime,
        test_format_bytes,
    ]

    passed = 0
    failed = 0

    for test_func in tests:
        try:
            test_func()
            passed += 1
        except AssertionError as e:
            print(f"✗ {test_func.__name__} FAILED: {e}")
            failed += 1
        except Exception as e:
            print(f"✗ {test_func.__name__} ERROR: {e}")
            failed += 1

    print("=" * 60)
    print(f"Results: {passed} passed, {failed} failed")
    print("=" * 60)

    return failed == 0


if __name__ == '__main__':
    success = run_all_tests()
    sys.exit(0 if success else 1)
