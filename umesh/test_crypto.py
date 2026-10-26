"""
Unit tests for umesh/crypto.py

Tests AES-128 CTR mode encryption for mesh payloads.
"""

import sys
import os

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from umesh.crypto import (
    MeshCrypto,
    is_encrypted_payload,
    NONCE_SIZE,
    KEY_SIZE,
    BLOCK_SIZE,
)


def test_constants():
    """Test module constants."""
    assert NONCE_SIZE == 8
    assert KEY_SIZE == 16
    assert BLOCK_SIZE == 16
    print("[PASS] Constants correct")


def test_init_valid():
    """Test initialization with valid PSK."""
    psk = b'0123456789ABCDEF'  # 16 bytes
    crypto = MeshCrypto(psk)
    assert crypto._psk == psk
    print("[PASS] Valid initialization")


def test_init_invalid_type():
    """Test initialization rejects non-bytes PSK."""
    try:
        MeshCrypto("not bytes")
        assert False, "Should raise ValueError for non-bytes PSK"
    except ValueError as e:
        assert "must be bytes" in str(e)
    print("[PASS] Rejects non-bytes PSK")


def test_init_invalid_length():
    """Test initialization rejects wrong-length PSK."""
    try:
        MeshCrypto(b'short')  # Only 5 bytes
        assert False, "Should raise ValueError for short PSK"
    except ValueError as e:
        assert "16 bytes" in str(e)

    try:
        MeshCrypto(b'0123456789ABCDEF!' * 2)  # 34 bytes
        assert False, "Should raise ValueError for long PSK"
    except ValueError as e:
        assert "16 bytes" in str(e)

    print("[PASS] Rejects invalid PSK length")


def test_encrypt_decrypt_roundtrip():
    """Test encrypt/decrypt roundtrip."""
    psk = b'TestKey123456789'
    crypto = MeshCrypto(psk)

    # Test various payload sizes
    test_data = [
        b'',                          # Empty
        b'X',                         # 1 byte
        b'Hello, mesh!',              # 12 bytes
        b'A' * 16,                    # Exactly one AES block
        b'B' * 31,                    # Just under 2 blocks
        b'C' * 32,                    # Exactly 2 blocks
        b'D' * 100,                   # Multiple blocks
        b'E' * 236,                   # Max mesh payload
    ]

    for i, data in enumerate(test_data):
        encrypted = crypto.encrypt(data, node_id=0x1234, pkt_id=i)

        # Check encrypted format
        assert len(encrypted) == NONCE_SIZE + len(data), \
            f"Encrypted length mismatch for {len(data)}-byte payload"
        assert encrypted[:NONCE_SIZE] != data[:NONCE_SIZE] or len(data) == 0, \
            "Nonce should differ from plaintext"

        # Decrypt and verify
        decrypted = crypto.decrypt(encrypted)
        assert decrypted == data, f"Roundtrip failed for {len(data)}-byte payload"

    print(f"[PASS] Encrypt/decrypt roundtrip ({len(test_data)} payloads)")


def test_ctr_mode_different_nonces():
    """Test that different nonces produce different ciphertexts."""
    psk = b'TestKey123456789'
    crypto = MeshCrypto(psk)
    data = b'Same plaintext'

    # Encrypt same data with different packet IDs (different nonces)
    enc1 = crypto.encrypt(data, node_id=0x1234, pkt_id=1)
    enc2 = crypto.encrypt(data, node_id=0x1234, pkt_id=2)
    enc3 = crypto.encrypt(data, node_id=0x5678, pkt_id=1)

    # Nonces should differ (due to pkt_id or node_id difference)
    assert enc1[:NONCE_SIZE] != enc2[:NONCE_SIZE], "Different pkt_id → different nonce"
    assert enc1[:NONCE_SIZE] != enc3[:NONCE_SIZE], "Different node_id → different nonce"

    # Ciphertexts should differ (CTR mode with different nonces)
    assert enc1 != enc2, "Same plaintext + different nonce → different ciphertext"
    assert enc1 != enc3, "Same plaintext + different nonce → different ciphertext"

    # But all should decrypt to same plaintext
    assert crypto.decrypt(enc1) == data
    assert crypto.decrypt(enc2) == data
    assert crypto.decrypt(enc3) == data

    print("[PASS] CTR mode uses nonce correctly")


def test_different_keys():
    """Test that different keys produce different results."""
    key1 = b'Key1111111111111'
    key2 = b'Key2222222222222'
    data = b'Test data'

    crypto1 = MeshCrypto(key1)
    crypto2 = MeshCrypto(key2)

    # Same plaintext, different keys
    enc1 = crypto1.encrypt(data, node_id=0x1234, pkt_id=42)
    enc2 = crypto2.encrypt(data, node_id=0x1234, pkt_id=42)

    # Should produce different ciphertexts (different random nonces + keys)
    assert enc1 != enc2, "Different keys should produce different ciphertexts"

    # Decrypt with correct key
    assert crypto1.decrypt(enc1) == data
    assert crypto2.decrypt(enc2) == data

    print("[PASS] Different keys produce different results")


def test_decrypt_invalid_short():
    """Test decrypt rejects too-short data."""
    psk = b'TestKey123456789'
    crypto = MeshCrypto(psk)

    try:
        crypto.decrypt(b'short')  # Only 5 bytes, need >= 8
        assert False, "Should raise ValueError for short data"
    except ValueError as e:
        assert "too short" in str(e)

    print("[PASS] Rejects short encrypted data")


def test_encrypt_invalid_node_id():
    """Test encrypt validates node_id range."""
    psk = b'TestKey123456789'
    crypto = MeshCrypto(psk)
    data = b'test'

    # node_id too large
    try:
        crypto.encrypt(data, node_id=0x10000, pkt_id=42)
        assert False, "Should reject node_id > 0xFFFF"
    except ValueError as e:
        assert "node_id" in str(e)

    # node_id negative
    try:
        crypto.encrypt(data, node_id=-1, pkt_id=42)
        assert False, "Should reject negative node_id"
    except ValueError as e:
        assert "node_id" in str(e)

    print("[PASS] Validates node_id range")


def test_encrypt_invalid_pkt_id():
    """Test encrypt validates pkt_id range."""
    psk = b'TestKey123456789'
    crypto = MeshCrypto(psk)
    data = b'test'

    # pkt_id too large
    try:
        crypto.encrypt(data, node_id=0x1234, pkt_id=0x10000)
        assert False, "Should reject pkt_id > 0xFFFF"
    except ValueError as e:
        assert "pkt_id" in str(e)

    # pkt_id negative
    try:
        crypto.encrypt(data, node_id=0x1234, pkt_id=-1)
        assert False, "Should reject negative pkt_id"
    except ValueError as e:
        assert "pkt_id" in str(e)

    print("[PASS] Validates pkt_id range")


def test_nonce_format():
    """Test nonce construction format."""
    psk = b'TestKey123456789'
    crypto = MeshCrypto(psk)

    node_id = 0x1234
    pkt_id = 0x5678
    data = b'test'

    encrypted = crypto.encrypt(data, node_id=node_id, pkt_id=pkt_id)
    nonce = encrypted[:NONCE_SIZE]

    # Check nonce structure
    assert len(nonce) == 8
    assert nonce[0:2] == node_id.to_bytes(2, 'big'), "Nonce bytes 0-1 should be node_id"
    # Bytes 2-7 are random and intentionally no longer encode the packet ID.
    second = crypto.encrypt(data, node_id=node_id, pkt_id=pkt_id)[:NONCE_SIZE]
    assert nonce[2:8] != second[2:8], "Separate packets need independently sampled nonces"

    print("[PASS] Nonce format correct")


def test_generate_key():
    """Test key generation."""
    key1 = MeshCrypto.generate_key()
    key2 = MeshCrypto.generate_key()

    assert len(key1) == KEY_SIZE, "Generated key should be 16 bytes"
    assert len(key2) == KEY_SIZE
    assert key1 != key2, "Generated keys should be different (statistically)"

    # Should be usable for initialization
    crypto = MeshCrypto(key1)
    encrypted = crypto.encrypt(b'test', node_id=0x1234, pkt_id=42)
    assert crypto.decrypt(encrypted) == b'test'

    print("[PASS] Key generation works")


def test_derive_key():
    """Test key derivation from password."""
    try:
        # Test key derivation
        key1 = MeshCrypto.derive_key("my-password")
        key2 = MeshCrypto.derive_key("my-password")
        key3 = MeshCrypto.derive_key("different-password")

        assert len(key1) == KEY_SIZE
        # Note: Same password produces different keys due to random salt
        # To get same key, must use same salt
        assert key1 != key2, "Different salt should produce different keys"
        assert key1 != key3

        # Test with explicit salt
        salt = b'0123456789ABCDEF'
        key4 = MeshCrypto.derive_key("password", salt=salt)
        key5 = MeshCrypto.derive_key("password", salt=salt)
        assert key4 == key5, "Same password + salt should produce same key"

        # Should be usable
        crypto = MeshCrypto(key4)
        encrypted = crypto.encrypt(b'test', node_id=0x1234, pkt_id=42)
        assert crypto.decrypt(encrypted) == b'test'

        print("[PASS] Key derivation works")

    except RuntimeError as e:
        if "hashlib not available" in str(e):
            print("[SKIP] Key derivation skipped (hashlib not available)")
        else:
            raise


def test_derive_key_invalid_salt():
    """Test key derivation rejects invalid salt."""
    try:
        try:
            MeshCrypto.derive_key("password", salt=b'short')
            assert False, "Should reject short salt"
        except ValueError as e:
            assert "16 bytes" in str(e)

        print("[PASS] Key derivation validates salt")

    except RuntimeError as e:
        if "hashlib not available" in str(e):
            print("[SKIP] Key derivation validation skipped (hashlib not available)")
        else:
            raise


def test_is_encrypted_payload():
    """Test encrypted payload detection heuristic."""
    assert not is_encrypted_payload(b''), "Empty should not be encrypted"
    assert not is_encrypted_payload(b'short'), "< 8 bytes not encrypted"
    assert is_encrypted_payload(b'X' * 8), "8 bytes could be encrypted"
    assert is_encrypted_payload(b'X' * 100), "Long payload could be encrypted"

    # Test with actual encrypted data
    psk = b'TestKey123456789'
    crypto = MeshCrypto(psk)
    encrypted = crypto.encrypt(b'test', node_id=0x1234, pkt_id=42)
    assert is_encrypted_payload(encrypted), "Actual encrypted payload should pass"

    print("[PASS] Encrypted payload detection")


def test_overhead():
    """Test encryption overhead is exactly NONCE_SIZE."""
    psk = b'TestKey123456789'
    crypto = MeshCrypto(psk)

    for size in [0, 1, 10, 16, 32, 100, 236]:
        data = b'X' * size
        encrypted = crypto.encrypt(data, node_id=0x1234, pkt_id=42)
        overhead = len(encrypted) - len(data)
        assert overhead == NONCE_SIZE, \
            f"Overhead should be {NONCE_SIZE} bytes, got {overhead} for {size}-byte payload"

    print("[PASS] Encryption overhead is 8 bytes")


def test_max_payload_encrypted():
    """Test encryption of maximum mesh payload."""
    psk = b'TestKey123456789'
    crypto = MeshCrypto(psk)

    # Max unencrypted payload is 236 bytes (250 - 14 header)
    # After encryption: 236 + 8 = 244 bytes (still under 250)
    max_payload = b'X' * 236

    encrypted = crypto.encrypt(max_payload, node_id=0x1234, pkt_id=42)
    assert len(encrypted) == 244, "Encrypted max payload should be 244 bytes"

    decrypted = crypto.decrypt(encrypted)
    assert decrypted == max_payload

    print("[PASS] Max payload encryption works")


def test_ctr_mode_properties():
    """Test CTR mode specific properties."""
    psk = b'TestKey123456789'
    crypto = MeshCrypto(psk)

    # CTR mode should allow parallel encryption/decryption
    # (not tested here, but implementation supports it)

    # Test that flipping a bit in ciphertext flips corresponding bit in plaintext
    data = b'AAAA'  # Binary: 01000001...
    encrypted = crypto.encrypt(data, node_id=0x1234, pkt_id=42)

    # Flip a bit in ciphertext (after nonce)
    corrupted = bytearray(encrypted)
    corrupted[NONCE_SIZE] ^= 0x01  # Flip LSB of first ciphertext byte

    # Decrypt - should have corresponding bit flipped
    decrypted = crypto.decrypt(bytes(corrupted))
    # Original: 'A' = 0x41 = 01000001
    # Flipped:  0x40 = 01000000 = '@'
    assert decrypted[0] == ord('@'), "Bit flip in ciphertext should flip plaintext bit"
    assert decrypted[1:] == data[1:], "Other bytes should be unchanged"

    print("[PASS] CTR mode properties verified")


def run_all_tests():
    """Run all test functions."""
    print("Running crypto module tests...\n")

    tests = [
        test_constants,
        test_init_valid,
        test_init_invalid_type,
        test_init_invalid_length,
        test_encrypt_decrypt_roundtrip,
        test_ctr_mode_different_nonces,
        test_different_keys,
        test_decrypt_invalid_short,
        test_encrypt_invalid_node_id,
        test_encrypt_invalid_pkt_id,
        test_nonce_format,
        test_generate_key,
        test_derive_key,
        test_derive_key_invalid_salt,
        test_is_encrypted_payload,
        test_overhead,
        test_max_payload_encrypted,
        test_ctr_mode_properties,
    ]

    passed = 0
    failed = 0

    for test in tests:
        try:
            test()
            passed += 1
        except AssertionError as e:
            print(f"[FAIL] {test.__name__} failed: {e}")
            failed += 1
        except Exception as e:
            print(f"[FAIL] {test.__name__} error: {e}")
            failed += 1

    print(f"\n{'='*60}")
    print(f"Results: {passed} passed, {failed} failed")
    print(f"{'='*60}")

    return failed == 0


if __name__ == '__main__':
    success = run_all_tests()
    sys.exit(0 if success else 1)
