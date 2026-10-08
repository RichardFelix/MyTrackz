"""Regression coverage for image transport address validation."""

import socket
import ssl
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase
from urllib3.exceptions import NewConnectionError

from app.image_download import (
    ImageAdapter,
    PublicAddressConnection,
    PublicAddressHTTPSConnection,
    PublicAddressHTTPSPool,
    get_image,
)


class ImageTransportTests(SimpleTestCase):
    """Exercise the actual socket boundary without making network requests."""

    @patch("app.image_download.socket.socket")
    @patch("app.image_download.socket.getaddrinfo")
    def test_temporary_dual_stack_dns_failure_uses_validated_ipv4(
        self, resolve, make_socket
    ):
        """An AAAA resolver failure need not block a working public A record."""
        public = (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))
        resolve.side_effect = [socket.gaierror(socket.EAI_AGAIN, "Try again"), [public]]
        connection = PublicAddressHTTPSConnection("images.example", timeout=3)
        connection._new_conn()
        self.assertEqual(resolve.call_count, 2)
        resolve.assert_called_with(
            "images.example", 443, family=socket.AF_INET, type=socket.SOCK_STREAM
        )
        make_socket.return_value.connect.assert_called_once_with(public[4])
        self.assertEqual(connection.host, "images.example")

    @patch("app.image_download.socket.socket")
    @patch("app.image_download.socket.getaddrinfo")
    def test_ipv4_dns_fallback_still_rejects_private_addresses(
        self, resolve, make_socket
    ):
        """Fallback must not bypass protection against private and mixed answers."""
        public = (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))
        private = (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))
        for addresses in [[private], [public, private]]:
            with self.subTest(addresses=addresses):
                resolve.side_effect = [
                    socket.gaierror(socket.EAI_AGAIN, "Try again"),
                    addresses,
                ]
                with self.assertRaises(NewConnectionError):
                    PublicAddressHTTPSConnection("images.example")._new_conn()
        make_socket.assert_not_called()

    @patch("app.image_download.socket.socket")
    @patch("app.image_download.socket.getaddrinfo")
    def test_missing_dns_name_does_not_trigger_fallback(self, resolve, make_socket):
        """Permanent DNS errors retain the existing failure behavior."""
        resolve.side_effect = socket.gaierror(socket.EAI_NONAME, "Name not known")
        with self.assertRaises(NewConnectionError):
            PublicAddressHTTPSConnection("images.example")._new_conn()
        resolve.assert_called_once()
        make_socket.assert_not_called()

    @patch("app.image_download.socket.socket")
    @patch("app.image_download.socket.getaddrinfo")
    def test_both_dns_lookups_failing_does_not_connect(self, resolve, make_socket):
        """DNS fallback failure is reported without creating a socket."""
        resolve.side_effect = socket.gaierror(socket.EAI_AGAIN, "Try again")
        with self.assertRaises(NewConnectionError):
            PublicAddressHTTPSConnection("images.example")._new_conn()
        self.assertEqual(resolve.call_count, 2)
        make_socket.assert_not_called()

    @patch("app.image_download.socket.socket")
    @patch("app.image_download.socket.getaddrinfo")
    def test_connects_to_validated_address_once(self, resolve, make_socket):
        """A later DNS answer cannot replace the validated address."""
        public = (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))
        private = (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))
        resolve.side_effect = [[public], [private]]
        connection = PublicAddressHTTPSConnection("images.example", timeout=3)
        connection._new_conn()
        resolve.assert_called_once()
        make_socket.return_value.connect.assert_called_once_with(public[4])
        self.assertEqual(connection.host, "images.example")
        self.assertIs(
            ImageAdapter().poolmanager.pool_classes_by_scheme["https"],
            PublicAddressHTTPSPool,
        )

    @patch("app.image_download.socket.socket")
    @patch("app.image_download.socket.getaddrinfo")
    def test_rejects_nonpublic_addresses_including_mixed_dns(
        self, resolve, make_socket
    ):
        """Reject private IPv4/IPv6 even alongside a public DNS answer."""
        for address in [
            "127.0.0.1",
            "169.254.169.254",
            "10.0.0.1",
            "::1",
            "fc00::1",
            "::ffff:127.0.0.1",
        ]:
            with self.subTest(address=address):
                resolve.return_value = [
                    (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 80)),
                    (socket.AF_INET6, socket.SOCK_STREAM, 6, "", (address, 80)),
                ]
                with self.assertRaises(NewConnectionError):
                    PublicAddressConnection("images.example", timeout=3)._new_conn()
        make_socket.assert_not_called()

    @patch("app.image_download.socket.socket")
    @patch("app.image_download.socket.getaddrinfo")
    def test_falls_back_to_next_validated_address(self, resolve, make_socket):
        """An unreachable address does not prevent trying other public addresses."""
        resolve.return_value = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 80))
            for address in ["93.184.216.34", "93.184.216.35"]
        ]
        failed, working = MagicMock(), MagicMock()
        failed.connect.side_effect = OSError("unreachable")
        make_socket.side_effect = [failed, working]
        self.assertIs(
            PublicAddressConnection("images.example", timeout=3)._new_conn(), working
        )
        failed.close.assert_called_once()
        working.connect.assert_called_once_with(("93.184.216.35", 80))

    @patch("app.image_download.requests.Session")
    def test_disables_environment_proxy_and_credentials(self, session_class):
        """Proxies must not bypass address validation."""
        session = session_class.return_value.__enter__.return_value
        get_image("https://images.example/a.jpg", allow_redirects=False)
        self.assertFalse(session.trust_env)
        session.get.assert_called_once_with(
            "https://images.example/a.jpg", allow_redirects=False
        )

    @patch("urllib3.connection._ssl_wrap_socket_and_match_hostname")
    @patch("app.image_download.socket.socket")
    @patch("app.image_download.socket.getaddrinfo")
    def test_https_verifies_original_hostname(self, resolve, make_socket, wrap_tls):
        """Address pinning must preserve SNI and certificate verification."""
        resolve.return_value = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
        ]
        connection = PublicAddressHTTPSConnection("images.example", timeout=3)
        connection.connect()
        make_socket.return_value.connect.assert_called_once_with(("93.184.216.34", 443))
        self.assertEqual(wrap_tls.call_args.kwargs["server_hostname"], "images.example")
        self.assertEqual(wrap_tls.call_args.kwargs["cert_reqs"], ssl.CERT_REQUIRED)
        self.assertIsNone(wrap_tls.call_args.kwargs["assert_hostname"])
