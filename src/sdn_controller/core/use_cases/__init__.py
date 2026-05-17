"""Use cases: thin coordinators that orchestrate entities and ports.

Each use case is a single-purpose class. Its constructor takes ports
(repositories, clock, id factory, ...) and exposes a single async ``execute``
method that accepts a command dataclass and returns a result.

Why classes rather than free functions? Constructor injection makes
dependencies explicit and lets the container wire everything once at startup.
"""
