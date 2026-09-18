import math
import time

from backend.controller import Controller
from backend.domain import Command, Mission, Point
from backend.simulator import VirtualAdapter
from backend.storage import Store


def wait_for(predicate, controller, timeout=12):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        controller.heartbeat('operator')
        controller.tick()
        if predicate():
            return
        time.sleep(.02)
    raise AssertionError('virtual mission timed out')


def test_virtual_lab_drives_scans_and_runs_a_route(tmp_path):
    robot = VirtualAdapter()
    store = Store(tmp_path)
    controller = Controller(robot, store)
    controller.map_id = 'virtual-workshop-alpha'
    def send(command):
        proof = controller.issue_permit('operator');proof.pop('expires_in_ms')
        seq = controller.command_sequences.get('operator', 0) + 1
        controller.execute(command.model_copy(update={**proof, 'seq':seq}), 'operator')
    send(Command(id='claim', action='claim', source='mission'))
    initial = robot.snapshot()
    assert initial['navigation_ready'] and len(initial['scan']['points']) > 20
    send(Command(id='home', action='home_set', source='mission'))
    target = Point(x=-1.65, y=1.25, name='Checkpoint 1')
    mission = Mission(name='Smoke route', map_id=controller.map_id, points=[target], return_home=True)
    send(Command(id='run', action='mission', source='mission', mission=mission))
    wait_for(lambda: controller.state == 'idle', controller)
    final = robot.snapshot()
    assert math.hypot(final['pose']['x'] - initial['pose']['x'], final['pose']['y'] - initial['pose']['y']) < .12
    assert final['distance_total'] > .8
    assert store.list('runs')[0]['status'] == 'Mission complete'


def test_virtual_obstacle_rejects_navigation():
    robot = VirtualAdapter()
    robot.navigate({'x': -1.75, 'y': -1.4, 'yaw': 0})
    assert robot.snapshot()['nav'] == 'failed'
