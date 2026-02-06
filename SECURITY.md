# Security Policy

## Supported Versions

| Version | Supported |
|---------|-----------|
| 0.1.x   | Yes       |

## Reporting Vulnerabilities

**Please do not open public issues for security vulnerabilities.**

Instead, use [GitHub Security Advisories](https://github.com/mateuszsury/mycelium/security/advisories/new) to report vulnerabilities privately.

### What to Include

- Description of the vulnerability
- Steps to reproduce
- Potential impact
- Suggested fix (if any)

### Response Timeline

| Step | Timeframe |
|------|-----------|
| Acknowledgment | 48 hours |
| Assessment | 7 days |
| Fix release | 30 days |

## Security Architecture

### Encryption

- **Algorithm:** AES-128 in CTR mode
- **Key distribution:** Pre-Shared Key (PSK), 16 bytes
- **Nonce:** Per-packet, derived from packet ID + source ID
- **Scope:** Payload only (header is plaintext for routing)

### Implementation

The encryption module (`umesh/crypto.py`) provides:

- Zero-allocation encrypt/decrypt using pre-allocated buffers
- CTR mode with unique nonce per packet
- Viper-optimized XOR operations on ESP32

## Known Limitations

These are design trade-offs, not bugs:

1. **No forward secrecy** — Compromised PSK exposes all past and future traffic
2. **Shared key** — All nodes use the same PSK; no per-link keys
3. **No replay protection beyond dedup** — The duplicate filter window is finite; old packets replayed after the window expires may be accepted
4. **Plaintext headers** — Source/destination IDs, TTL, and packet type are visible to eavesdroppers (required for routing)
5. **No authentication** — Any node with the PSK can join the mesh; there is no certificate or identity verification
6. **No key rotation** — PSK must be changed manually on all nodes

### Recommendations

- Use Mycelium encryption for sensor data and non-critical IoT applications
- For high-security requirements, add application-layer encryption (e.g., TLS over the mesh transport)
- Physically secure ESP32 devices to prevent PSK extraction from flash
- Rotate PSKs periodically by reflashing all nodes
