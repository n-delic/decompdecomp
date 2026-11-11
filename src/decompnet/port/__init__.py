"""Porting a finished game to the PC (docs/PORTING.md): what every console shares. A console's own part is its
`PortKit` (consoles/__init__.py: `Console.port_kit`; N64: consoles/n64/port.py).

- readiness.py: the port readiness report (phase 0): what still stands between a project and a native build. It
  reads the project and never changes it.
"""
