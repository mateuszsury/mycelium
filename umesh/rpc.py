"""
µMesh RPC (Remote Procedure Call)
==================================

Simple RPC mechanism for calling functions on remote mesh nodes.

This module provides:

1. **MeshRPC**: RPC handler and caller
2. **PendingCall**: Tracks pending RPC calls awaiting responses
3. **RemoteError**: Exception raised when remote handler fails
4. **Custom serialization**: Compact binary format (not JSON)

Key features:
- Function name hashing (DJB2) for compact transmission
- Custom binary serialization (lighter than JSON)
- Timeout handling with asyncio
- Error propagation from remote to caller
- Optional reliable delivery via ReliableTransport
- Memory-bounded handler and pending call limits

Usage:
    # Initialize RPC system
    rpc = MeshRPC(mesh, transport=reliable_transport)

    # Register a handler
    def get_sensor_reading():
        return {'temp': 23.5, 'humidity': 45}

    rpc.register('get_sensor', get_sensor_reading)

    # Call remote function
    result = await rpc.call(dest_id=0x1234, 'get_sensor', timeout=3000)
    print(f"Remote sensor: {result}")

    # Unregister handler
    rpc.unregister('get_sensor')

Packet format (payload of PKT_RPC):
    call_id: uint16          # Unique call identifier
    flags: uint8             # Bit 0: is_response, Bit 1: is_error
    name_hash: uint16        # Hash of function name (request only)
    data: bytes              # Serialized args or result

Serialization format:
    Type byte + data:
    0x00: None
    0x01: True
    0x02: False
    0x10: int8 + 1 byte
    0x11: int16 + 2 bytes
    0x12: int32 + 4 bytes
    0x13: float + 4 bytes (IEEE 754 single precision)
    0x20: bytes + length(uint16) + data
    0x21: str + length(uint16) + utf8 data
    0x30: list + count(uint8) + items
    0x31: dict + count(uint8) + key-value pairs
"""

try:
    import uasyncio as asyncio
except ImportError:
    import asyncio

try:
    from utime import ticks_ms, ticks_diff
except ImportError:
    # Fallback for desktop Python
    from time import monotonic
    ticks_ms = lambda: int(monotonic() * 1000)
    ticks_diff = lambda a, b: a - b

try:
    import ustruct as struct
except ImportError:
    import struct

from .const import PKT_RPC


# =============================================================================
# CONSTANTS
# =============================================================================

DEFAULT_TIMEOUT = 5000   # ms
MAX_HANDLERS = 32        # Max registered handlers
MAX_PENDING_CALLS = 16   # Max concurrent pending RPC calls

# RPC packet flags
FLAG_RESPONSE = 0x01     # This is a response packet
FLAG_ERROR = 0x02        # This response contains an error

# Serialization type codes
TYPE_NONE = 0x00
TYPE_TRUE = 0x01
TYPE_FALSE = 0x02
TYPE_INT8 = 0x10
TYPE_INT16 = 0x11
TYPE_INT32 = 0x12
TYPE_FLOAT = 0x13
TYPE_BYTES = 0x20
TYPE_STR = 0x21
TYPE_LIST = 0x30
TYPE_DICT = 0x31


# =============================================================================
# EXCEPTIONS
# =============================================================================

class RemoteError(Exception):
    """Exception raised when remote handler fails."""
    pass


# =============================================================================
# PENDING CALL TRACKING
# =============================================================================

class PendingCall:
    """Tracks pending RPC call waiting for response."""
    __slots__ = ('call_id', 'dest_id', 'event', 'result', 'error', 'started_ms')

    def __init__(self, call_id: int, dest_id: int):
        """Initialize pending call.

        Args:
            call_id: Unique call identifier
            dest_id: Destination node ID
        """
        self.call_id = call_id
        self.dest_id = dest_id
        self.event = asyncio.Event()
        self.result = None
        self.error = None
        self.started_ms = ticks_ms()


# =============================================================================
# MESH RPC
# =============================================================================

class MeshRPC:
    """Remote Procedure Call over mesh network.

    Provides RPC mechanism with function registration, remote calls,
    timeout handling, and error propagation.

    Attributes:
        _mesh: Mesh instance for sending/receiving packets
        _transport: Optional ReliableTransport for guaranteed delivery
        _handlers: Registered handler functions (name_hash -> (name, handler))
        _pending: Pending RPC calls awaiting response (call_id -> PendingCall)
        _call_id: Counter for generating unique call IDs
    """

    def __init__(self, mesh, transport=None):
        """Initialize with Mesh and optional ReliableTransport.

        If transport is provided, uses reliable delivery for RPC.

        Args:
            mesh: Mesh instance
            transport: Optional ReliableTransport instance for reliable delivery
        """
        self._mesh = mesh
        self._transport = transport
        self._handlers = {}      # name_hash -> (name, handler)
        self._pending = {}       # call_id -> PendingCall
        self._call_id = 0

    def register(self, name: str, handler):
        """Register a function that can be called remotely.

        The handler function will be called when a remote node sends an RPC
        request with the matching name. The handler should accept arguments
        and return a result that can be serialized.

        Args:
            name: Function name (will be hashed for transmission)
            handler: Callable handler(*args) -> result

        Raises:
            ValueError: If too many handlers registered (>MAX_HANDLERS)

        Note:
            Result must be serializable (bytes, int, str, None, list, dict).
            Handler exceptions are caught and propagated to caller as RemoteError.
        """
        if len(self._handlers) >= MAX_HANDLERS:
            raise ValueError(f"Too many handlers: {len(self._handlers)}, max {MAX_HANDLERS}")

        name_hash = self._hash_name(name)
        self._handlers[name_hash] = (name, handler)

    def unregister(self, name: str):
        """Remove registered handler.

        Args:
            name: Function name to unregister

        Returns:
            True if handler was found and removed, False otherwise
        """
        name_hash = self._hash_name(name)
        if name_hash in self._handlers:
            del self._handlers[name_hash]
            return True
        return False

    async def call(self, dest_id: int, name: str, *args, timeout: int = None) -> any:
        """Call remote function and wait for result.

        Sends RPC request to dest_id with function name and arguments,
        then waits for response. Blocks until response received or timeout.

        Args:
            dest_id: Destination node ID
            name: Function name to call
            *args: Arguments to pass to remote function
            timeout: Timeout in milliseconds (default: DEFAULT_TIMEOUT)

        Returns:
            Result returned by remote handler

        Raises:
            TimeoutError: If no response within timeout
            RemoteError: If remote handler raised exception
            ValueError: If too many pending calls
        """
        if timeout is None:
            timeout = DEFAULT_TIMEOUT

        # Check pending call limit
        if len(self._pending) >= MAX_PENDING_CALLS:
            raise ValueError(
                f"Too many pending calls: {len(self._pending)}, max {MAX_PENDING_CALLS}"
            )

        # Generate unique call ID
        call_id = self._next_call_id()

        # Create pending call tracker
        pending = PendingCall(call_id, dest_id)
        self._pending[call_id] = pending

        try:
            # Build RPC request packet
            name_hash = self._hash_name(name)
            args_data = self._serialize_args(args)

            # Packet format: call_id(2) + flags(1) + name_hash(2) + args_data
            payload = struct.pack('<HBH', call_id, 0, name_hash) + args_data

            # Send via reliable transport if available, otherwise best-effort
            if self._transport:
                success = await self._transport.send_reliable(
                    dest_id, payload, timeout=timeout
                )
                if not success:
                    raise TimeoutError(f"Failed to send RPC request to {dest_id:#06x}")
            else:
                await self._mesh.send(dest_id, payload, pkt_type=PKT_RPC)

            # Wait for response with timeout
            try:
                await asyncio.wait_for(pending.event.wait(), timeout / 1000.0)
            except asyncio.TimeoutError:
                raise TimeoutError(
                    f"RPC call timeout after {timeout}ms: {name} to {dest_id:#06x}"
                )

            # Check for error
            if pending.error:
                raise RemoteError(pending.error)

            return pending.result

        finally:
            # Clean up pending call
            if call_id in self._pending:
                del self._pending[call_id]

    def handle_rpc_request(self, src_id: int, payload: bytes):
        """Handle incoming RPC request.

        Deserializes request, calls registered handler, and sends response.
        Errors during handler execution are caught and sent back as error response.

        Args:
            src_id: Source node ID
            payload: RPC request payload
        """
        try:
            # Parse request: call_id(2) + flags(1) + name_hash(2) + args_data
            if len(payload) < 5:
                return  # Invalid packet

            call_id, flags, name_hash = struct.unpack('<HBH', payload[:5])
            args_data = payload[5:]

            # Ignore if this is a response (shouldn't happen, but guard against it)
            if flags & FLAG_RESPONSE:
                return

            # Find handler
            if name_hash not in self._handlers:
                # Send error: unknown function
                self._send_error_response(src_id, call_id, f"Unknown function: {name_hash:#06x}")
                return

            name, handler = self._handlers[name_hash]

            # Deserialize arguments
            try:
                args = self._deserialize_args(args_data)
            except Exception as e:
                self._send_error_response(src_id, call_id, f"Argument deserialization failed: {e}")
                return

            # Call handler
            try:
                result = handler(*args)
            except Exception as e:
                self._send_error_response(src_id, call_id, f"Handler exception: {e}")
                return

            # Serialize result
            try:
                result_data = self._serialize_result(result)
            except Exception as e:
                self._send_error_response(src_id, call_id, f"Result serialization failed: {e}")
                return

            # Send success response
            self._send_success_response(src_id, call_id, result_data)

        except Exception as e:
            # Catch-all for unexpected errors
            print(f"RPC request handler error: {e}")

    def handle_rpc_response(self, src_id: int, payload: bytes):
        """Handle incoming RPC response.

        Matches response to pending call and sets result or error.

        Args:
            src_id: Source node ID
            payload: RPC response payload
        """
        try:
            # Parse response: call_id(2) + flags(1) + data
            if len(payload) < 3:
                return  # Invalid packet

            call_id, flags = struct.unpack('<HB', payload[:3])
            data = payload[3:]

            # Ignore if not a response
            if not (flags & FLAG_RESPONSE):
                return

            # Find pending call
            if call_id not in self._pending:
                return  # Unknown or already completed call

            pending = self._pending[call_id]

            # Check if error response
            if flags & FLAG_ERROR:
                # Deserialize error message
                try:
                    error_msg = self._deserialize_result(data)
                    pending.error = str(error_msg)
                except:
                    pending.error = "Unknown error"
            else:
                # Deserialize result
                try:
                    pending.result = self._deserialize_result(data)
                except Exception as e:
                    pending.error = f"Result deserialization failed: {e}"

            # Signal completion
            pending.event.set()

        except Exception as e:
            print(f"RPC response handler error: {e}")

    def _send_success_response(self, dest_id: int, call_id: int, result_data: bytes):
        """Send successful RPC response.

        Args:
            dest_id: Destination node ID
            call_id: Call ID to respond to
            result_data: Serialized result data
        """
        # Build response: call_id(2) + flags(1) + result_data
        flags = FLAG_RESPONSE
        payload = struct.pack('<HB', call_id, flags) + result_data

        # Send via mesh (fire-and-forget for responses)
        # Try to create task if event loop is running, otherwise schedule for later
        try:
            asyncio.create_task(self._mesh.send(dest_id, payload, pkt_type=PKT_RPC))
        except RuntimeError:
            # No event loop running - this is OK for testing
            # In production, handler is called from async context
            pass

    def _send_error_response(self, dest_id: int, call_id: int, error_msg: str):
        """Send error RPC response.

        Args:
            dest_id: Destination node ID
            call_id: Call ID to respond to
            error_msg: Error message string
        """
        # Build error response: call_id(2) + flags(1) + error_msg_data
        flags = FLAG_RESPONSE | FLAG_ERROR
        error_data = self._serialize_result(error_msg)
        payload = struct.pack('<HB', call_id, flags) + error_data

        # Send via mesh
        try:
            asyncio.create_task(self._mesh.send(dest_id, payload, pkt_type=PKT_RPC))
        except RuntimeError:
            # No event loop running - this is OK for testing
            pass

    def _serialize_args(self, args: tuple) -> bytes:
        """Serialize arguments for transmission.

        Args:
            args: Tuple of arguments

        Returns:
            Serialized bytes
        """
        # Serialize as list
        return self._serialize_value(list(args))

    def _deserialize_args(self, data: bytes) -> tuple:
        """Deserialize arguments from received data.

        Args:
            data: Serialized bytes

        Returns:
            Tuple of arguments
        """
        # Deserialize as list, convert to tuple
        args_list, _ = self._deserialize_value(data, 0)
        return tuple(args_list) if isinstance(args_list, list) else ()

    def _serialize_result(self, result) -> bytes:
        """Serialize result for transmission.

        Args:
            result: Result value

        Returns:
            Serialized bytes
        """
        return self._serialize_value(result)

    def _deserialize_result(self, data: bytes):
        """Deserialize result from received data.

        Args:
            data: Serialized bytes

        Returns:
            Deserialized result value
        """
        result, _ = self._deserialize_value(data, 0)
        return result

    def _serialize_value(self, value) -> bytes:
        """Serialize a single value to bytes.

        Supports: None, bool, int, float, bytes, str, list, dict

        Args:
            value: Value to serialize

        Returns:
            Serialized bytes

        Raises:
            TypeError: If value type not supported
        """
        # None
        if value is None:
            return bytes([TYPE_NONE])

        # Boolean (check before int, as bool is subclass of int)
        if isinstance(value, bool):
            return bytes([TYPE_TRUE if value else TYPE_FALSE])

        # Float
        if isinstance(value, float):
            return bytes([TYPE_FLOAT]) + struct.pack('<f', value)

        # Integer
        if isinstance(value, int):
            # Try int8
            if -128 <= value <= 127:
                return bytes([TYPE_INT8]) + struct.pack('<b', value)
            # Try int16
            elif -32768 <= value <= 32767:
                return bytes([TYPE_INT16]) + struct.pack('<h', value)
            # Use int32
            else:
                return bytes([TYPE_INT32]) + struct.pack('<i', value)

        # Bytes
        if isinstance(value, (bytes, bytearray)):
            data = bytes(value)
            return bytes([TYPE_BYTES]) + struct.pack('<H', len(data)) + data

        # String
        if isinstance(value, str):
            data = value.encode('utf-8')
            return bytes([TYPE_STR]) + struct.pack('<H', len(data)) + data

        # List
        if isinstance(value, (list, tuple)):
            if len(value) > 255:
                raise ValueError(f"List too long: {len(value)}, max 255")
            result = bytes([TYPE_LIST, len(value)])
            for item in value:
                result += self._serialize_value(item)
            return result

        # Dict
        if isinstance(value, dict):
            if len(value) > 255:
                raise ValueError(f"Dict too large: {len(value)}, max 255")
            result = bytes([TYPE_DICT, len(value)])
            for k, v in value.items():
                result += self._serialize_value(k)
                result += self._serialize_value(v)
            return result

        raise TypeError(f"Unsupported type for serialization: {type(value)}")

    def _deserialize_value(self, data: bytes, offset: int) -> tuple:
        """Deserialize a single value from bytes.

        Args:
            data: Serialized bytes
            offset: Current offset in data

        Returns:
            Tuple of (value, new_offset)

        Raises:
            ValueError: If data is malformed
        """
        if offset >= len(data):
            raise ValueError("Unexpected end of data")

        type_code = data[offset]
        offset += 1

        # None
        if type_code == TYPE_NONE:
            return None, offset

        # Boolean
        if type_code == TYPE_TRUE:
            return True, offset
        if type_code == TYPE_FALSE:
            return False, offset

        # Integer
        if type_code == TYPE_INT8:
            value = struct.unpack('<b', data[offset:offset+1])[0]
            return value, offset + 1

        if type_code == TYPE_INT16:
            value = struct.unpack('<h', data[offset:offset+2])[0]
            return value, offset + 2

        if type_code == TYPE_INT32:
            value = struct.unpack('<i', data[offset:offset+4])[0]
            return value, offset + 4

        # Float
        if type_code == TYPE_FLOAT:
            value = struct.unpack('<f', data[offset:offset+4])[0]
            return value, offset + 4

        # Bytes
        if type_code == TYPE_BYTES:
            length = struct.unpack('<H', data[offset:offset+2])[0]
            offset += 2
            value = data[offset:offset+length]
            return bytes(value), offset + length

        # String
        if type_code == TYPE_STR:
            length = struct.unpack('<H', data[offset:offset+2])[0]
            offset += 2
            value = data[offset:offset+length].decode('utf-8')
            return value, offset + length

        # List
        if type_code == TYPE_LIST:
            count = data[offset]
            offset += 1
            result = []
            for _ in range(count):
                item, offset = self._deserialize_value(data, offset)
                result.append(item)
            return result, offset

        # Dict
        if type_code == TYPE_DICT:
            count = data[offset]
            offset += 1
            result = {}
            for _ in range(count):
                key, offset = self._deserialize_value(data, offset)
                value, offset = self._deserialize_value(data, offset)
                result[key] = value
            return result, offset

        raise ValueError(f"Unknown type code: {type_code:#04x}")

    def _hash_name(self, name: str) -> int:
        """Hash function name to uint16 using DJB2 algorithm.

        DJB2 is a simple, fast hash function suitable for short strings.
        Formula: hash = ((hash << 5) + hash) + char

        Args:
            name: Function name string

        Returns:
            16-bit hash value (0x0000 - 0xFFFF)
        """
        hash_val = 5381
        for char in name:
            hash_val = ((hash_val << 5) + hash_val) + ord(char)
            hash_val &= 0xFFFFFFFF  # Keep as 32-bit
        return hash_val & 0xFFFF  # Return lower 16 bits

    def _next_call_id(self) -> int:
        """Generate unique call ID.

        Returns:
            16-bit call ID (0x0000 - 0xFFFF)
        """
        self._call_id = (self._call_id + 1) & 0xFFFF
        return self._call_id

    @property
    def stats(self) -> dict:
        """Get RPC statistics.

        Returns:
            Dict with handler count, pending call count, and call ID counter
        """
        return {
            'handlers': len(self._handlers),
            'pending_calls': len(self._pending),
            'call_id': self._call_id,
        }
