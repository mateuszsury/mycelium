"""
Complete µMesh Example
=======================

Demonstrates all features of the µMesh protocol:
- Basic messaging (unicast and broadcast)
- Reliable delivery with ACK
- Large message fragmentation
- Publish/Subscribe
- Remote Procedure Call
- File Transfer
- Network Diagnostics

This example shows a sensor node that:
1. Publishes temperature readings
2. Responds to RPC requests for sensor data
3. Sends periodic diagnostics to a gateway
4. Receives configuration file updates
"""

import umesh
try:
    import uasyncio as asyncio
except ImportError:
    import asyncio

# =============================================================================
# CONFIGURATION
# =============================================================================

# Node configuration
CONFIG = umesh.MeshConfig(
    # node_id will be auto-derived from MAC if not specified
    channel=6,                  # WiFi channel (1-14)
    lr_mode=True,               # Enable Long Range mode for better coverage
    hello_interval=10000,       # Send HELLO beacons every 10 seconds
    route_timeout=120000,       # Routes expire after 2 minutes of inactivity
    max_ttl=7,                  # Maximum 7 hops
    peer_slots=19,              # Use all 19 available peer slots
    enable_encryption=False,    # Disable encryption for this example
    # psk=b"my_secret_key_16", # 16-byte key if encryption enabled
)

# Gateway node ID (replace with your gateway's node_id)
GATEWAY_ID = 0x0001

# =============================================================================
# SENSOR DATA SIMULATION
# =============================================================================

class SensorSimulator:
    """Simulates temperature and humidity sensors."""

    def __init__(self):
        self.temperature = 23.5  # Celsius
        self.humidity = 45.0     # Percent
        self.counter = 0

    def read(self):
        """Read sensor values (simulated with slight variation)."""
        import random
        self.counter += 1
        # Add small random variation
        self.temperature += random.uniform(-0.5, 0.5)
        self.humidity += random.uniform(-2.0, 2.0)
        # Keep in reasonable ranges
        self.temperature = max(15.0, min(35.0, self.temperature))
        self.humidity = max(20.0, min(80.0, self.humidity))

        return {
            'temperature': round(self.temperature, 1),
            'humidity': round(self.humidity, 1),
            'reading': self.counter,
        }

# =============================================================================
# MESH NODE APPLICATION
# =============================================================================

class SensorNode:
    """Sensor node application using µMesh."""

    def __init__(self):
        self.mesh = None
        self.sensor = SensorSimulator()
        self.running = False

    async def start(self):
        """Initialize and start the mesh node."""
        print("Starting µMesh sensor node...")

        # Create mesh
        self.mesh = umesh.Mesh(CONFIG)
        await self.mesh.start()

        print(f"Node ID: 0x{self.mesh.node_id:04X}")
        print(f"MAC: {self.mesh.mac.hex(':')}")
        print()

        # Register message callback
        self.mesh.on_message(self.on_message)

        # Register RPC handlers
        self.mesh.rpc.register("get_sensor", self.rpc_get_sensor)
        self.mesh.rpc.register("get_status", self.rpc_get_status)
        self.mesh.rpc.register("reboot", self.rpc_reboot)

        # Subscribe to configuration updates
        self.mesh.pubsub.subscribe("config/update", self.on_config_update)

        # Register file transfer callback
        self.mesh.file.on_file_received(self.on_file_received)

        self.running = True

        # Start background tasks
        asyncio.create_task(self.publish_sensor_data())
        asyncio.create_task(self.send_diagnostics())
        asyncio.create_task(self.monitor_neighbors())

        print("Node started successfully!")

    async def stop(self):
        """Stop the mesh node."""
        self.running = False
        if self.mesh:
            await self.mesh.stop()

    # =========================================================================
    # MESSAGE HANDLERS
    # =========================================================================

    def on_message(self, src_id: int, data: bytes):
        """Handle received DATA packets."""
        print(f"[MSG] From 0x{src_id:04X}: {data}")

    def on_config_update(self, src_id: int, topic: str, data: bytes):
        """Handle configuration update via pub/sub."""
        print(f"[CONFIG] Update from 0x{src_id:04X}: {data}")
        # In real application, parse and apply config here

    def on_file_received(self, src_id: int, filename: str, data: bytes):
        """Handle received file."""
        print(f"[FILE] Received '{filename}' from 0x{src_id:04X} ({len(data)} bytes)")
        # In real application, save file to filesystem
        # with open(filename, 'wb') as f:
        #     f.write(data)

    # =========================================================================
    # RPC HANDLERS
    # =========================================================================

    def rpc_get_sensor(self):
        """RPC: Get current sensor readings."""
        return self.sensor.read()

    def rpc_get_status(self):
        """RPC: Get node status."""
        stats = self.mesh.stats
        return {
            'node_id': self.mesh.node_id,
            'neighbors': len(self.mesh.neighbors),
            'routes': self.mesh.routes.count(),
            'uptime': stats.get('tx_packets', 0),  # Simplified
            'stats': {
                'tx_packets': stats['tx_packets'],
                'rx_packets': stats['rx_packets'],
                'dropped': stats['dropped_duplicates'],
            }
        }

    async def rpc_reboot(self):
        """RPC: Reboot node (simulated)."""
        print("[RPC] Reboot requested")
        # In real application: machine.reset()
        return "Rebooting..."

    # =========================================================================
    # BACKGROUND TASKS
    # =========================================================================

    async def publish_sensor_data(self):
        """Publish sensor readings every 30 seconds."""
        while self.running:
            await asyncio.sleep(30)

            # Read sensor
            data = self.sensor.read()

            # Encode as simple bytes (temperature as int, humidity as int)
            # Format: [temp_int8, humidity_uint8]
            temp_byte = int(data['temperature']) & 0xFF
            humidity_byte = int(data['humidity']) & 0xFF
            payload = bytes([temp_byte, humidity_byte])

            # Publish to topic
            try:
                await self.mesh.pubsub.publish(
                    "sensor/temp_humidity",
                    payload,
                    qos=1  # QoS 1 = acknowledged delivery
                )
                print(f"[PUB] Published: temp={data['temperature']}°C, "
                      f"humidity={data['humidity']}%")
            except Exception as e:
                print(f"[PUB] Error: {e}")

    async def send_diagnostics(self):
        """Send diagnostics to gateway every 2 minutes."""
        # Wait for network to stabilize
        await asyncio.sleep(60)

        while self.running:
            await asyncio.sleep(120)

            try:
                # Get comprehensive stats
                stats = umesh.get_mesh_stats(self.mesh)

                # Format as text
                diag_text = umesh.format_stats(stats)

                # Send to gateway with reliable delivery
                success = await self.mesh.send_reliable(
                    GATEWAY_ID,
                    diag_text.encode('utf-8'),
                    timeout=5000
                )

                if success:
                    print(f"[DIAG] Sent diagnostics to gateway")
                else:
                    print(f"[DIAG] Failed to send diagnostics")

            except Exception as e:
                print(f"[DIAG] Error: {e}")

    async def monitor_neighbors(self):
        """Monitor and report neighbor changes."""
        last_neighbors = set()

        while self.running:
            await asyncio.sleep(15)

            # Get current neighbors
            current = set(n[0] for n in self.mesh.neighbors)

            # Check for changes
            joined = current - last_neighbors
            left = last_neighbors - current

            for node_id in joined:
                print(f"[NEIGHBOR] Node 0x{node_id:04X} joined")

            for node_id in left:
                print(f"[NEIGHBOR] Node 0x{node_id:04X} left")

            last_neighbors = current

            # Print current neighbor count
            if len(current) > 0:
                print(f"[NEIGHBOR] Currently {len(current)} neighbors")

    # =========================================================================
    # INTERACTIVE COMMANDS
    # =========================================================================

    async def send_test_message(self, dest_id: int, message: str):
        """Send a test message to another node."""
        success = await self.mesh.send(dest_id, message.encode('utf-8'))
        if success:
            print(f"[TEST] Sent to 0x{dest_id:04X}: {message}")
        else:
            print(f"[TEST] Failed to send to 0x{dest_id:04X}")
        return success

    async def ping_node(self, dest_id: int, count: int = 3):
        """Ping another node to measure RTT."""
        print(f"[PING] Pinging 0x{dest_id:04X}...")
        rtts = await self.mesh.diag.ping(dest_id, count=count, timeout=2000)

        if rtts:
            avg_rtt = sum(rtts) / len(rtts)
            min_rtt = min(rtts)
            max_rtt = max(rtts)
            print(f"[PING] Results: {len(rtts)}/{count} received")
            print(f"[PING] RTT: min={min_rtt:.1f}ms avg={avg_rtt:.1f}ms max={max_rtt:.1f}ms")
        else:
            print(f"[PING] No response from 0x{dest_id:04X}")

        return rtts

    async def call_remote_sensor(self, dest_id: int):
        """Call get_sensor RPC on remote node."""
        try:
            result = await self.mesh.rpc.call(
                dest_id,
                "get_sensor",
                timeout=3000
            )
            print(f"[RPC] Remote sensor data from 0x{dest_id:04X}:")
            print(f"      Temperature: {result['temperature']}°C")
            print(f"      Humidity: {result['humidity']}%")
            print(f"      Reading: {result['reading']}")
            return result
        except umesh.RemoteError as e:
            print(f"[RPC] Remote error: {e}")
        except Exception as e:
            print(f"[RPC] Error: {e}")
        return None

# =============================================================================
# MAIN APPLICATION
# =============================================================================

async def main():
    """Main application entry point."""
    node = SensorNode()

    try:
        # Start node
        await node.start()

        # Wait a bit for network to form
        await asyncio.sleep(5)

        # Example: Send test broadcast
        await node.mesh.send(
            umesh.const.BROADCAST_ID,
            b"Hello mesh network!"
        )

        # Example: Ping gateway (if reachable)
        # await node.ping_node(GATEWAY_ID)

        # Example: Call RPC on another node
        # await node.call_remote_sensor(0x1234)

        # Run forever (in real app, handle shutdown signal)
        print("\nNode running... Press Ctrl+C to stop")
        while True:
            await asyncio.sleep(1)

    except KeyboardInterrupt:
        print("\nShutting down...")
    finally:
        await node.stop()

# =============================================================================
# ENTRY POINT
# =============================================================================

if __name__ == "__main__":
    asyncio.run(main())
