"""
Every log source the tool knows about. To add one (e.g. the Portrait diags
export), write a LogSource subclass and add one register() line below;
nothing in the service, manifest or API changes.
"""
from app.sources.base import LogSource

_SOURCES: dict[str, LogSource] = {}


def register(source: LogSource) -> None:
    _SOURCES[source.id] = source


def get_source(source_id: str) -> LogSource | None:
    return _SOURCES.get(source_id)


def all_sources() -> list[LogSource]:
    return list(_SOURCES.values())


def _register_defaults() -> None:
    from app.sources.axone_export import AxoneLogExportSource
    from app.sources.box import BoxSource
    from app.sources.local_import import LocalImportSource

    register(LocalImportSource())
    register(AxoneLogExportSource())
    register(BoxSource())


_register_defaults()
