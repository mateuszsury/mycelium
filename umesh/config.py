"""
µMesh Configuration
===================

Configuration class for mesh network parameters with validation.
Supports auto-configuration and explicit parameter override.
"""

from . import const as C


class MeshConfig:
    """
    Mesh network configuration with validation.

    All timing values are in milliseconds.

    Attributes:
        node_id: 16-bit node identifier (auto-derived from MAC if None)
        channel: WiFi channel (1-14)
        lr_mode: Enable ESP-NOW Long Range mode (reduces throughput)
        hello_interval: HELLO beacon interval in ms
        route_timeout: Route entry expiration time in ms
        max_ttl: Maximum Time-To-Live for packets (1-15)
        peer_slots: Maximum ESP-NOW peer slots (1-19)
        rx_buf: Receive buffer size in bytes
        duplicate_window: Duplicate detection window size
        enable_encryption: Enable payload encryption
        psk: Pre-Shared Key for encryption (must be 16 bytes if enabled)
    """

    def __init__(
        self,
        node_id: int = None,
        channel: int = 1,
        lr_mode: bool = False,
        hello_interval: int = 10000,
        route_timeout: int = 120000,
        max_ttl: int = 7,
        peer_slots: int = 19,
        rx_buf: int = 2048,
        duplicate_window: int = 32,
        enable_encryption: bool = False,
        psk: bytes = None,
    ):
        """
        Initialize mesh configuration.

        Args:
            node_id: Node identifier (auto from MAC if None)
            channel: WiFi channel (1-14)
            lr_mode: Enable Long Range mode
            hello_interval: HELLO beacon interval (ms)
            route_timeout: Route timeout (ms)
            max_ttl: Maximum TTL (1-15)
            peer_slots: Max peer slots (1-19)
            rx_buf: RX buffer size (bytes)
            duplicate_window: Duplicate detection window size
            enable_encryption: Enable encryption
            psk: Pre-Shared Key (16 bytes if encryption enabled)

        Raises:
            ValueError: If configuration parameters are invalid
        """
        self.node_id = node_id
        self.channel = channel
        self.lr_mode = lr_mode
        self.hello_interval = hello_interval
        self.route_timeout = route_timeout
        self.max_ttl = max_ttl
        self.peer_slots = peer_slots
        self.rx_buf = rx_buf
        self.duplicate_window = duplicate_window
        self.enable_encryption = enable_encryption
        self.psk = psk

        # Validate configuration on creation
        self.validate()

    def validate(self) -> None:
        """
        Validate configuration parameters.

        Raises:
            ValueError: If any parameter is invalid
        """
        # Channel validation (WiFi channels 1-14)
        if not (1 <= self.channel <= 14):
            raise ValueError(f"channel must be 1-14, got {self.channel}")

        # TTL validation (4-bit field, 1-15)
        if not (1 <= self.max_ttl <= C.MAX_TTL):
            raise ValueError(f"max_ttl must be 1-{C.MAX_TTL}, got {self.max_ttl}")

        # Peer slots validation (ESP-NOW limitation: max 20 peers total)
        if not (1 <= self.peer_slots <= C.MAX_PEERS):
            raise ValueError(f"peer_slots must be 1-{C.MAX_PEERS}, got {self.peer_slots}")

        # Duplicate window must be positive
        if self.duplicate_window <= 0:
            raise ValueError(f"duplicate_window must be > 0, got {self.duplicate_window}")

        # Encryption validation
        if self.enable_encryption:
            if self.psk is None:
                raise ValueError("psk required when enable_encryption=True")
            if not isinstance(self.psk, bytes):
                raise ValueError(f"psk must be bytes, got {type(self.psk).__name__}")
            if len(self.psk) != 16:
                raise ValueError(f"psk must be 16 bytes, got {len(self.psk)}")

        # Node ID validation (if provided)
        if self.node_id is not None:
            if not (0 <= self.node_id <= 0xFFFF):
                raise ValueError(f"node_id must be 0-65535, got {self.node_id}")
            if self.node_id == C.BROADCAST_ID:
                raise ValueError(f"node_id cannot be broadcast address (0xFFFF)")

        # Timing validation
        if self.hello_interval <= 0:
            raise ValueError(f"hello_interval must be > 0, got {self.hello_interval}")

        if self.route_timeout <= 0:
            raise ValueError(f"route_timeout must be > 0, got {self.route_timeout}")

        # Buffer size validation
        if self.rx_buf < 512:
            raise ValueError(f"rx_buf should be >= 512 bytes, got {self.rx_buf}")

    def __repr__(self) -> str:
        """String representation for debugging."""
        node_id_str = f"0x{self.node_id:04X}" if self.node_id is not None else "auto"
        psk_str = f"{len(self.psk)}B" if self.psk else "None"

        return (
            f"MeshConfig(node_id={node_id_str}, channel={self.channel}, "
            f"lr_mode={self.lr_mode}, hello_interval={self.hello_interval}ms, "
            f"route_timeout={self.route_timeout}ms, max_ttl={self.max_ttl}, "
            f"peer_slots={self.peer_slots}, rx_buf={self.rx_buf}, "
            f"duplicate_window={self.duplicate_window}, "
            f"enable_encryption={self.enable_encryption}, psk={psk_str})"
        )
