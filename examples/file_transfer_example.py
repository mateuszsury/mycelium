"""
File Transfer Example for µMesh
================================

Demonstrates file transfer over mesh network with progress tracking.

This example shows:
1. Sending files to remote nodes
2. Receiving files with callbacks
3. Progress monitoring during transfer
4. Integration with Mesh and ReliableTransport

Usage:
    # Node A (sender):
    python file_transfer_example.py --mode sender --dest 0x5678 --file config.json

    # Node B (receiver):
    python file_transfer_example.py --mode receiver
"""

import sys
import asyncio

# Add umesh to path (for development)
sys.path.insert(0, '../')

import umesh


# =============================================================================
# SENDER EXAMPLE
# =============================================================================

async def sender_example(dest_id: int, filepath: str):
    """
    Send file to remote node with progress tracking.

    Args:
        dest_id: Destination node ID (16-bit)
        filepath: Path to file to send
    """
    print(f"Initializing mesh network...")
    mesh = umesh.Mesh()
    await mesh.start()

    print(f"My node ID: 0x{mesh.node_id:04X}")
    print(f"My MAC: {mesh.mac.hex(':')}")

    # Initialize file transfer
    print(f"\nInitializing file transfer...")
    ft = umesh.FileTransfer(mesh)

    # Read file
    print(f"Reading file: {filepath}")
    try:
        with open(filepath, 'rb') as f:
            file_data = f.read()
    except FileNotFoundError:
        print(f"ERROR: File not found: {filepath}")
        await mesh.stop()
        return

    filename = filepath.split('/')[-1].split('\\')[-1]  # Get basename
    file_size = len(file_data)

    print(f"File size: {file_size} bytes")
    print(f"Filename: {filename}")

    # Progress callback
    def on_progress(bytes_sent, total_bytes):
        percent = (bytes_sent / total_bytes) * 100
        print(f"Progress: {bytes_sent}/{total_bytes} bytes ({percent:.1f}%)")

    # Send file
    print(f"\nSending file to node 0x{dest_id:04X}...")
    success = await ft.send_file(
        dest_id=dest_id,
        filename=filename,
        data=file_data,
        on_progress=on_progress
    )

    if success:
        print(f"\nFile transfer completed successfully!")
    else:
        print(f"\nFile transfer FAILED!")

    # Cleanup
    await mesh.stop()
    print("\nMesh stopped.")


# =============================================================================
# RECEIVER EXAMPLE
# =============================================================================

async def receiver_example():
    """
    Receive files from remote nodes.
    """
    print(f"Initializing mesh network...")
    mesh = umesh.Mesh()
    await mesh.start()

    print(f"My node ID: 0x{mesh.node_id:04X}")
    print(f"My MAC: {mesh.mac.hex(':')}")

    # Initialize file transfer
    print(f"\nInitializing file transfer...")
    ft = umesh.FileTransfer(mesh)

    # Register callback for received files
    def on_file_received(src_id: int, filename: str, data: bytes):
        print(f"\n" + "="*60)
        print(f"FILE RECEIVED!")
        print(f"From: 0x{src_id:04X}")
        print(f"Filename: {filename}")
        print(f"Size: {len(data)} bytes")
        print("="*60)

        # Save to disk
        save_path = f"received_{filename}"
        with open(save_path, 'wb') as f:
            f.write(data)
        print(f"Saved to: {save_path}")

    ft.on_file_received(on_file_received)

    print(f"\nWaiting for incoming files... (Press Ctrl+C to stop)")

    try:
        # Keep running
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        print("\n\nStopping...")

    # Cleanup
    await mesh.stop()
    print("Mesh stopped.")


# =============================================================================
# ADVANCED EXAMPLE - Reliable file transfer
# =============================================================================

async def reliable_sender_example(dest_id: int, filepath: str):
    """
    Send file with reliable delivery (ACK-based).

    Args:
        dest_id: Destination node ID (16-bit)
        filepath: Path to file to send
    """
    print(f"Initializing mesh network...")
    mesh = umesh.Mesh()
    await mesh.start()

    print(f"My node ID: 0x{mesh.node_id:04X}")

    # Initialize reliable transport
    print(f"Initializing reliable transport...")
    transport = umesh.ReliableTransport(mesh)

    # Initialize file transfer with reliable transport
    print(f"Initializing file transfer (reliable mode)...")
    ft = umesh.FileTransfer(mesh, transport=transport)

    # Read file
    print(f"Reading file: {filepath}")
    try:
        with open(filepath, 'rb') as f:
            file_data = f.read()
    except FileNotFoundError:
        print(f"ERROR: File not found: {filepath}")
        await mesh.stop()
        return

    filename = filepath.split('/')[-1].split('\\')[-1]

    # Progress callback
    def on_progress(bytes_sent, total_bytes):
        percent = (bytes_sent / total_bytes) * 100
        print(f"Progress: {bytes_sent}/{total_bytes} bytes ({percent:.1f}%)")

    # Send file (with retries and ACK)
    print(f"\nSending file to node 0x{dest_id:04X} (reliable)...")
    success = await ft.send_file(
        dest_id=dest_id,
        filename=filename,
        data=file_data,
        on_progress=on_progress
    )

    if success:
        print(f"\nFile transfer completed successfully!")
    else:
        print(f"\nFile transfer FAILED after retries!")

    # Cleanup
    await mesh.stop()
    print("\nMesh stopped.")


# =============================================================================
# INTERACTIVE EXAMPLE
# =============================================================================

async def interactive_example():
    """
    Interactive file transfer demo.
    """
    print("""
    ╔════════════════════════════════════════════╗
    ║   µMesh File Transfer Interactive Demo    ║
    ╚════════════════════════════════════════════╝
    """)

    mesh = umesh.Mesh()
    await mesh.start()

    print(f"Node ID: 0x{mesh.node_id:04X}")
    print(f"MAC: {mesh.mac.hex(':')}")

    ft = umesh.FileTransfer(mesh)

    # File received callback
    def on_file_received(src_id: int, filename: str, data: bytes):
        print(f"\n[RECEIVED] From 0x{src_id:04X}: {filename} ({len(data)} bytes)")
        with open(f"received_{filename}", 'wb') as f:
            f.write(data)

    ft.on_file_received(on_file_received)

    print("\nCommands:")
    print("  send <dest_id> <filepath>  - Send file to node")
    print("  neighbors                  - Show neighbor nodes")
    print("  quit                       - Exit")

    try:
        while True:
            # Show prompt
            cmd = input("\n> ").strip()

            if not cmd:
                continue

            parts = cmd.split()
            command = parts[0].lower()

            if command == 'quit' or command == 'exit':
                break

            elif command == 'send':
                if len(parts) < 3:
                    print("Usage: send <dest_id> <filepath>")
                    continue

                try:
                    # Parse dest_id (hex or decimal)
                    dest_str = parts[1]
                    if dest_str.startswith('0x'):
                        dest_id = int(dest_str, 16)
                    else:
                        dest_id = int(dest_str)

                    filepath = ' '.join(parts[2:])  # Handle spaces in path

                    # Read file
                    with open(filepath, 'rb') as f:
                        file_data = f.read()

                    filename = filepath.split('/')[-1].split('\\')[-1]

                    # Progress
                    def progress(sent, total):
                        pct = (sent / total) * 100
                        print(f"  [{pct:3.0f}%] {sent}/{total} bytes", end='\r')

                    print(f"Sending {filename} ({len(file_data)} bytes) to 0x{dest_id:04X}...")

                    success = await ft.send_file(dest_id, filename, file_data, progress)

                    if success:
                        print(f"\n  SUCCESS!")
                    else:
                        print(f"\n  FAILED!")

                except ValueError:
                    print(f"Invalid dest_id: {parts[1]}")
                except FileNotFoundError:
                    print(f"File not found: {filepath}")
                except Exception as e:
                    print(f"Error: {e}")

            elif command == 'neighbors':
                neighbors = mesh.neighbors
                if not neighbors:
                    print("No neighbors discovered yet.")
                else:
                    print(f"\nNeighbors ({len(neighbors)}):")
                    for node_id, mac, rssi, lqi in neighbors:
                        print(f"  0x{node_id:04X}  MAC:{mac.hex(':')}  RSSI:{rssi:4d} dBm  LQI:{lqi:3d}")

            else:
                print(f"Unknown command: {command}")

    except KeyboardInterrupt:
        print("\n")

    # Cleanup
    await mesh.stop()
    print("Mesh stopped. Goodbye!")


# =============================================================================
# MAIN
# =============================================================================

def main():
    """Parse arguments and run appropriate example."""
    import argparse

    parser = argparse.ArgumentParser(description='µMesh File Transfer Example')
    parser.add_argument('--mode', choices=['sender', 'receiver', 'interactive'],
                        default='interactive',
                        help='Operation mode')
    parser.add_argument('--dest', type=str,
                        help='Destination node ID (hex: 0x1234 or decimal: 4660)')
    parser.add_argument('--file', type=str,
                        help='File path to send')
    parser.add_argument('--reliable', action='store_true',
                        help='Use reliable delivery (with ACK)')

    args = parser.parse_args()

    if args.mode == 'sender':
        if not args.dest or not args.file:
            print("ERROR: --dest and --file required for sender mode")
            parser.print_help()
            return

        # Parse dest_id
        if args.dest.startswith('0x'):
            dest_id = int(args.dest, 16)
        else:
            dest_id = int(args.dest)

        # Run sender
        if args.reliable:
            asyncio.run(reliable_sender_example(dest_id, args.file))
        else:
            asyncio.run(sender_example(dest_id, args.file))

    elif args.mode == 'receiver':
        asyncio.run(receiver_example())

    elif args.mode == 'interactive':
        asyncio.run(interactive_example())


if __name__ == '__main__':
    main()
