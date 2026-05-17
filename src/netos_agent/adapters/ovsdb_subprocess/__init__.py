"""Real OVSDB adapter — talks to ``ovs-vsctl`` over subprocess.

Imported only on hosts that actually have OVS installed. The class lives in
its own subpackage so importing this module never tries to ``exec`` anything
unless you ask for it.
"""

from netos_agent.adapters.ovsdb_subprocess.adapter import SubprocessOvsdb

__all__ = ["SubprocessOvsdb"]
