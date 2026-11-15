"""The keyring's secret exchange: a password encrypted by one side comes out whole on the other."""

import unittest

import keyring_exchange


class ExchangeTest(unittest.TestCase):
    def test_round_trip(self):
        if not keyring_exchange.available():
            self.skipTest("no libcrypto")
        ours, theirs = keyring_exchange.Exchange(), keyring_exchange.Exchange()
        self.assertIsNone(theirs.receive(ours.begin()))
        for secret in ("", "pässwörd", "x" * 16, "a much longer password than one block"):
            ours.receive(theirs.send())
            self.assertEqual(theirs.receive(ours.send(secret)), secret)

    def test_rejects_bad_public(self):
        with self.assertRaises(ValueError):
            keyring_exchange.Exchange().receive("[sx-aes-1]\npublic=AQ==\n")
        with self.assertRaises(ValueError):
            keyring_exchange.Exchange().receive("[other]\npublic=AQ==\n")
