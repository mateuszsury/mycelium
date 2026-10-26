"""Real AES-ECB shim for host tests; never part of the MicroPython runtime."""
import importlib.util
import sys
import types

if importlib.util.find_spec('ucryptolib') is None:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    class AES:
        def __init__(self, key, mode):
            if mode != 1:
                raise ValueError('Host adapter implements ECB only')
            self.cipher = Cipher(algorithms.AES(key), modes.ECB())

        def encrypt(self, data):
            encryptor = self.cipher.encryptor()
            return encryptor.update(data) + encryptor.finalize()

    adapter = types.ModuleType('ucryptolib')
    adapter.aes = AES
    sys.modules['ucryptolib'] = adapter
