"""NetOS Agent — process running on every managed node.

The agent owns the local OVS configuration (read and write), exposes a small
southbound API the controller drives, and never receives raw shell commands —
only structured ``Plan`` objects. Its public seams are exactly the same kind
of port/adapter layout the controller uses, so the two services stay
substitutable for tests.
"""

__version__ = "0.1.0"
