"""Pair with a Lutron Connect Bridge using the bundled Connect LAP certificates."""

from __future__ import annotations

import asyncio
import logging
import socket
import ssl
import tempfile
import os
from typing import Callable, Optional, Tuple, TypedDict

import orjson
from cryptography import x509
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from .certs import CONNECT_LAP_CA, CONNECT_LAP_CERT, CONNECT_LAP_KEY
from .const import PAIRING_PORT, LEAP_PORT

_LOGGER = logging.getLogger(__name__)

CERT_COMMON_NAME = "lutron_connect"
CERT_SUBJECT = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, CERT_COMMON_NAME)])

SOCKET_TIMEOUT = 10
BUTTON_PRESS_TIMEOUT = 180

PAIR_KEY = "key"
PAIR_CERT = "cert"
PAIR_CA = "ca"
PAIR_VERSION = "version"


class PairingData(TypedDict):
    """Certificate data returned by a successful pairing."""

    key: str
    cert: str
    ca: str
    version: str


class _JsonSocket:
    def __init__(self, reader, writer):
        self._reader = reader
        self._writer = writer

    async def read_json(self, timeout):
        async with asyncio.timeout(timeout):
            line = await self._reader.readline()
        if line == b"":
            return None
        _LOGGER.debug("recv: %s", line)
        return orjson.loads(line)

    async def write_json(self, obj):
        buf = orjson.dumps(obj)
        self._writer.writelines((buf, b"\r\n"))
        _LOGGER.debug("send: %s", buf)

    def __del__(self):
        self._writer.close()


async def async_pair(
    bridge_ip: str,
    ready: Optional[Callable[[], None]] = None,
) -> PairingData:
    """Pair with a Connect Bridge.

    Connects to port 8083 using the bundled Connect LAP cert, waits for the user to
    press the physical button, submits a CSR, and returns the signed cert + CA.
    Then verifies the cert by pinging port 8090.
    """
    loop = asyncio.get_running_loop()
    private_key = await loop.run_in_executor(None, _generate_private_key)
    key_pem = await loop.run_in_executor(None, _key_to_pem, private_key)
    csr = await loop.run_in_executor(None, _generate_csr, private_key)

    ssl_ctx = await loop.run_in_executor(None, _build_lap_ssl_context)
    cert_pem, ca_pem = await _request_certificate(bridge_ip, ssl_ctx, csr, ready)

    signed_ctx = await loop.run_in_executor(
        None, _build_signed_ssl_context, key_pem, cert_pem, ca_pem
    )
    version = await _verify_and_ping(bridge_ip, signed_ctx)

    _LOGGER.debug("Connect Bridge LEAP version %s", version)
    return {
        PAIR_KEY: key_pem.decode("ASCII"),
        PAIR_CERT: cert_pem,
        PAIR_CA: ca_pem,
        PAIR_VERSION: version,
    }


async def _request_certificate(
    bridge_ip: str,
    ssl_ctx: ssl.SSLContext,
    csr: x509.CertificateSigningRequest,
    ready: Optional[Callable[[], None]],
) -> Tuple[str, str]:
    async with asyncio.timeout(SOCKET_TIMEOUT):
        reader, writer = await asyncio.open_connection(
            bridge_ip,
            PAIRING_PORT,
            server_hostname="",
            ssl=ssl_ctx,
            family=socket.AF_INET,
        )

    sock = _JsonSocket(reader, writer)

    _LOGGER.info("Waiting for physical button press on Connect Bridge at %s…", bridge_ip)
    if ready is not None:
        ready()

    while True:
        msg = await sock.read_json(BUTTON_PRESS_TIMEOUT)
        if msg is None:
            raise RuntimeError("Bridge closed connection before button press")
        if msg.get("Header", {}).get("ContentType", "").startswith("status;"):
            perms = msg.get("Body", {}).get("Status", {}).get("Permissions", [])
            if "PhysicalAccess" in perms:
                break

    csr_text = csr.public_bytes(serialization.Encoding.PEM).decode("ASCII")
    await sock.write_json(
        {
            "Header": {
                "RequestType": "Execute",
                "Url": "/pair",
                "ClientTag": "get-cert",
            },
            "Body": {
                "CommandType": "CSR",
                "Parameters": {
                    "CSR": csr_text,
                    "DisplayName": CERT_COMMON_NAME,
                    "DeviceUID": "000000000000",
                    "Role": "Admin",
                },
            },
        }
    )

    while True:
        msg = await sock.read_json(SOCKET_TIMEOUT)
        if msg is None:
            raise RuntimeError("Bridge closed connection during CSR exchange")
        if msg.get("Header", {}).get("ClientTag") == "get-cert":
            break

    result = msg["Body"]["SigningResult"]
    return result["Certificate"], result["RootCertificate"]


async def _verify_and_ping(bridge_ip: str, ssl_ctx: ssl.SSLContext) -> str:
    async with asyncio.timeout(SOCKET_TIMEOUT):
        reader, writer = await asyncio.open_connection(
            bridge_ip,
            LEAP_PORT,
            server_hostname="",
            ssl=ssl_ctx,
            family=socket.AF_INET,
        )
    sock = _JsonSocket(reader, writer)
    await sock.write_json(
        {"CommuniqueType": "ReadRequest", "Header": {"Url": "/server/1/status/ping"}}
    )
    while True:
        msg = await sock.read_json(SOCKET_TIMEOUT)
        if msg and msg.get("CommuniqueType") == "ReadResponse":
            return msg["Body"]["PingResponse"]["LEAPVersion"]


def _generate_private_key():
    return rsa.generate_private_key(
        public_exponent=65537, key_size=2048, backend=default_backend()
    )


def _key_to_pem(private_key) -> bytes:
    return private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )


def _generate_csr(private_key) -> x509.CertificateSigningRequest:
    return (
        x509.CertificateSigningRequestBuilder()
        .subject_name(CERT_SUBJECT)
        .sign(private_key, hashes.SHA256(), default_backend())
    )


def _build_lap_ssl_context() -> ssl.SSLContext:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.load_verify_locations(CONNECT_LAP_CA)
    ctx.load_cert_chain(CONNECT_LAP_CERT, CONNECT_LAP_KEY)
    ctx.verify_mode = ssl.CERT_REQUIRED
    return ctx


def _build_signed_ssl_context(key_pem: bytes, cert_pem: str, ca_pem: str) -> ssl.SSLContext:
    with tempfile.NamedTemporaryFile(delete=False) as kf:
        with tempfile.NamedTemporaryFile(delete=False) as cf:
            try:
                kf.write(key_pem)
                kf.flush()
                cf.write(cert_pem.encode("ASCII"))
                cf.flush()

                ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
                ctx.check_hostname = False
                ctx.load_verify_locations(cadata=ca_pem)
                ctx.load_cert_chain(cf.name, kf.name)
                ctx.verify_mode = ssl.CERT_REQUIRED
                return ctx
            finally:
                kf.close()
                cf.close()
                os.remove(kf.name)
                os.remove(cf.name)
