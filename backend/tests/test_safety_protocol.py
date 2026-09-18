"""Replay real immutable command envelopes against the authoritative controller.

There is no automatic restamping in these tests: delayed commands retain the
epoch, generation, permit and sequence they had when first created.
"""
import time

import pytest
from pydantic import ValidationError

from backend.controller import Controller, DirectUsbAdapter
from backend.domain import Command
from backend.storage import Store


@pytest.fixture
def safety(tmp_path):
    ready = [True]
    motor = []
    controller = Controller(DirectUsbAdapter(lambda: ready[0]), Store(tmp_path))
    controller.motion_output = lambda linear, angular: motor.append((linear, angular))
    return controller, motor, ready


def envelope(controller, action, session='operator', seq=None, **fields):
    if action in ('drive','timed'):fields={'linear':0.,'angular':0.,**fields}
    proof = controller.issue_permit(session)
    proof.pop('expires_in_ms')
    return Command(id=f'{action}-{time.monotonic_ns()}', action=action,
                   seq=controller.command_sequences.get(session, 0) + 1 if seq is None else seq,
                   **proof, **fields)


def claim(controller, source='keyboard', session='operator'):
    controller.execute(envelope(controller, 'claim', session, source=source), session)


def test_boot_is_disarmed_even_with_fresh_movement(safety):
    controller, motor, _ = safety
    with pytest.raises(ValueError, match='STOP is latched'):
        controller.execute(envelope(controller, 'drive', linear=.06), 'operator')
    assert not motor and not controller.armed


@pytest.mark.parametrize('rearm', [False, True])
def test_forward_created_before_stop_cannot_move_after_stop(safety, rearm):
    controller, motor, _ = safety
    claim(controller)
    delayed = envelope(controller, 'drive', linear=.06)
    controller.execute(Command(id='stop', action='stop'), 'another-browser')
    assert not controller.armed and controller.owner is None
    if rearm: claim(controller)
    count = len(motor)
    with pytest.raises(ValueError, match='Stale control generation'):
        controller.execute(delayed, 'operator')
    assert len(motor) == count


def test_stop_latch_requires_explicit_claim_not_just_new_proof(safety):
    controller, motor, _ = safety
    claim(controller)
    controller.stop()
    with pytest.raises(ValueError, match='Take control first'):
        controller.execute(envelope(controller, 'drive', linear=.06), 'operator')
    assert motor[-1] == (0., 0.)


@pytest.mark.parametrize('replayed_seq', [10, 9])
def test_duplicate_and_out_of_order_movement_never_reaches_motor(safety, replayed_seq):
    controller, motor, _ = safety
    claim(controller)
    first = envelope(controller, 'drive', seq=10, linear=.06)
    delayed = envelope(controller, 'drive', seq=replayed_seq, linear=-.06)
    controller.execute(first, 'operator')
    count = len(motor)
    with pytest.raises(ValueError, match='sequence'):
        controller.execute(delayed, 'operator')
    assert len(motor) == count and motor[-1] == (.06, 0.)


def test_server_expiry_rejects_delayed_command_without_client_clock(safety):
    controller, motor, _ = safety
    claim(controller)
    delayed = envelope(controller, 'drive', linear=.06)
    controller.command_permits[delayed.permit]['until'] = time.monotonic() - .001
    count = len(motor)
    with pytest.raises(ValueError, match='freshness permit expired'):
        controller.execute(delayed, 'operator')
    assert len(motor) == count


def test_freshness_permit_is_bound_to_authenticated_session(safety):
    controller, motor, _ = safety
    claim(controller)
    stolen = envelope(controller, 'drive', linear=.06)
    with pytest.raises(ValueError, match='permit expired or invalid'):
        controller.execute(stolen, 'different-session')
    assert motor[-1] == (0., 0.)


def test_old_boot_commands_are_invalid_after_backend_restart(safety, tmp_path):
    controller, _, _ = safety
    delayed = envelope(controller, 'claim')
    restarted = Controller(DirectUsbAdapter(lambda: True), Store(tmp_path / 'restarted'))
    assert controller.epoch != restarted.epoch
    with pytest.raises(ValueError, match='Stale control generation'):
        restarted.execute(delayed, 'operator')
    assert not restarted.armed


def test_second_browser_cannot_take_over_without_explicit_stop(safety):
    controller, motor, _ = safety
    claim(controller)
    with pytest.raises(ValueError, match='Another operator'):
        claim(controller, session='second-browser')
    assert controller.owner == 'operator' and controller.armed
    controller.execute(Command(id='transfer-stop', action='stop'), 'second-browser')
    claim(controller, session='second-browser')
    assert controller.owner == 'second-browser' and motor[-1] == (0., 0.)


def test_old_release_cannot_disarm_new_human_claim(safety):
    controller, _, _ = safety
    claim(controller)
    delayed = envelope(controller, 'release')
    controller.stop()
    claim(controller)
    with pytest.raises(ValueError, match='Stale control generation'):
        controller.execute(delayed, 'operator')
    assert controller.armed


@pytest.mark.parametrize('active_motion', [False, True])
def test_connection_timeout_latches_even_when_armed_and_idle(safety, active_motion):
    controller, motor, _ = safety
    claim(controller)
    if active_motion: controller.execute(envelope(controller, 'drive', linear=.06), 'operator')
    generation = controller.generation
    controller.last_heartbeat = time.monotonic() - 2
    controller.tick()
    assert not controller.armed and controller.owner is None
    assert controller.generation > generation and motor[-1] == (0., 0.)


def test_manual_frame_timeout_latches(safety):
    controller, motor, _ = safety
    claim(controller)
    controller.execute(envelope(controller, 'drive', linear=.06), 'operator')
    controller.last_drive = time.monotonic() - .3
    controller.tick()
    assert not controller.armed and motor[-1] == (0., 0.)
    with pytest.raises(ValueError, match='Take control first'):
        controller.execute(envelope(controller, 'drive', linear=.06), 'operator')


def test_neutral_manual_frame_does_not_latch_or_change_generation(safety):
    controller, motor, _ = safety
    claim(controller)
    controller.execute(envelope(controller, 'drive', linear=.06), 'operator')
    generation = controller.generation
    controller.execute(envelope(controller, 'drive'), 'operator')
    controller.last_drive = time.monotonic() - .3
    controller.tick()
    assert controller.armed and controller.generation == generation and controller.state == 'idle'
    controller.execute(envelope(controller, 'drive', linear=.06), 'operator')
    assert motor[-1] == (.06, 0.)


@pytest.mark.parametrize('action', ['distance','angle','mission','home_set','home_go','explore','map_save','map_load','resume','retry','skip'])
def test_direct_usb_cannot_use_fabricated_odometry_or_autonomy(safety, action):
    controller, motor, _ = safety
    claim(controller)
    count = len(motor)
    with pytest.raises(ValueError, match='no measured robot odometry'):
        controller.execute(envelope(controller, action, value=20), 'operator')
    assert len(motor) == count
    state = controller.snapshot()
    assert state['mode'] == 'direct-usb' and state['motor_connected']
    assert all(state[key] is None for key in ('pose','odom','map','scan','scan_age','odom_age'))


def test_direct_usb_timed_voice_uses_shared_output_and_latches_on_unplug(safety):
    controller, motor, ready = safety
    claim(controller, source='voice')
    controller.execute(envelope(controller, 'timed', source='voice', linear=.06, value=1), 'operator')
    controller.tick()
    assert motor[-1] == (.06, 0.)
    ready[0] = False
    controller.tick()
    assert motor[-1] == (0., 0.) and not controller.armed and controller.timed_until == 0


@pytest.mark.parametrize('field,value', [('seq', True), ('seq', -1), ('seq', 1.2), ('generation', True), ('generation', -1)])
def test_invalid_sequence_and_generation_types_rejected(field, value):
    with pytest.raises(ValidationError):
        Command(id='invalid', action='drive', linear=0., angular=0., **{field:value})


@pytest.mark.parametrize('action', ['drive','timed'])
@pytest.mark.parametrize('vector', [{}, {'linear':.06}, {'angular':.2}])
def test_movement_requires_both_velocity_fields(action, vector):
    with pytest.raises(ValidationError, match='explicit linear and angular'):
        Command(id='missing-axis', action=action, **vector)


@pytest.mark.parametrize('action', ['stop','cancel'])
def test_stop_cancel_need_no_motion_vector_or_proof(safety, action):
    controller, motor, _ = safety
    claim(controller)
    controller.execute(Command(id='stop-no-fields', action=action), 'observer')
    assert motor[-1] == (0.,0.) and not controller.armed and controller.owner is None
