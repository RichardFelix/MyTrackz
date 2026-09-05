"""Image transport that validates the addresses it actually connects to."""

import ipaddress
import socket

import requests
from requests.adapters import HTTPAdapter
from urllib3.connection import HTTPConnection, HTTPSConnection
from urllib3.connectionpool import HTTPConnectionPool, HTTPSConnectionPool
from urllib3.exceptions import NewConnectionError


class PublicAddressConnection(HTTPConnection):
    """Connect directly to a validated address without a second DNS lookup."""

    def _new_conn(self):
        try:
            addresses = socket.getaddrinfo(
                self.host,
                self.port,
                type=socket.SOCK_STREAM,
            )
        except OSError as exc:
            raise NewConnectionError(self, str(exc)) from exc
        if not addresses or any(
            not ipaddress.ip_address(address[4][0]).is_global
            or ipaddress.ip_address(address[4][0]).is_multicast
            for address in addresses
        ):
            msg = "Image host resolves to a non-public address"
            raise NewConnectionError(self, msg)

        try:
            for index, (family, kind, proto, _, address) in enumerate(addresses):
                sock = socket.socket(family, kind, proto)
                try:
                    sock.settimeout(self.timeout)
                    for option in self.socket_options or ():
                        sock.setsockopt(*option)
                    if self.source_address:
                        sock.bind(self.source_address)
                    sock.connect(address)
                except OSError:
                    sock.close()
                    if index == len(addresses) - 1:
                        raise
                else:
                    return sock
        except OSError as exc:
            raise NewConnectionError(self, str(exc)) from exc
        return None


class PublicAddressHTTPSConnection(PublicAddressConnection, HTTPSConnection):
    """Keep the original hostname for TLS SNI and certificate verification."""


class PublicAddressHTTPPool(HTTPConnectionPool):
    """Use the validated HTTP transport."""

    ConnectionCls = PublicAddressConnection


class PublicAddressHTTPSPool(HTTPSConnectionPool):
    """Use the validated HTTPS transport."""

    ConnectionCls = PublicAddressHTTPSConnection


class ImageAdapter(HTTPAdapter):
    """Use private pool classes without altering other HTTP clients."""

    def init_poolmanager(self, *args, **kwargs):
        """Configure pools for both image URL schemes."""
        super().init_poolmanager(*args, **kwargs)
        self.poolmanager.pool_classes_by_scheme = {
            "http": PublicAddressHTTPPool,
            "https": PublicAddressHTTPSPool,
        }


def get_image(url, **kwargs):
    """Fetch an image without environment proxies or ambient credentials."""
    with requests.Session() as session:
        session.trust_env = False
        adapter = ImageAdapter()
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        return session.get(url, **kwargs)
