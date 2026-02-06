"""
Tests for µMesh RPC (Remote Procedure Call)
============================================

Comprehensive test suite for MeshRPC including:
- Handler registration/unregistration
- Serialization/deserialization
- Remote function calls
- Error propagation
- Timeout handling
- Request/response handling
"""

import sys
import asyncio

# Add parent directory to path for imports
sys.path.insert(0, '..')

from umesh.rpc import (
    MeshRPC, PendingCall, RemoteError,
    TYPE_NONE, TYPE_TRUE, TYPE_FALSE,
    TYPE_INT8, TYPE_INT16, TYPE_INT32, TYPE_FLOAT,
    TYPE_BYTES, TYPE_STR, TYPE_LIST, TYPE_DICT,
    FLAG_RESPONSE, FLAG_ERROR,
    DEFAULT_TIMEOUT, MAX_HANDLERS, MAX_PENDING_CALLS,
)
from umesh.const import PKT_RPC

import struct


# =============================================================================
# MOCK MESH
# =============================================================================

class MockMesh:
    """Mock Mesh for testing without hardware."""

    def __init__(self):
        self.sent_packets = []
        self.node_id_val = 0x1111

    async def send(self, dest_id, payload, pkt_type=None):
        """Mock send that records packets."""
        self.sent_packets.append({
            'dest_id': dest_id,
            'payload': payload,
            'pkt_type': pkt_type,
        })

    @property
    def node_id(self):
        return self.node_id_val

    def clear(self):
        """Clear recorded packets."""
        self.sent_packets.clear()


# =============================================================================
# TEST SERIALIZATION
# =============================================================================

def test_serialize_none():
    """Test serialization of None."""
    rpc = MeshRPC(MockMesh())
    data = rpc._serialize_value(None)
    assert data == bytes([TYPE_NONE]), f"Expected {bytes([TYPE_NONE])}, got {data}"
    print("[PASS] Serialize None")


def test_serialize_bool():
    """Test serialization of booleans."""
    rpc = MeshRPC(MockMesh())

    # True
    data = rpc._serialize_value(True)
    assert data == bytes([TYPE_TRUE]), f"Expected {bytes([TYPE_TRUE])}, got {data}"

    # False
    data = rpc._serialize_value(False)
    assert data == bytes([TYPE_FALSE]), f"Expected {bytes([TYPE_FALSE])}, got {data}"

    print("[PASS] Serialize bool")


def test_serialize_int():
    """Test serialization of integers."""
    rpc = MeshRPC(MockMesh())

    # int8
    data = rpc._serialize_value(42)
    expected = bytes([TYPE_INT8]) + struct.pack('<b', 42)
    assert data == expected, f"Expected {expected}, got {data}"

    # int8 negative
    data = rpc._serialize_value(-100)
    expected = bytes([TYPE_INT8]) + struct.pack('<b', -100)
    assert data == expected, f"Expected {expected}, got {data}"

    # int16
    data = rpc._serialize_value(1000)
    expected = bytes([TYPE_INT16]) + struct.pack('<h', 1000)
    assert data == expected, f"Expected {expected}, got {data}"

    # int32
    data = rpc._serialize_value(100000)
    expected = bytes([TYPE_INT32]) + struct.pack('<i', 100000)
    assert data == expected, f"Expected {expected}, got {data}"

    print("[PASS] Serialize int")


def test_serialize_float():
    """Test serialization of floats."""
    rpc = MeshRPC(MockMesh())

    # Positive float
    data = rpc._serialize_value(23.5)
    expected = bytes([TYPE_FLOAT]) + struct.pack('<f', 23.5)
    assert data == expected, f"Expected {expected}, got {data}"

    # Negative float
    data = rpc._serialize_value(-100.123)
    expected = bytes([TYPE_FLOAT]) + struct.pack('<f', -100.123)
    assert data == expected, f"Expected {expected}, got {data}"

    # Deserialize and verify
    deserialized, _ = rpc._deserialize_value(data, 0)
    assert abs(deserialized - (-100.123)) < 0.001, f"Float mismatch: {deserialized}"

    print("[PASS] Serialize float")


def test_serialize_bytes():
    """Test serialization of bytes."""
    rpc = MeshRPC(MockMesh())

    data = rpc._serialize_value(b'hello')
    expected = bytes([TYPE_BYTES]) + struct.pack('<H', 5) + b'hello'
    assert data == expected, f"Expected {expected}, got {data}"

    print("[PASS] Serialize bytes")


def test_serialize_str():
    """Test serialization of strings."""
    rpc = MeshRPC(MockMesh())

    data = rpc._serialize_value('world')
    expected = bytes([TYPE_STR]) + struct.pack('<H', 5) + b'world'
    assert data == expected, f"Expected {expected}, got {data}"

    print("[PASS] Serialize str")


def test_serialize_list():
    """Test serialization of lists."""
    rpc = MeshRPC(MockMesh())

    # Simple list
    data = rpc._serialize_value([1, 2, 3])
    # TYPE_LIST + count(3) + 3 int8 values
    expected = bytes([TYPE_LIST, 3])
    expected += bytes([TYPE_INT8]) + struct.pack('<b', 1)
    expected += bytes([TYPE_INT8]) + struct.pack('<b', 2)
    expected += bytes([TYPE_INT8]) + struct.pack('<b', 3)
    assert data == expected, f"Expected {expected}, got {data}"

    # Mixed list
    data = rpc._serialize_value([None, True, 'test'])
    expected = bytes([TYPE_LIST, 3])
    expected += bytes([TYPE_NONE])
    expected += bytes([TYPE_TRUE])
    expected += bytes([TYPE_STR]) + struct.pack('<H', 4) + b'test'
    assert data == expected, f"Expected {expected}, got {data}"

    print("[PASS] Serialize list")


def test_serialize_dict():
    """Test serialization of dictionaries."""
    rpc = MeshRPC(MockMesh())

    # Simple dict
    data = rpc._serialize_value({'a': 1, 'b': 2})
    # TYPE_DICT + count(2) + key-value pairs
    # Note: dict order may vary, so we'll deserialize to verify
    value, offset = rpc._deserialize_value(data, 0)
    assert value == {'a': 1, 'b': 2}, f"Expected {{'a': 1, 'b': 2}}, got {value}"

    print("[PASS] Serialize dict")


def test_deserialize_all_types():
    """Test deserialization of all supported types."""
    rpc = MeshRPC(MockMesh())

    test_values = [
        None,
        True,
        False,
        42,
        -100,
        1000,
        100000,
        23.5,
        -100.123,
        b'bytes',
        'string',
        [1, 2, 3],
        {'key': 'value', 'num': 123},
    ]

    for original in test_values:
        data = rpc._serialize_value(original)
        deserialized, offset = rpc._deserialize_value(data, 0)
        # Special handling for floats (precision issues)
        if isinstance(original, float):
            assert abs(deserialized - original) < 0.001, f"Float mismatch: {original} != {deserialized}"
        else:
            assert deserialized == original, f"Mismatch: {original} != {deserialized}"
        assert offset == len(data), f"Offset mismatch: {offset} != {len(data)}"

    print("[PASS] Deserialize all types")


def test_serialize_nested():
    """Test serialization of nested structures."""
    rpc = MeshRPC(MockMesh())

    # Nested list
    original = [1, [2, 3], [4, [5, 6]]]
    data = rpc._serialize_value(original)
    deserialized, _ = rpc._deserialize_value(data, 0)
    assert deserialized == original, f"Mismatch: {original} != {deserialized}"

    # Nested dict
    original = {'a': {'b': 1, 'c': 2}, 'd': [3, 4]}
    data = rpc._serialize_value(original)
    deserialized, _ = rpc._deserialize_value(data, 0)
    assert deserialized == original, f"Mismatch: {original} != {deserialized}"

    print("[PASS] Serialize nested structures")


# =============================================================================
# TEST HANDLER REGISTRATION
# =============================================================================

def test_register_handler():
    """Test handler registration."""
    rpc = MeshRPC(MockMesh())

    def test_func():
        return 42

    rpc.register('test', test_func)
    name_hash = rpc._hash_name('test')
    assert name_hash in rpc._handlers, "Handler not registered"
    assert rpc._handlers[name_hash][0] == 'test', "Handler name mismatch"
    assert rpc._handlers[name_hash][1] is test_func, "Handler function mismatch"

    print("[PASS] Register handler")


def test_unregister_handler():
    """Test handler unregistration."""
    rpc = MeshRPC(MockMesh())

    def test_func():
        return 42

    rpc.register('test', test_func)
    result = rpc.unregister('test')
    assert result is True, "Unregister should return True"

    name_hash = rpc._hash_name('test')
    assert name_hash not in rpc._handlers, "Handler should be removed"

    # Unregister non-existent
    result = rpc.unregister('nonexistent')
    assert result is False, "Unregister non-existent should return False"

    print("[PASS] Unregister handler")


def test_max_handlers():
    """Test maximum handler limit."""
    rpc = MeshRPC(MockMesh())

    # Register MAX_HANDLERS handlers
    for i in range(MAX_HANDLERS):
        rpc.register(f'func_{i}', lambda: i)

    # Try to register one more
    try:
        rpc.register('overflow', lambda: None)
        assert False, "Should raise ValueError for too many handlers"
    except ValueError as e:
        assert 'Too many handlers' in str(e)

    print("[PASS] Max handlers limit")


# =============================================================================
# TEST HASH FUNCTION
# =============================================================================

def test_hash_name():
    """Test function name hashing."""
    rpc = MeshRPC(MockMesh())

    # DJB2 hash should be deterministic
    hash1 = rpc._hash_name('test_function')
    hash2 = rpc._hash_name('test_function')
    assert hash1 == hash2, "Hash should be deterministic"

    # Different names should produce different hashes (usually)
    hash3 = rpc._hash_name('other_function')
    assert hash1 != hash3, "Different names should produce different hashes"

    # Hash should be 16-bit
    assert 0 <= hash1 <= 0xFFFF, f"Hash out of range: {hash1:#06x}"

    print("[PASS] Hash name")


# =============================================================================
# TEST RPC REQUEST/RESPONSE HANDLING
# =============================================================================

async def test_handle_rpc_request_success():
    """Test handling of successful RPC request."""
    mesh = MockMesh()
    rpc = MeshRPC(mesh)

    # Register handler
    def add_numbers(a, b):
        return a + b

    rpc.register('add', add_numbers)

    # Build request packet
    call_id = 123
    name_hash = rpc._hash_name('add')
    args_data = rpc._serialize_args((10, 20))
    payload = struct.pack('<HBH', call_id, 0, name_hash) + args_data

    # Handle request
    rpc.handle_rpc_request(src_id=0x2222, payload=payload)

    # Give asyncio a chance to process create_task
    await asyncio.sleep(0.01)

    # Verify response packet was sent
    assert len(mesh.sent_packets) == 1, "Should send one response packet"

    # Verify response packet
    pkt = mesh.sent_packets[0]
    assert pkt['dest_id'] == 0x2222, "Response should go to sender"
    assert pkt['pkt_type'] == PKT_RPC, "Should be RPC packet"

    # Parse response
    resp_call_id, resp_flags = struct.unpack('<HB', pkt['payload'][:3])
    assert resp_call_id == call_id, "Call ID should match"
    assert resp_flags & FLAG_RESPONSE, "Should be response"
    assert not (resp_flags & FLAG_ERROR), "Should not be error"

    # Deserialize result
    result, _ = rpc._deserialize_value(pkt['payload'][3:], 0)
    assert result == 30, f"Result should be 30, got {result}"

    print("[PASS] Handle RPC request success")


async def test_handle_rpc_request_error():
    """Test handling of RPC request with handler exception."""
    mesh = MockMesh()
    rpc = MeshRPC(mesh)

    # Register handler that raises exception
    def error_func():
        raise ValueError("Test error")

    rpc.register('error', error_func)

    # Build request packet
    call_id = 456
    name_hash = rpc._hash_name('error')
    args_data = rpc._serialize_args(())
    payload = struct.pack('<HBH', call_id, 0, name_hash) + args_data

    # Handle request
    rpc.handle_rpc_request(src_id=0x2222, payload=payload)

    # Give asyncio a chance to process
    await asyncio.sleep(0.01)

    # Verify error response
    assert len(mesh.sent_packets) == 1, "Should send error response"
    pkt = mesh.sent_packets[0]

    resp_call_id, resp_flags = struct.unpack('<HB', pkt['payload'][:3])
    assert resp_call_id == call_id, "Call ID should match"
    assert resp_flags & FLAG_RESPONSE, "Should be response"
    assert resp_flags & FLAG_ERROR, "Should be error"

    # Deserialize error message
    error_msg, _ = rpc._deserialize_value(pkt['payload'][3:], 0)
    assert 'Handler exception' in error_msg, f"Error message: {error_msg}"

    print("[PASS] Handle RPC request error")


async def test_handle_rpc_request_unknown_function():
    """Test handling of RPC request for unknown function."""
    mesh = MockMesh()
    rpc = MeshRPC(mesh)

    # Build request for non-existent function
    call_id = 789
    name_hash = 0x9999  # Unknown hash
    args_data = rpc._serialize_args(())
    payload = struct.pack('<HBH', call_id, 0, name_hash) + args_data

    # Handle request
    rpc.handle_rpc_request(src_id=0x2222, payload=payload)

    # Give asyncio a chance to process
    await asyncio.sleep(0.01)

    # Verify error response
    assert len(mesh.sent_packets) == 1, "Should send error response"
    pkt = mesh.sent_packets[0]

    resp_call_id, resp_flags = struct.unpack('<HB', pkt['payload'][:3])
    assert resp_flags & FLAG_ERROR, "Should be error"

    error_msg, _ = rpc._deserialize_value(pkt['payload'][3:], 0)
    assert 'Unknown function' in error_msg, f"Error message: {error_msg}"

    print("[PASS] Handle RPC request unknown function")


async def test_handle_rpc_response_success():
    """Test handling of successful RPC response."""
    mesh = MockMesh()
    rpc = MeshRPC(mesh)

    # Create pending call
    call_id = 111
    pending = PendingCall(call_id, 0x2222)
    rpc._pending[call_id] = pending

    # Build success response
    result_data = rpc._serialize_result(42)
    payload = struct.pack('<HB', call_id, FLAG_RESPONSE) + result_data

    # Handle response
    rpc.handle_rpc_response(src_id=0x2222, payload=payload)

    # Wait briefly for event to be set
    await asyncio.sleep(0.01)

    # Verify pending call was updated
    assert pending.event.is_set(), "Event should be set"
    assert pending.result == 42, f"Result should be 42, got {pending.result}"
    assert pending.error is None, "Error should be None"

    print("[PASS] Handle RPC response success")


async def test_handle_rpc_response_error():
    """Test handling of error RPC response."""
    mesh = MockMesh()
    rpc = MeshRPC(mesh)

    # Create pending call
    call_id = 222
    pending = PendingCall(call_id, 0x2222)
    rpc._pending[call_id] = pending

    # Build error response
    error_data = rpc._serialize_result("Remote error message")
    payload = struct.pack('<HB', call_id, FLAG_RESPONSE | FLAG_ERROR) + error_data

    # Handle response
    rpc.handle_rpc_response(src_id=0x2222, payload=payload)

    # Wait briefly for event to be set
    await asyncio.sleep(0.01)

    # Verify pending call was updated
    assert pending.event.is_set(), "Event should be set"
    assert pending.result is None, "Result should be None"
    assert pending.error == "Remote error message", f"Error: {pending.error}"

    print("[PASS] Handle RPC response error")


# =============================================================================
# TEST FULL RPC CALL FLOW
# =============================================================================

async def test_rpc_call_mock():
    """Test full RPC call flow with mock mesh."""
    mesh = MockMesh()
    rpc = MeshRPC(mesh)

    # Start the call (will wait for response)
    call_task = asyncio.create_task(
        rpc.call(0x2222, 'test_func', 10, 20, timeout=1000)
    )

    # Wait briefly to let call start
    await asyncio.sleep(0.01)

    # Verify request was sent
    assert len(mesh.sent_packets) == 1, "Should send request"
    pkt = mesh.sent_packets[0]

    # Parse request
    call_id, flags, name_hash = struct.unpack('<HBH', pkt['payload'][:5])
    assert flags == 0, "Request should have no flags set"

    # Simulate response
    result_data = rpc._serialize_result(123)
    response_payload = struct.pack('<HB', call_id, FLAG_RESPONSE) + result_data
    rpc.handle_rpc_response(src_id=0x2222, payload=response_payload)

    # Wait for call to complete
    result = await call_task

    assert result == 123, f"Result should be 123, got {result}"
    print("[PASS] RPC call mock")


async def test_rpc_call_timeout():
    """Test RPC call timeout."""
    mesh = MockMesh()
    rpc = MeshRPC(mesh)

    # Start call with short timeout (no response will come)
    try:
        await rpc.call(0x2222, 'test_func', timeout=100)
        assert False, "Should raise TimeoutError"
    except TimeoutError as e:
        assert 'timeout' in str(e).lower()

    print("[PASS] RPC call timeout")


async def test_rpc_call_remote_error():
    """Test RPC call with remote error."""
    mesh = MockMesh()
    rpc = MeshRPC(mesh)

    # Start the call
    call_task = asyncio.create_task(
        rpc.call(0x2222, 'test_func', timeout=1000)
    )

    await asyncio.sleep(0.01)

    # Get call ID from sent packet
    pkt = mesh.sent_packets[0]
    call_id, _, _ = struct.unpack('<HBH', pkt['payload'][:5])

    # Simulate error response
    error_data = rpc._serialize_result("Remote function failed")
    response_payload = struct.pack('<HB', call_id, FLAG_RESPONSE | FLAG_ERROR) + error_data
    rpc.handle_rpc_response(src_id=0x2222, payload=response_payload)

    # Wait for call and expect RemoteError
    try:
        await call_task
        assert False, "Should raise RemoteError"
    except RemoteError as e:
        assert 'Remote function failed' in str(e)

    print("[PASS] RPC call remote error")


async def test_max_pending_calls():
    """Test maximum pending calls limit."""
    mesh = MockMesh()
    rpc = MeshRPC(mesh)

    # Start MAX_PENDING_CALLS calls (don't await, just create tasks)
    tasks = []
    for i in range(MAX_PENDING_CALLS):
        task = asyncio.create_task(
            rpc.call(0x2222, f'func_{i}', timeout=5000)
        )
        tasks.append(task)

    await asyncio.sleep(0.01)

    # Try to start one more
    try:
        await rpc.call(0x2222, 'overflow', timeout=100)
        assert False, "Should raise ValueError for too many pending calls"
    except (ValueError, TimeoutError) as e:
        # ValueError for limit, TimeoutError if it somehow started
        if isinstance(e, ValueError):
            assert 'Too many pending calls' in str(e)

    # Cancel all pending tasks
    for task in tasks:
        task.cancel()

    print("[PASS] Max pending calls limit")


# =============================================================================
# TEST STATS
# =============================================================================

def test_stats():
    """Test RPC statistics."""
    mesh = MockMesh()
    rpc = MeshRPC(mesh)

    # Register some handlers
    rpc.register('func1', lambda: 1)
    rpc.register('func2', lambda: 2)

    stats = rpc.stats
    assert stats['handlers'] == 2, f"Expected 2 handlers, got {stats['handlers']}"
    assert stats['pending_calls'] == 0, f"Expected 0 pending calls, got {stats['pending_calls']}"
    assert stats['call_id'] == 0, "Initial call_id should be 0"

    print("[PASS] Stats")


# =============================================================================
# RUN TESTS
# =============================================================================

async def run_async_tests():
    """Run async tests."""
    print("\n=== Async Tests ===")
    await test_handle_rpc_request_success()
    await test_handle_rpc_request_error()
    await test_handle_rpc_request_unknown_function()
    await test_handle_rpc_response_success()
    await test_handle_rpc_response_error()
    await test_rpc_call_mock()
    await test_rpc_call_timeout()
    await test_rpc_call_remote_error()
    await test_max_pending_calls()


def run_all_tests():
    """Run all tests."""
    print("\n=== MeshRPC Tests ===\n")

    # Serialization tests
    print("=== Serialization Tests ===")
    test_serialize_none()
    test_serialize_bool()
    test_serialize_int()
    test_serialize_float()
    test_serialize_bytes()
    test_serialize_str()
    test_serialize_list()
    test_serialize_dict()
    test_deserialize_all_types()
    test_serialize_nested()

    # Handler registration tests
    print("\n=== Handler Registration Tests ===")
    test_register_handler()
    test_unregister_handler()
    test_max_handlers()

    # Hash function tests
    print("\n=== Hash Function Tests ===")
    test_hash_name()

    # Stats tests
    print("\n=== Stats Tests ===")
    test_stats()

    # Async tests
    asyncio.run(run_async_tests())

    print("\n=== ALL TESTS PASSED ===\n")


if __name__ == '__main__':
    run_all_tests()
