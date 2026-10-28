"""Model backends.

`Backend` is a real interface only because the model-swap ask is explicit in
the stakeholder wishlist and it costs nothing; the sole implementation is
`SamGeoBackend`. Do not add stub Sam2/GroundingDino
classes until one is actually being built.
"""

from .base import Backend, InstanceResult

__all__ = ["Backend", "InstanceResult", "make_backend"]


def make_backend(settings) -> Backend:
    name = settings.model["backend"]
    if name == "samgeo":
        from .samgeo_backend import SamGeoBackend

        return SamGeoBackend(settings)
    raise ValueError(f"unknown model backend: {name!r}")
