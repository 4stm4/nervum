"""In-memory OVSDB adapter.

Used in unit tests and for the bootable MVP on hosts that don't have a real
OVS installation. Same protocol as ``SubprocessOvsdb`` so use-case behaviour
is identical regardless of backend.
"""

from netos_agent.adapters.ovsdb_fake.adapter import FakeOvsdb

__all__ = ["FakeOvsdb"]
