"""Route modules for the NiCo Flask app.

Each module exposes register(app, ctx); ctx carries the request helpers
defined in server.create_app() (CSRF check, setup guard, sudo nonces, ...).
"""

from . import (brix, config, files, flake, git, hm, import_routes,
               packages, rebuild, symlink)

_MODULES = (config, import_routes, brix, flake, rebuild,
            packages, git, files, hm, symlink)


def register_all(app, ctx):
    for module in _MODULES:
        module.register(app, ctx)
