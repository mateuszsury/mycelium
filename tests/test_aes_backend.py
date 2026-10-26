from ucryptolib import aes

def test_nist_aes128_ecb_known_answer():
    cipher = aes(bytes.fromhex('000102030405060708090a0b0c0d0e0f'), 1)
    plaintext = bytes.fromhex('00112233445566778899aabbccddeeff')
    assert cipher.encrypt(plaintext).hex() == '69c4e0d86a7b0430d8cdb78070b4c55a'
