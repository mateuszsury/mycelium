# µMesh Examples

This directory contains examples demonstrating how to use the µMesh protocol stack.

## Layer 1 - Physical Adapter (ESP-NOW)

### 01_adapter_basic.py
Basic broadcast communication between ESP32 nodes.

**What it demonstrates:**
- Initializing the ESPNowAdapter
- Sending broadcast messages
- Receiving messages asynchronously
- Reading RSSI values

**Hardware needed:** 2+ ESP32 boards

**Run it:**
```python
# Upload umesh package to your ESP32
# Run this script on multiple boards
import examples.01_adapter_basic
```

### 02_adapter_unicast.py
Point-to-point (unicast) communication with peer registration.

**What it demonstrates:**
- Registering specific peers
- Unicast send with ACK
- Difference between broadcast and unicast
- Peer management

**Hardware needed:** 2 ESP32 boards

**Setup:**
1. Get MAC addresses by running 01_adapter_basic.py on both boards
2. Configure TARGET_MAC in the script for each board
3. Run on both boards

**Key concepts:**
- Unicast requires peer registration via `add_peer()`
- Unicast send returns True only if 1-hop ACK received
- Broadcast always returns True (no ACK)
- Max 19 unicast peers (20 total - 1 for broadcast)

## Coming Soon

### Layer 2 - Mesh Link Layer
- Neighbor discovery
- Duplicate detection
- Peer rotation for >19 nodes

### Layer 3 - Routing
- Multi-hop routing
- Route discovery (AODV-lite)
- Mesh topology

### Layer 4 - Transport
- Message fragmentation
- Reliable delivery with retransmit

### Layer 5 - Application
- Pub/Sub messaging
- RPC (Remote Procedure Call)
- File transfer

## Hardware Requirements

**Minimum:**
- ESP32 (any variant)
- MicroPython firmware >= v1.20

**Recommended:**
- ESP32-S3 (for PSRAM and better performance)
- MicroPython >= v1.21 (for better RSSI support)

**NOT supported:**
- ESP8266 (insufficient RAM for mesh protocol)

## MicroPython Version Notes

The adapter works across different MicroPython versions with graceful fallbacks:

**v1.20+:** Basic functionality, RSSI may not be available
**v1.21+:** Full RSSI support via `peers_table`
**v1.22+:** Split heap, better memory management

## Channel Configuration

All nodes in the mesh must use the same Wi-Fi channel (1-14).

**Recommendations:**
- **Channel 1, 6, or 11** - Non-overlapping 2.4GHz channels
- **Channel 6** - Default, good middle ground
- Avoid channels used by nearby Wi-Fi networks for less interference

## Long Range Mode

Some ESP32 variants support Long Range mode for extended distance:

**Supported:** ESP32-S2, ESP32-S3, ESP32-C3 (with LR capability)
**Not supported:** Original ESP32, ESP32-C2

Enable it:
```python
adapter = ESPNowAdapter(channel=6, lr_mode=True)
```

**Note:** All nodes must use the same mode (LR or normal).

## Troubleshooting

**"network module not available"**
- You're not running on ESP32 or MicroPython not installed correctly

**"Failed to register peer"**
- Peer table full (max 20 total, 19 usable)
- Use peer rotation strategy (coming in Layer 2)

**"No messages received"**
- Check all nodes on same channel
- Check WLAN is active (`wlan.active()` should return True)
- Verify boards are in range (~100m normal, ~300m LR mode)

**RSSI always -127**
- Your MicroPython version doesn't support RSSI retrieval
- Upgrade to v1.21+ for full support
- This doesn't affect functionality, just metrics

## Performance Tips

1. **RX Buffer:** Increase `rx_buf` for high traffic scenarios
   ```python
   adapter = ESPNowAdapter(rx_buf=4096)  # Default is 2048
   ```

2. **Reduce GC pauses:** Call `gc.collect()` manually between transmissions
   ```python
   import gc
   await adapter.send_broadcast(data)
   gc.collect()
   ```

3. **Power saving:** Use shorter intervals for battery-powered nodes
   ```python
   await asyncio.sleep_ms(100)  # Instead of sleep(seconds)
   ```

## License

See main project LICENSE file.
