"""Read-only discovery of this laptop's secure phone origin (never a saved hostname)."""
import os
import re
import shutil
import subprocess
import json
import ipaddress
from pathlib import Path
from urllib.parse import urlsplit


def tailscale_binary():
    configured = os.environ.get('TBOT_TAILSCALE_BIN')
    if configured:
        return shutil.which(configured)
    app = Path('/Applications/Tailscale.app/Contents/MacOS/Tailscale')
    return shutil.which('tailscale') or (str(app) if app.is_file() and os.access(app, os.X_OK) else None)


def _object(value, label):
    if not isinstance(value, dict):
        raise ValueError(f'Tailscale returned an invalid {label} object.')
    return value


def _status(binary, *arguments):
    output = subprocess.run([binary, *arguments], capture_output=True, text=True, timeout=3, env={**os.environ, 'TAILSCALE_BE_CLI': '1'})
    if output.returncode:
        raise ValueError('Tailscale status failed; open Tailscale and sign in.')
    return _object(json.loads(output.stdout), 'status')


def _proxy(handler):
    value = handler.get('Proxy', '')
    if not isinstance(value, str):
        raise ValueError('Tailscale returned an invalid proxy target.')
    return value.rstrip('/')


def _private_serve(routes, name):
    tcp = _object(routes.get('TCP', {}), 'TCP configuration')
    port = _object(tcp.get('443', {}), 'port 443 configuration')
    if port.get('HTTPS') is not True or port.get('TCPForward'):
        raise ValueError('Private HTTPS Serve must own port 443; existing TCP forwarding was not changed.')
    funnel = _object(routes.get('AllowFunnel', {}), 'Funnel configuration')
    if funnel.get(name + ':443', False) is not False:
        raise ValueError('Public Funnel is enabled on this host. Disable it explicitly before using the private robot dashboard.')
    web = _object(routes.get('Web', {}), 'Web configuration')
    own = _object(web.get(name + ':443', {}), 'host configuration')
    handlers = _object(own.get('Handlers', {}), 'Serve handlers')
    root = _object(handlers.get('/', {}), 'root handler')
    if _proxy(root) != 'http://127.0.0.1:5173':
        raise ValueError('Run ./scripts/tbot-classroom.sh to verify the private HTTPS route to this dashboard.')
    for path in ('/api', '/api/'):
        if path in handlers:
            api = _object(handlers[path], 'API handler')
            if _proxy(api) != 'http://127.0.0.1:8001':
                raise ValueError('An existing private /api route conflicts with the local gateway; no routing was changed.')


def _explicit_origin(configured):
    """Accept only an HTTPS origin, never credentials, paths or a saved tailnet."""
    if any(char.isspace() or ord(char) < 32 for char in configured) or '\\' in configured:
        raise ValueError('TBOT_PUBLIC_URL must be a valid HTTPS origin.')
    parsed = urlsplit(configured)
    host = (parsed.hostname or '').lower()
    port = parsed.port  # Also rejects malformed and out-of-range port numbers.
    if (parsed.scheme != 'https' or not host or parsed.username is not None or parsed.password is not None
            or parsed.path not in ('', '/') or parsed.query or parsed.fragment or port == 0):
        raise ValueError('TBOT_PUBLIC_URL must be an HTTPS origin without credentials, a path, query or fragment.')
    if host.rstrip('.') == 'ts.net' or host.rstrip('.').endswith('.ts.net'):
        return ''  # A .ts.net address is valid only after live discovery above.
    try:
        address = ipaddress.ip_address(host)
        rendered_host = '[' + str(address) + ']' if address.version == 6 else str(address)
    except ValueError:
        rendered_host = host.encode('idna').decode('ascii')
        if not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)*\.?', rendered_host):
            raise ValueError('TBOT_PUBLIC_URL has an invalid hostname.')
    return 'https://' + rendered_host + (':' + str(port) if port not in (None, 443) else '')


def secure_network():
    """Do not return an old .ts.net URL when Tailscale is disconnected/moved."""
    binary = tailscale_binary()
    result = {'installed': bool(binary), 'connected': False, 'dns_name': '', 'public_url': '', 'serve_configured': False, 'reason': 'Secure phone camera unavailable: Tailscale is not installed.'}
    if binary:
        try:
            state = _status(binary, 'status', '--json')
            own = _object(state.get('Self', {}), 'Self')
            name = str(own.get('DNSName') or '').rstrip('.').lower()
            if state.get('BackendState') != 'Running' or own.get('Online') is not True:
                raise ValueError('Connect and authenticate Tailscale on this laptop.')
            if not re.fullmatch(r'[a-z0-9-]+(?:\.[a-z0-9-]+)*\.ts\.net', name):
                raise ValueError('This laptop has no valid Tailscale DNS name.')
            result.update(connected=True, dns_name=name)
            _private_serve(_status(binary, 'serve', 'status', '--json'), name)
            result.update(public_url='https://' + name, serve_configured=True, reason='')
        except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
            result['reason'] = 'Secure phone camera unavailable: ' + str(exc)
    # Explicit trusted local HTTPS is supported, but a .ts.net URL must always
    # match live discovery above, never an environment value from another Mac.
    configured = os.environ.get('TBOT_PUBLIC_URL', '')
    if configured and not result['public_url']:
        try:
            origin = _explicit_origin(configured)
            if origin:
                result.update(public_url=origin, reason='', explicit_https=True)
        except (ValueError, UnicodeError) as exc:
            result['reason'] = 'Secure phone camera unavailable: ' + str(exc)
    return result
