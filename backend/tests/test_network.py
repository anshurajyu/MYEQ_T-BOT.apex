"""Read-only network discovery: mock Tailscale, never configure or expose a host."""
import json
import subprocess
from copy import deepcopy
from types import SimpleNamespace

import pytest

from backend import network


NAME = 'class-laptop.tail123.ts.net'


def online(name=NAME):
    return {'BackendState': 'Running', 'Self': {'Online': True, 'DNSName': name + '.'}}


def routes(name=NAME):
    return {'TCP': {'443': {'HTTPS': True}}, 'Web': {name + ':443': {'Handlers': {'/': {'Proxy': 'http://127.0.0.1:5173'}}}}}


@pytest.fixture
def tailnet(monkeypatch):
    monkeypatch.delenv('TBOT_PUBLIC_URL', raising=False)
    monkeypatch.setattr(network, 'tailscale_binary', lambda: '/installed/tailscale')
    state = {'status': online(), 'serve': routes(), 'calls': []}
    def run(args, **kwargs):
        state['calls'].append(args)
        assert kwargs['env']['TAILSCALE_BE_CLI'] == '1'
        assert kwargs['timeout'] == 3
        assert args in (['/installed/tailscale', 'status', '--json'], ['/installed/tailscale', 'serve', 'status', '--json'])
        value = state['status' if args[1] == 'status' else 'serve']
        if isinstance(value, Exception):
            raise value
        return SimpleNamespace(returncode=0, stdout=json.dumps(value))
    monkeypatch.setattr(network.subprocess, 'run', run)
    return state


def test_private_root_only_serve_is_discovered_without_mutation(tailnet):
    before = deepcopy(tailnet)
    status = network.secure_network()
    assert status['public_url'] == 'https://' + NAME
    assert status['connected'] and status['serve_configured']
    assert status['reason'] == ''
    assert tailnet['serve'] == before['serve']
    assert len(tailnet['calls']) == 2


def test_correct_existing_api_and_unrelated_routes_remain_acceptable(tailnet):
    handlers = tailnet['serve']['Web'][NAME + ':443']['Handlers']
    handlers.update({'/api': {'Proxy': 'http://127.0.0.1:8001/'}, '/unrelated': {'Proxy': 'http://127.0.0.1:9999'}})
    tailnet['serve']['AllowFunnel'] = {'different-laptop.tail123.ts.net:443': True}
    assert network.secure_network()['public_url'] == 'https://' + NAME
    assert handlers['/unrelated']['Proxy'] == 'http://127.0.0.1:9999'


def test_hostname_change_uses_live_host_instead_of_old_environment(tailnet, monkeypatch):
    monkeypatch.setenv('TBOT_PUBLIC_URL', 'https://' + NAME)
    assert network.secure_network()['public_url'] == 'https://' + NAME
    changed = 'replacement-laptop.tail456.ts.net'
    tailnet.update(status=online(changed), serve=routes(changed))
    assert network.secure_network()['public_url'] == 'https://' + changed


@pytest.mark.parametrize('state', ['NeedsLogin', 'Stopped', 'Starting'])
def test_not_running_never_returns_a_phone_origin(tailnet, state):
    tailnet['status']['BackendState'] = state
    result = network.secure_network()
    assert result['public_url'] == '' and not result['connected']
    assert len(tailnet['calls']) == 1


@pytest.mark.parametrize('online_value', [False, None, 1, 'true'])
def test_requires_explicit_self_online_true(tailnet, online_value):
    if online_value is None:
        tailnet['status']['Self'].pop('Online')
    else:
        tailnet['status']['Self']['Online'] = online_value
    assert network.secure_network()['public_url'] == ''


def test_missing_tailscale_does_not_reuse_saved_tsnet_url(monkeypatch):
    monkeypatch.setenv('TBOT_PUBLIC_URL', 'https://old-machine.tail123.ts.net')
    monkeypatch.setattr(network, 'tailscale_binary', lambda: None)
    monkeypatch.setattr(network.subprocess, 'run', lambda *args, **kwargs: pytest.fail('No client means no command'))
    status = network.secure_network()
    assert status['public_url'] == '' and not status['installed']
    assert 'not installed' in status['reason']


@pytest.mark.parametrize('phase,value', [('status', []), ('status', None), ('status', {'Self': None}), ('serve', []), ('serve', {'TCP': []})])
def test_invalid_json_shapes_return_unavailable_instead_of_raising(tailnet, phase, value):
    tailnet[phase] = value
    result = network.secure_network()
    assert result['public_url'] == ''
    assert 'unavailable' in result['reason']


@pytest.mark.parametrize('payload,code', [('not json', 0), ('', 1)])
def test_bad_json_or_failed_cli_is_reported(tailnet, monkeypatch, payload, code):
    monkeypatch.setattr(network.subprocess, 'run', lambda *args, **kwargs: SimpleNamespace(stdout=payload, returncode=code))
    assert network.secure_network()['public_url'] == ''


def test_cli_timeout_is_a_reported_failure(tailnet):
    tailnet['status'] = subprocess.TimeoutExpired('tailscale', 3)
    assert network.secure_network()['public_url'] == ''


@pytest.mark.parametrize('case', ['missing_https', 'http', 'tcp_forward', 'public_funnel', 'wrong_root', 'wrong_api', 'wrong_api_slash', 'nonproxy_api', 'malformed_proxy', 'wrong_host'])
def test_conflicting_serve_configuration_is_never_advertised(tailnet, case):
    config = tailnet['serve']
    handlers = config['Web'][NAME + ':443']['Handlers']
    if case == 'missing_https':
        config.pop('TCP')
    elif case == 'http':
        config['TCP']['443']['HTTPS'] = False
    elif case == 'tcp_forward':
        config['TCP']['443']['TCPForward'] = '127.0.0.1:9999'
    elif case == 'public_funnel':
        config['AllowFunnel'] = {NAME + ':443': True}
    elif case == 'wrong_root':
        handlers['/']['Proxy'] = 'http://127.0.0.1:8787'
    elif case in ('wrong_api', 'wrong_api_slash'):
        handlers['/api' + ('/' if case.endswith('slash') else '')] = {'Proxy': 'http://127.0.0.1:9999'}
    elif case == 'nonproxy_api':
        handlers['/api'] = {'Text': 'Different service'}
    elif case == 'malformed_proxy':
        handlers['/']['Proxy'] = None
    elif case == 'wrong_host':
        config['Web']['other-laptop.tail123.ts.net:443'] = config['Web'].pop(NAME + ':443')
    before = deepcopy(config)
    result = network.secure_network()
    assert result['public_url'] == '' and not result['serve_configured']
    assert config == before


@pytest.mark.parametrize('url', [
    'https://old-machine.tail123.ts.net', 'https://OLD-MACHINE.TAIL123.TS.NET./',
    'http://localhost:5173', 'https://@robot.example', 'https://user:password@robot.example',
    'https://robot.example/dashboard', 'https://robot.example/?query=1', 'https://robot.example/#fragment',
    'https://robot.example:wrong', 'https://robot.example:70000', 'https://robot.example:0',
    'https://[invalid', 'https://robot example', 'https://robot^example',
    ' https://robot.example', 'https://robot.example\n', 'https://robot.example\\path',
])
def test_invalid_or_saved_tailnet_fallback_is_rejected(monkeypatch, url):
    monkeypatch.setattr(network, 'tailscale_binary', lambda: None)
    monkeypatch.setenv('TBOT_PUBLIC_URL', url)
    assert network.secure_network()['public_url'] == ''


@pytest.mark.parametrize('url,canonical', [
    ('https://robot.example', 'https://robot.example'),
    ('https://ROBOT.EXAMPLE:443/', 'https://robot.example'),
    ('https://localhost:8443/', 'https://localhost:8443'),
    ('https://[::1]:8443', 'https://[::1]:8443'),
])
def test_explicit_non_tailnet_https_origin_can_be_used_without_tailscale(monkeypatch, url, canonical):
    monkeypatch.setattr(network, 'tailscale_binary', lambda: None)
    monkeypatch.setenv('TBOT_PUBLIC_URL', url)
    result = network.secure_network()
    assert result['public_url'] == canonical and result['explicit_https']


def test_verified_live_tailnet_takes_precedence_over_fallback(tailnet, monkeypatch):
    monkeypatch.setenv('TBOT_PUBLIC_URL', 'https://custom.example')
    assert network.secure_network()['public_url'] == 'https://' + NAME


def test_configured_cli_name_is_resolved_from_current_path(monkeypatch):
    monkeypatch.setenv('TBOT_TAILSCALE_BIN', 'my-tailscale')
    monkeypatch.setattr(network.shutil, 'which', lambda name: '/installed/current/tailscale' if name == 'my-tailscale' else None)
    assert network.tailscale_binary() == '/installed/current/tailscale'
