"""Bundled Connect LAP certificates for pairing with a Lutron Connect Bridge."""

import os

_DIR = os.path.dirname(__file__)

CONNECT_LAP_CA = os.path.join(_DIR, "connect-lap-ca.crt")
CONNECT_LAP_CERT = os.path.join(_DIR, "connect-lap.crt")
CONNECT_LAP_KEY = os.path.join(_DIR, "connect-lap.key")
