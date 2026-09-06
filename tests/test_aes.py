"""AES-128-CBC encrypt: the FIPS-197 vector and the firmware's zero-padding contract."""
import unittest

from gbox import aes


class BlockCipherTest(unittest.TestCase):
    def test_fips_197_appendix_c1(self):
        key = bytes.fromhex("000102030405060708090a0b0c0d0e0f")
        plain = bytes.fromhex("00112233445566778899aabbccddeeff")
        self.assertEqual(aes.encrypt_block(plain, key).hex(), "69c4e0d86a7b0430d8cdb78070b4c55a")

    def test_fips_197_appendix_b(self):
        key = bytes.fromhex("2b7e151628aed2a6abf7158809cf4f3c")
        plain = bytes.fromhex("3243f6a8885a308d313198a2e0370734")
        self.assertEqual(aes.encrypt_block(plain, key).hex(), "3925841d02dc09fbdc118597196a0b32")

    def test_rejects_wrong_sizes(self):
        with self.assertRaises(ValueError):
            aes.encrypt_block(b"short", b"!" * 16)
        with self.assertRaises(ValueError):
            aes.encrypt_block(b"x" * 16, b"!" * 8)


class FirmwarePasswordTest(unittest.TestCase):
    """Reference values produced with .NET AesManaged (CBC, Zeros padding, zero IV) on 2026-09-05."""

    def test_one_block_padded(self):
        self.assertEqual(aes.encrypt_password("password"), "404cb23305b12d9c10df7c38f7ef4220")

    def test_exactly_one_block_gets_no_extra_block(self):
        self.assertEqual(aes.encrypt_password("sixteen chars!!!"), "fb3a3b3f39f1fc4b43c369a94b36272a")

    def test_two_blocks_chain(self):
        self.assertEqual(aes.encrypt_password("correct horse battery"),
                         "e757a13818364365ecd07c15aa6c17790fec96f7b738dc81102374d38276acc1")

    def test_empty_password_is_one_zero_block(self):
        self.assertEqual(len(aes.encrypt_password("")), 32)

    def test_utf8(self):
        # 'ü' is two bytes, so this is 17 bytes and must span two blocks
        self.assertEqual(len(aes.encrypt_password("ü" + "a" * 15)), 64)


if __name__ == "__main__":
    unittest.main()
