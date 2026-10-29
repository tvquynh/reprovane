"""Regression checks for the offline CI gate, using no external requests."""
import subprocess
import sys
import unittest
from pathlib import Path
from tools.ci_network_guard import loopback

ROOT = Path(__file__).parents[1]


class TestCINetworkGuard(unittest.TestCase):
    def test_only_loopback_addresses_allowed(self):
        for host in ('127.0.0.1', '::1', 'localhost'):
            with self.subTest(host=host):self.assertTrue(loopback(host))
        for host in ('example.invalid', '192.0.2.1', '10.0.0.1', '0.0.0.0'):
            with self.subTest(host=host):self.assertFalse(loopback(host))

    def run_guard(self, action):
        code = 'from tools.ci_network_guard import install; install(); import socket; '+action
        result = subprocess.run([sys.executable, '-c', code], cwd=ROOT, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('CI forbids external', result.stderr)

    def test_external_dns_fails_before_lookup(self):
        self.run_guard("socket.getaddrinfo('example.invalid',443)")

    def test_external_connection_fails_before_connect(self):
        self.run_guard("socket.socket().connect(('192.0.2.1',443))")

    def test_external_udp_fails_before_send(self):
        self.run_guard("socket.socket(socket.AF_INET,socket.SOCK_DGRAM).sendto(b'synthetic',('192.0.2.1',443))")
