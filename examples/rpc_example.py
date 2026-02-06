"""
µMesh RPC Example
=================

Demonstrates Remote Procedure Call functionality with:
- Function registration on responder node
- Remote function calls from initiator node
- Error handling and timeouts
- Different argument and return types

This example uses two virtual nodes to demonstrate RPC communication.
"""

import sys
import asyncio

# Add parent directory to path
sys.path.insert(0, '..')

try:
    import uasyncio as asyncio
except ImportError:
    import asyncio

try:
    import ustruct as struct
except ImportError:
    import struct

from umesh import MeshRPC, RemoteError
from umesh.const import PKT_RPC


# =============================================================================
# MOCK MESH FOR TESTING
# =============================================================================

class MockMesh:
    """Simple mock mesh for demonstration."""

    def __init__(self, node_id):
        self.node_id_val = node_id
        self.peer_mesh = None  # Will be set to communicate with peer

    async def send(self, dest_id, payload, pkt_type=None):
        """Send packet to peer mesh."""
        if self.peer_mesh:
            # Simulate packet delivery to peer's RPC handler
            _, flags = struct.unpack('<HB', payload[:3])
            if flags & 0x01:  # FLAG_RESPONSE
                self.peer_mesh.rpc.handle_rpc_response(self.node_id_val, payload)
            else:
                self.peer_mesh.rpc.handle_rpc_request(self.node_id_val, payload)

    @property
    def node_id(self):
        return self.node_id_val


# =============================================================================
# RESPONDER NODE
# =============================================================================

class ResponderNode:
    """Node that registers RPC handlers and responds to calls."""

    def __init__(self, node_id):
        self.mesh = MockMesh(node_id)
        self.rpc = MeshRPC(self.mesh)
        self.mesh.rpc = self.rpc  # For mock communication

        # Simulated sensor data
        self.temperature = 23.5
        self.humidity = 45
        self.led_state = False

        # Register handlers
        self._register_handlers()

    def _register_handlers(self):
        """Register all RPC handlers."""

        # Simple function - no arguments
        def get_status():
            return {
                'node_id': self.mesh.node_id,
                'uptime': 12345,
                'free_mem': 50000,
            }

        self.rpc.register('get_status', get_status)

        # Function with arguments
        def get_sensor(sensor_type):
            if sensor_type == 'temperature':
                return {'type': 'temperature', 'value': self.temperature, 'unit': 'C'}
            elif sensor_type == 'humidity':
                return {'type': 'humidity', 'value': self.humidity, 'unit': '%'}
            else:
                raise ValueError(f"Unknown sensor type: {sensor_type}")

        self.rpc.register('get_sensor', get_sensor)

        # Function with multiple arguments
        def set_led(pin, state):
            self.led_state = bool(state)
            return {'pin': pin, 'state': self.led_state}

        self.rpc.register('set_led', set_led)

        # Function that returns different types
        def echo(value):
            return value

        self.rpc.register('echo', echo)

        # Function that raises error
        def risky_operation(should_fail):
            if should_fail:
                raise RuntimeError("Operation failed as requested")
            return "Success!"

        self.rpc.register('risky_op', risky_operation)

        print(f"[Responder {self.mesh.node_id:#06x}] Registered {len(self.rpc._handlers)} handlers")


# =============================================================================
# INITIATOR NODE
# =============================================================================

class InitiatorNode:
    """Node that makes RPC calls to responder."""

    def __init__(self, node_id):
        self.mesh = MockMesh(node_id)
        self.rpc = MeshRPC(self.mesh)
        self.mesh.rpc = self.rpc  # For mock communication

    async def run_examples(self, responder_id):
        """Run various RPC examples."""

        print(f"\n[Initiator {self.mesh.node_id:#06x}] Starting RPC examples...")

        # Example 1: Simple call with no arguments
        print("\n=== Example 1: Get Status (no arguments) ===")
        try:
            result = await self.rpc.call(responder_id, 'get_status', timeout=2000)
            print(f"Status: {result}")
        except (RemoteError, TimeoutError) as e:
            print(f"Error: {e}")

        # Example 2: Call with single argument
        print("\n=== Example 2: Get Sensor (with argument) ===")
        for sensor in ['temperature', 'humidity']:
            try:
                result = await self.rpc.call(responder_id, 'get_sensor', sensor, timeout=2000)
                print(f"{sensor.capitalize()}: {result['value']} {result['unit']}")
            except (RemoteError, TimeoutError) as e:
                print(f"Error reading {sensor}: {e}")

        # Example 3: Call with multiple arguments
        print("\n=== Example 3: Set LED (multiple arguments) ===")
        try:
            result = await self.rpc.call(responder_id, 'set_led', 2, True, timeout=2000)
            print(f"LED control result: {result}")
        except (RemoteError, TimeoutError) as e:
            print(f"Error: {e}")

        # Example 4: Echo different types
        print("\n=== Example 4: Echo (different types) ===")
        test_values = [
            None,
            True,
            42,
            "Hello RPC!",
            b'\x01\x02\x03',
            [1, 2, 3],
            {'key': 'value', 'number': 123},
        ]

        for value in test_values:
            try:
                result = await self.rpc.call(responder_id, 'echo', value, timeout=2000)
                match = result == value
                print(f"Echo {value!r:20s} -> {result!r:20s} [{'OK' if match else 'MISMATCH'}]")
            except (RemoteError, TimeoutError) as e:
                print(f"Error echoing {value}: {e}")

        # Example 5: Error handling
        print("\n=== Example 5: Error Handling ===")

        # Known sensor type
        try:
            result = await self.rpc.call(responder_id, 'get_sensor', 'pressure', timeout=2000)
            print(f"Pressure: {result}")
        except RemoteError as e:
            print(f"RemoteError (expected): {e}")
        except TimeoutError as e:
            print(f"TimeoutError: {e}")

        # Handler that raises exception
        try:
            result = await self.rpc.call(responder_id, 'risky_op', True, timeout=2000)
            print(f"Risky op result: {result}")
        except RemoteError as e:
            print(f"RemoteError (expected): {e}")

        # Handler that succeeds
        try:
            result = await self.rpc.call(responder_id, 'risky_op', False, timeout=2000)
            print(f"Risky op result: {result}")
        except RemoteError as e:
            print(f"RemoteError: {e}")

        # Example 6: Unknown function
        print("\n=== Example 6: Unknown Function ===")
        try:
            result = await self.rpc.call(responder_id, 'nonexistent_function', timeout=2000)
            print(f"Result: {result}")
        except RemoteError as e:
            print(f"RemoteError (expected): {e}")
        except TimeoutError as e:
            print(f"TimeoutError: {e}")

        # Example 7: Timeout
        print("\n=== Example 7: Timeout (unreachable node) ===")
        try:
            result = await self.rpc.call(0x9999, 'get_status', timeout=500)
            print(f"Result: {result}")
        except TimeoutError as e:
            print(f"TimeoutError (expected): {e}")

        print("\n=== All Examples Complete ===")

        # Show stats
        stats = self.rpc.stats
        print(f"\nRPC Stats: {stats}")


# =============================================================================
# MAIN
# =============================================================================

async def main():
    """Run RPC demonstration."""

    print("=" * 70)
    print("µMesh RPC Example")
    print("=" * 70)

    # Create responder node
    responder = ResponderNode(node_id=0x1234)

    # Create initiator node
    initiator = InitiatorNode(node_id=0x5678)

    # Connect the nodes for mock communication
    initiator.mesh.peer_mesh = responder.mesh
    responder.mesh.peer_mesh = initiator.mesh

    # Run examples
    await initiator.run_examples(responder_id=0x1234)

    print("\n" + "=" * 70)
    print("Example complete!")
    print("=" * 70)


if __name__ == '__main__':
    asyncio.run(main())
