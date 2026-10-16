"""Print OpenYAM joint positions once per second (sim sanity check).

Usage: python tools/joint_probe.py [seconds]
"""

import sys
import time

from dimos.core.transport_factory import make_transport
from dimos.msgs.sensor_msgs.JointState import JointState

latest: list[JointState] = []
transport = make_transport("/coordinator_joint_state", JointState)
transport.subscribe(lambda msg: latest.append(msg))

end = time.time() + float(sys.argv[1] if len(sys.argv) > 1 else 30)
while time.time() < end:
    time.sleep(1.0)
    if latest:
        msg = latest[-1]
        latest.clear()
        pos = " ".join(f"{p:+.3f}" for p in msg.position)
        print(f"{time.strftime('%H:%M:%S')}  {pos}", flush=True)
