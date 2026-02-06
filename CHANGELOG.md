# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [0.1.0] - 2026-02-06

### Added

- Complete 5-layer protocol stack (Physical, Infrastructure, Routing, Transport, Application)
- **Layer 1:** ESPNowAdapter with async I/O, Long Range mode support, RSSI tracking
- **Layer 2:** PeerManager with LRU eviction, NeighborTable with EWMA smoothing, DuplicateFilter with O(1) eviction
- **Layer 3:** AODV-lite route discovery with expanding ring search, RoutingTable with sequence numbers, ForwardingEngine with pending queue
- **Layer 4:** PacketCodec with 14-byte header, Fragmenter/Reassembler supporting up to 3,540 bytes, ReliableTransport with ACK/NACK/retransmit
- **Layer 5:** PubSub topic messaging, MeshRPC with request/response, FileTransfer with CRC-32 verification, MeshDiag with ping/stats
- AES-128 CTR encryption with per-packet nonce and pre-shared key
- Viper/native optimized critical paths (`_fast.py`)
- MeshConfig with comprehensive parameter validation
- 17 example scripts covering all layers
- Full test suite for CPython
