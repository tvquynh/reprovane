"""Deny external sockets in the test process and inherited Python children."""
import ipaddress
import sys


def loopback(host):
    if host in ('localhost', b'localhost'):
        return True
    try:
        if isinstance(host, bytes):
            host = host.decode('ascii')
        return ipaddress.ip_address(host).is_loopback
    except (ValueError, TypeError, UnicodeError):
        return False


def install():
    def audit(event, args):
        if event in ('socket.connect', 'socket.sendto'):
            address = args[1]
            if not isinstance(address, tuple) or not loopback(address[0]):
                raise AssertionError('CI forbids external network connections')
        elif event == 'socket.getaddrinfo' and not loopback(args[0]):
            raise AssertionError('CI forbids external DNS lookups')
    sys.addaudithook(audit)
