"""
µMesh Cryptography Example
===========================

Demonstrates AES-128 CTR mode encryption for mesh payloads.

This example shows:
1. Key generation and derivation
2. Basic encryption/decryption
3. Integration with MeshConfig
4. Security best practices
"""

import sys
import os

# Add parent directory for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from umesh.crypto import MeshCrypto, is_encrypted_payload, NONCE_SIZE
from umesh import MeshConfig


def example_basic_encryption():
    """Basic encryption and decryption."""
    print("=" * 60)
    print("Example 1: Basic Encryption/Decryption")
    print("=" * 60)

    # Create crypto instance with 16-byte PSK
    psk = b'MySecretKey12345'  # Must be exactly 16 bytes
    crypto = MeshCrypto(psk)

    # Encrypt a message
    plaintext = b'Hello, secure mesh network!'
    node_id = 0x1234
    pkt_id = 42

    encrypted = crypto.encrypt(plaintext, node_id, pkt_id)

    print(f"Plaintext:  {plaintext}")
    print(f"Plaintext length: {len(plaintext)} bytes")
    print(f"Encrypted:  {encrypted.hex()}")
    print(f"Encrypted length: {len(encrypted)} bytes")
    print(f"Overhead:   {len(encrypted) - len(plaintext)} bytes (nonce)")
    print(f"Is encrypted? {is_encrypted_payload(encrypted)}")

    # Extract and display nonce
    nonce = encrypted[:NONCE_SIZE]
    print(f"\nNonce structure:")
    print(f"  Bytes 0-1 (node_id): 0x{nonce[0]:02x}{nonce[1]:02x} = {int.from_bytes(nonce[0:2], 'big')}")
    print(f"  Bytes 2-3 (pkt_id):  0x{nonce[2]:02x}{nonce[3]:02x} = {int.from_bytes(nonce[2:4], 'big')}")
    print(f"  Bytes 4-7 (random):  {nonce[4:8].hex()}")

    # Decrypt
    decrypted = crypto.decrypt(encrypted)
    print(f"\nDecrypted:  {decrypted}")
    print(f"Match:      {decrypted == plaintext}")
    print()


def example_key_generation():
    """Random key generation."""
    print("=" * 60)
    print("Example 2: Random Key Generation")
    print("=" * 60)

    # Generate random key (best practice for production)
    key1 = MeshCrypto.generate_key()
    key2 = MeshCrypto.generate_key()

    print(f"Random key 1: {key1.hex()}")
    print(f"Random key 2: {key2.hex()}")
    print(f"Keys differ: {key1 != key2} (statistically guaranteed)")

    # Use generated key
    crypto = MeshCrypto(key1)
    encrypted = crypto.encrypt(b'Test message', node_id=0x0001, pkt_id=1)
    decrypted = crypto.decrypt(encrypted)

    print(f"\nEncryption with generated key works: {decrypted == b'Test message'}")
    print()


def example_key_derivation():
    """Password-based key derivation."""
    print("=" * 60)
    print("Example 3: Password-Based Key Derivation")
    print("=" * 60)

    # Derive key from password (convenient but less secure)
    password = "my-super-secret-password-with-high-entropy"

    # Without salt (different key each time - non-deterministic)
    key1 = MeshCrypto.derive_key(password)
    key2 = MeshCrypto.derive_key(password)
    print(f"Derived key 1: {key1.hex()}")
    print(f"Derived key 2: {key2.hex()}")
    print(f"Without salt, keys differ: {key1 != key2} (random salt used)")

    # With fixed salt (same key each time - deterministic)
    salt = b'FixedSalt1234567'  # 16 bytes
    key3 = MeshCrypto.derive_key(password, salt=salt)
    key4 = MeshCrypto.derive_key(password, salt=salt)
    print(f"\nDerived key 3: {key3.hex()}")
    print(f"Derived key 4: {key4.hex()}")
    print(f"With salt, keys match: {key3 == key4}")

    # Use derived key
    crypto = MeshCrypto(key3)
    encrypted = crypto.encrypt(b'Test', node_id=0x0002, pkt_id=1)
    decrypted = crypto.decrypt(encrypted)
    print(f"\nEncryption with derived key works: {decrypted == b'Test'}")
    print()


def example_mesh_config_integration():
    """Integration with MeshConfig."""
    print("=" * 60)
    print("Example 4: Integration with MeshConfig")
    print("=" * 60)

    # Generate PSK
    psk = MeshCrypto.generate_key()
    print(f"Generated PSK: {psk.hex()}")

    # Create mesh config with encryption enabled
    config = MeshConfig(
        channel=6,
        enable_encryption=True,
        psk=psk,
    )

    print(f"\nMeshConfig created:")
    print(f"  Encryption enabled: {config.enable_encryption}")
    print(f"  PSK length: {len(config.psk)} bytes")

    # Validate configuration
    try:
        config.validate()
        print(f"  Configuration valid: True")
    except ValueError as e:
        print(f"  Configuration error: {e}")

    # Show what happens with invalid PSK
    try:
        invalid_config = MeshConfig(
            enable_encryption=True,
            psk=b'short',  # Only 5 bytes - INVALID
        )
    except ValueError as e:
        print(f"\nInvalid PSK rejected: {e}")

    print()


def example_different_payload_sizes():
    """Test various payload sizes."""
    print("=" * 60)
    print("Example 5: Various Payload Sizes")
    print("=" * 60)

    psk = b'TestKey123456789'
    crypto = MeshCrypto(psk)

    sizes = [0, 1, 16, 100, 236]  # 236 = max mesh payload

    for size in sizes:
        data = b'X' * size
        encrypted = crypto.encrypt(data, node_id=0x1234, pkt_id=1)
        decrypted = crypto.decrypt(encrypted)

        overhead = len(encrypted) - len(data)
        print(f"Payload {size:3d} bytes -> encrypted {len(encrypted):3d} bytes (overhead: {overhead} bytes)")

        assert decrypted == data, f"Roundtrip failed for {size}-byte payload"

    print("\nMax encrypted payload for mesh:")
    max_payload = 236
    print(f"  Max unencrypted: {max_payload} bytes")
    print(f"  After encryption: {max_payload + NONCE_SIZE} bytes (still under 250-byte ESP-NOW limit)")
    print()


def example_ctr_mode_properties():
    """Demonstrate CTR mode properties."""
    print("=" * 60)
    print("Example 6: CTR Mode Properties")
    print("=" * 60)

    psk = b'TestKey123456789'
    crypto = MeshCrypto(psk)

    # 1. Same plaintext with different nonces produces different ciphertexts
    plaintext = b'Same message'
    enc1 = crypto.encrypt(plaintext, node_id=0x1234, pkt_id=1)
    enc2 = crypto.encrypt(plaintext, node_id=0x1234, pkt_id=2)  # Different pkt_id
    enc3 = crypto.encrypt(plaintext, node_id=0x5678, pkt_id=1)  # Different node_id

    print("Same plaintext with different nonces:")
    print(f"  Ciphertext 1: {enc1[NONCE_SIZE:].hex()}")
    print(f"  Ciphertext 2: {enc2[NONCE_SIZE:].hex()}")
    print(f"  Ciphertext 3: {enc3[NONCE_SIZE:].hex()}")
    print(f"  All different: {enc1 != enc2 and enc1 != enc3 and enc2 != enc3}")

    # 2. CTR mode is a stream cipher (bit flip property)
    data = b'AAAA'  # 0x41 = 01000001
    encrypted = crypto.encrypt(data, node_id=0x1234, pkt_id=42)

    # Flip a bit in ciphertext
    corrupted = bytearray(encrypted)
    corrupted[NONCE_SIZE] ^= 0x01  # Flip LSB of first ciphertext byte

    decrypted = crypto.decrypt(bytes(corrupted))
    print(f"\nCTR mode bit-flip property:")
    print(f"  Original plaintext: {data.hex()} ({data})")
    print(f"  Corrupted decrypt:  {decrypted.hex()} ({decrypted})")
    print(f"  Expected: 0x40 ('@'), got: 0x{decrypted[0]:02x} ('{chr(decrypted[0])}')")
    print(f"  Demonstrates: No integrity protection (bit flip in ciphertext = bit flip in plaintext)")

    print()


def example_security_considerations():
    """Security best practices and limitations."""
    print("=" * 60)
    print("Example 7: Security Considerations")
    print("=" * 60)

    print("STRENGTHS:")
    print("  [+] Hardware-accelerated AES on ESP32")
    print("  [+] Cryptographically secure random nonces (os.urandom)")
    print("  [+] CTR mode requires no padding (stream cipher)")
    print("  [+] Simple, well-understood algorithm")

    print("\nLIMITATIONS:")
    print("  [-] No authentication/integrity (CTR provides confidentiality only)")
    print("  [-] Vulnerable to bit-flipping attacks")
    print("  [-] Shared PSK across mesh (compromise one node = compromise network)")
    print("  [-] No forward secrecy")
    print("  [-] Simple KDF (not PBKDF2/Argon2 strength)")

    print("\nBEST PRACTICES:")
    print("  [!] Use MeshCrypto.generate_key() for strong random keys")
    print("  [!] Avoid weak passwords in derive_key() (use 20+ characters)")
    print("  [!] Consider adding HMAC for critical applications")
    print("  [!] Rotate PSK periodically")
    print("  [!] Store PSK securely (not in source code)")
    print("  [!] Some packets may be unencrypted (e.g., HELLO beacons)")

    print("\nRECOMMENDED ENHANCEMENT (not implemented):")
    print("  Add HMAC for authenticated encryption:")

    print("""
    import hashlib

    def encrypt_with_auth(crypto, data, node_id, pkt_id, key):
        encrypted = crypto.encrypt(data, node_id, pkt_id)
        mac = hashlib.sha256(key + encrypted).digest()[:8]
        return encrypted + mac  # 8-byte HMAC appended

    def decrypt_with_auth(crypto, data, key):
        if len(data) < 8:
            raise ValueError("Missing HMAC")
        encrypted = data[:-8]
        received_mac = data[-8:]
        expected_mac = hashlib.sha256(key + encrypted).digest()[:8]
        if received_mac != expected_mac:
            raise ValueError("HMAC verification failed")
        return crypto.decrypt(encrypted)
    """)

    print()


def main():
    """Run all examples."""
    print("\nµMesh Cryptography Examples")
    print("=" * 60)
    print()

    example_basic_encryption()
    example_key_generation()
    example_key_derivation()
    example_mesh_config_integration()
    example_different_payload_sizes()
    example_ctr_mode_properties()
    example_security_considerations()

    print("=" * 60)
    print("All examples completed successfully!")
    print("=" * 60)


if __name__ == '__main__':
    main()
