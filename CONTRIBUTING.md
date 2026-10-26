# Contributing to Mycelium

Thank you for your interest in contributing to Mycelium! This guide will help you get started.

## How to Contribute

### Reporting Issues

- Use [GitHub Issues](https://github.com/mateuszsury/mycelium/issues) for bug reports and feature requests
- Include your ESP32 board variant, MicroPython version, and steps to reproduce
- For security vulnerabilities, see [SECURITY.md](SECURITY.md)

### Pull Requests

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/my-feature`)
3. Make your changes
4. Run tests (`python -m pytest tests/ -v`)
5. Commit with a descriptive message
6. Push and open a Pull Request

## Development Setup

```bash
# Clone the repository
git clone https://github.com/mateuszsury/mycelium.git
cd mycelium

# Create a virtual environment
python -m venv .venv
source .venv/bin/activate  # Linux/macOS
.venv\Scripts\activate     # Windows

# Install dev dependencies
pip install pytest

# Run tests
python -m pytest tests/ -v

# Run in-module tests
python -m pytest umesh/ -v
```

## MicroPython Compatibility Guidelines

Mycelium targets MicroPython on ESP32. All code must follow these rules:

### Import Guards

Use `try/except ImportError` for optional or platform-specific modules:

```python
try:
    from micropython import const
except ImportError:
    def const(x):
        return x
```

### Memory Budget

- Avoid large allocations in hot paths
- Pre-allocate buffers where possible
- Use `const()` for integer constants (compiles to inline values)
- Prefer `bytes` over `str` for protocol data

### Timing

Use `ticks_ms()` / `ticks_diff()` from `utime` with CPython fallback:

```python
try:
    from utime import ticks_ms, ticks_diff
except ImportError:
    from time import monotonic
    def ticks_ms():
        return int(monotonic() * 1000)
    def ticks_diff(a, b):
        return a - b
```

### Async Pattern

Use `uasyncio` with `asyncio` fallback. Avoid features not present in MicroPython's async (e.g., `asyncio.TaskGroup`).

## Code Style

- Follow existing patterns in the codebase
- Use docstrings for public classes and methods
- No type annotations (MicroPython overhead)
- Keep line length reasonable (~100 chars)
- Use `snake_case` for functions and variables
- Use `UPPER_CASE` for constants

## Commit Messages

Use [Conventional Commits](https://www.conventionalcommits.org/):

```
feat: add Long Range mode support
fix: correct RSSI two's complement conversion
docs: update packet format table
test: add routing table expiry tests
refactor: extract duplicate filter to separate module
```

## Testing

### CPython Tests

All tests run on desktop Python with MicroPython-compatible mocks:

```bash
python -m pytest tests/ -v        # Integration tests
python -m pytest umesh/ -v         # Unit tests
python -m pytest -x --tb=short     # Stop on first failure
```

### Manual ESP32 Testing

For hardware validation:

1. Flash MicroPython >= 1.20 onto 2+ ESP32 boards
2. Upload `umesh/` to each board
3. Run examples from `examples/` directory
4. Verify multi-hop routing with 3+ nodes

## Project Structure

- `umesh/` — Core library (deployed to ESP32)
- `umesh/test_*.py` — Unit tests (CPython only)
- `tests/` — Integration tests (CPython only)
- `examples/` — Example scripts (run on ESP32)
- `docs/` — Documentation

## Questions?

Open a [Discussion](https://github.com/mateuszsury/mycelium/discussions) or file an Issue.

## Authorship and contributions

Project-maintained changes use the Git identity `Mateusz Sury
<66475105+mateuszsury@users.noreply.github.com>`. Configure it locally before
committing:

```sh
git config user.name "Mateusz Sury"
git config user.email "66475105+mateuszsury@users.noreply.github.com"
git config user.useConfigOnly true
```

The maintainer takes responsibility for tool-assisted changes. Do not add AI
tools as authors or co-authors. Preserve genuine human contributions, upstream
history, copyright notices and third-party licenses. Keep release provenance
and checksums intact. Describe the purpose of each change and record relevant
validation; avoid generic upload messages and environment-specific worktree names.
