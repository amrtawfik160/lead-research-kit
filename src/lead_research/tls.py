"""TLS contexts that keep verification on but trust the operating system's roots too.

Python builds (e.g. python.org on macOS) can ship a stale CA bundle, so valid sites fail
with "unable to get local issuer certificate". We add, never remove, trust anchors:
default paths, common Homebrew/Linux bundles, ``certifi`` if installed, and on macOS the
system root keychain exported in memory.
"""
from __future__ import annotations

import functools
import os
import shutil
import ssl
import subprocess
import sys

EXTRA_BUNDLES = ('/opt/homebrew/etc/openssl@3/cert.pem', '/usr/local/etc/openssl@3/cert.pem',
                 '/etc/ssl/certs/ca-certificates.crt', '/etc/pki/tls/certs/ca-bundle.crt', '/etc/ssl/cert.pem')
MAC_KEYCHAIN = '/System/Library/Keychains/SystemRootCertificates.keychain'


@functools.lru_cache(maxsize=1)
def _macos_roots() -> str:
    if sys.platform != 'darwin' or not shutil.which('security') or not os.path.exists(MAC_KEYCHAIN):
        return ''
    try:
        result = subprocess.run(['security', 'find-certificate', '-a', '-p', MAC_KEYCHAIN],
                                capture_output=True, text=True, timeout=15, check=False)
    except (OSError, subprocess.SubprocessError):
        return ''
    return result.stdout if result.returncode == 0 and 'BEGIN CERTIFICATE' in result.stdout else ''


def trusted_context() -> ssl.SSLContext:
    """A verifying client context with the widest legitimate set of trust anchors."""
    context = ssl.create_default_context()
    for path in EXTRA_BUNDLES:
        if os.path.exists(path):
            try:
                context.load_verify_locations(path)
            except (OSError, ssl.SSLError):
                pass
    try:
        import certifi  # optional
        context.load_verify_locations(certifi.where())
    except Exception:
        pass
    roots = _macos_roots()
    if roots:
        try:
            context.load_verify_locations(cadata=roots)
        except (ssl.SSLError, ValueError):
            pass
    return context
