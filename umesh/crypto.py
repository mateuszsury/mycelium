"""
µMesh Cryptography
==================

AES-128 CTR mode encryption for mesh payloads.

Since ESP-NOW cannot encrypt broadcast packets, we implement
application-layer encryption using AES-128 in Counter (CTR) mode.

CTR mode is implemented manually on top of ECB mode, which is provided
by ucryptolib (built into MicroPython ESP32).

Security properties:
- AES-128 with 16-byte Pre-Shared Key (PSK)
- CTR mode for stream cipher operation (no padding needed)
- Unique nonce per packet: node_id + random(6B)
- 8-byte overhead per encrypted packet

Memory usage:
- Per-packet: 8 bytes (nonce)
- Encryption state: minimal (single AES context)
"""

try:
    from micropython import const
except ImportError:
    # Fallback for non-MicroPython environments
    def const(x):
        return x

try:
    from ucryptolib import aes
    UCRYPTOLIB_AVAILABLE = True
except ImportError:
    # Encryption remains unavailable when no AES backend is present.
    UCRYPTOLIB_AVAILABLE = False
    import warnings
    warnings.warn("ucryptolib not available; encryption will raise until an AES backend is supplied")

try:
    import os
    urandom = os.urandom
except AttributeError:
    # MicroPython uses os.urandom, desktop Python has it too
    import random
    def urandom(n):
        return bytes(random.randint(0, 255) for _ in range(n))

try:
    import hashlib
except ImportError:
    # MicroPython has hashlib.sha256
    hashlib = None


# Encryption constants
NONCE_SIZE = const(8)         # Bytes: node_id(2) + random(6)
KEY_SIZE = const(16)          # AES-128 key size
BLOCK_SIZE = const(16)        # AES block size


class MeshCrypto:
    """
    AES-128 CTR mode encryption for mesh payloads.

    CTR mode is built on top of ECB mode using ucryptolib.

    Example:
        >>> crypto = MeshCrypto(b'0123456789ABCDEF')  # 16-byte PSK
        >>> encrypted = crypto.encrypt(b'Hello mesh!', node_id=0x1234, pkt_id=42)
        >>> decrypted = crypto.decrypt(encrypted)
        >>> assert decrypted == b'Hello mesh!'

    Attributes:
        NONCE_SIZE: Size of nonce prefix in encrypted payloads (8 bytes)
        KEY_SIZE: AES key size (16 bytes)
        BLOCK_SIZE: AES block size (16 bytes)
    """

    NONCE_SIZE = NONCE_SIZE
    KEY_SIZE = KEY_SIZE
    BLOCK_SIZE = BLOCK_SIZE

    def __init__(self, psk: bytes):
        """
        Initialize crypto engine with Pre-Shared Key.

        Args:
            psk: 16-byte Pre-Shared Key for AES-128

        Raises:
            ValueError: If PSK is not exactly 16 bytes
        """
        if not isinstance(psk, bytes):
            raise ValueError(f"PSK must be bytes, got {type(psk).__name__}")
        if len(psk) != KEY_SIZE:
            raise ValueError(f"PSK must be {KEY_SIZE} bytes, got {len(psk)}")

        self._psk = psk

    def encrypt(self, data: bytes, node_id: int, pkt_id: int) -> bytes:
        """
        Encrypt data with AES-128 CTR mode.

        Nonce construction (8 bytes):
        - Bytes 0-1: node_id (big-endian uint16)
        - Bytes 2-7: six random bytes from os.urandom

        Args:
            data: Plaintext to encrypt
            node_id: 16-bit source node ID (for nonce uniqueness)
            pkt_id: validated legacy packet identifier; not encoded in the nonce

        Returns:
            Encrypted payload: nonce (8 bytes) + ciphertext

        Raises:
            ValueError: If node_id or pkt_id out of range
        """
        if not (0 <= node_id <= 0xFFFF):
            raise ValueError(f"node_id must be 0-65535, got {node_id}")
        if not (0 <= pkt_id <= 0xFFFF):
            raise ValueError(f"pkt_id must be 0-65535, got {pkt_id}")

        # Generate unique nonce
        nonce = self._make_nonce(node_id, pkt_id)

        # Encrypt with CTR mode
        ciphertext = self._ctr_crypt(data, nonce)

        # Return nonce + ciphertext
        return nonce + ciphertext

    def decrypt(self, data: bytes) -> bytes:
        """
        Decrypt data encrypted with encrypt().

        Expects: nonce (8 bytes) + ciphertext

        Args:
            data: Encrypted payload from encrypt()

        Returns:
            Decrypted plaintext

        Raises:
            ValueError: If data too short (< 8 bytes)
        """
        if len(data) < NONCE_SIZE:
            raise ValueError(f"Encrypted data too short: {len(data)} bytes (need >= {NONCE_SIZE})")

        # Extract nonce and ciphertext
        nonce = data[:NONCE_SIZE]
        ciphertext = data[NONCE_SIZE:]

        # Decrypt with CTR mode (CTR is symmetric: encrypt = decrypt)
        plaintext = self._ctr_crypt(ciphertext, nonce)

        return plaintext

    def _ctr_crypt(self, data: bytes, nonce: bytes) -> bytes:
        """
        AES-128 CTR mode encryption/decryption.

        CTR mode implementation using ECB as primitive:
        1. Create counter block: nonce (8B) + counter (8B, big-endian)
        2. Encrypt counter block with AES-ECB
        3. XOR result with plaintext/ciphertext
        4. Increment counter, repeat for each 16-byte block

        CTR mode is symmetric: encryption and decryption are identical.

        Args:
            data: Data to encrypt/decrypt
            nonce: 8-byte nonce for CTR mode

        Returns:
            Encrypted/decrypted data (same length as input)
        """
        if not UCRYPTOLIB_AVAILABLE:
            # SECURITY: Do not use insecure XOR fallback in production
            raise RuntimeError(
                "Encryption requested but ucryptolib not available. "
                "Install ucryptolib or disable encryption. "
                "Using insecure fallback is not permitted."
            )

        # Create AES cipher in ECB mode (mode 1)
        cipher = aes(self._psk, 1)

        result = bytearray(len(data))
        counter_block = bytearray(BLOCK_SIZE)
        counter_block[:NONCE_SIZE] = nonce  # First 8 bytes = nonce
        # Last 8 bytes = counter (starts at 0)

        data_offset = 0
        block_counter = 0

        while data_offset < len(data):
            # Set counter value (big-endian 64-bit)
            counter_block[8:16] = block_counter.to_bytes(8, 'big')

            # Encrypt counter block to generate keystream
            keystream = cipher.encrypt(bytes(counter_block))

            # XOR keystream with data
            block_end = min(data_offset + BLOCK_SIZE, len(data))
            for i in range(data_offset, block_end):
                result[i] = data[i] ^ keystream[i - data_offset]

            data_offset += BLOCK_SIZE
            block_counter += 1

        return bytes(result)

    def _make_nonce(self, node_id: int, pkt_id: int) -> bytes:
        """
        Generate 8-byte nonce with high entropy.

        Nonce format (improved for security):
        - Bytes 0-1: node_id (big-endian)
        - Bytes 2-7: 6 random bytes (48 bits entropy)

        Note: pkt_id is no longer used in nonce to prevent two-time pad risk
        when pkt_id wraps around (16-bit). Instead, we use 6 random bytes
        for 48 bits of entropy which provides strong uniqueness guarantees.

        Args:
            node_id: 16-bit node identifier
            pkt_id: 16-bit packet identifier (unused, kept for API compatibility)

        Returns:
            8-byte nonce
        """
        nonce = bytearray(NONCE_SIZE)
        nonce[0:2] = node_id.to_bytes(2, 'big')
        nonce[2:8] = urandom(6)  # 6 random bytes (48 bits entropy)
        return bytes(nonce)

    @staticmethod
    def generate_key() -> bytes:
        """
        Generate random 16-byte AES-128 key.

        Uses os.urandom for cryptographically secure random generation.

        Returns:
            16-byte random key

        Example:
            >>> key = MeshCrypto.generate_key()
            >>> crypto = MeshCrypto(key)
        """
        return urandom(KEY_SIZE)

    @staticmethod
    def derive_key(password: str, salt: bytes = None) -> bytes:
        """
        Derive 16-byte AES key from password using simple KDF.

        Uses multiple SHA256 rounds for key stretching.
        Not as strong as PBKDF2 but lightweight for MicroPython.

        WARNING: Use strong passwords. Short passwords are vulnerable
        to brute-force attacks.

        Args:
            password: User password (string)
            salt: Optional 16-byte salt (random generated if None)

        Returns:
            16-byte derived key

        Raises:
            RuntimeError: If hashlib not available
            ValueError: If salt provided but not 16 bytes

        Example:
            >>> key = MeshCrypto.derive_key("my-secure-password")
            >>> crypto = MeshCrypto(key)
        """
        if hashlib is None:
            raise RuntimeError("hashlib not available - cannot derive key")

        if salt is None:
            salt = urandom(16)
        elif len(salt) != 16:
            raise ValueError(f"Salt must be 16 bytes, got {len(salt)}")

        # Simple KDF: SHA256(password + salt) iterated 1000 times
        # For stronger security, use PBKDF2 if available
        pwd_bytes = password.encode('utf-8')
        key = salt + pwd_bytes

        # 1000 iterations of SHA256
        for _ in range(1000):
            key = hashlib.sha256(key).digest()

        # Return first 16 bytes for AES-128
        return key[:KEY_SIZE]


def is_encrypted_payload(data: bytes) -> bool:
    """
    Check if payload appears to be encrypted.

    A payload is considered encrypted if it's at least NONCE_SIZE bytes.
    This is a basic heuristic - actual encryption status should be tracked
    via packet flags (FLAG_ENCRYPTED).

    Args:
        data: Payload to check

    Returns:
        True if payload length >= NONCE_SIZE (could be encrypted)

    Example:
        >>> crypto = MeshCrypto(b'0123456789ABCDEF')
        >>> enc = crypto.encrypt(b'test', 0x1234, 42)
        >>> is_encrypted_payload(enc)
        True
        >>> is_encrypted_payload(b'short')
        False
    """
    return len(data) >= NONCE_SIZE


# Expose key functions at module level
__all__ = [
    'MeshCrypto',
    'is_encrypted_payload',
    'NONCE_SIZE',
    'KEY_SIZE',
    'BLOCK_SIZE',
]
