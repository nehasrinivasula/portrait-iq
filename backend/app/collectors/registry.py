"""
Plugin registry (SCRUM-38). The orchestrator looks plugins up here by name
and never imports a plugin module directly.

To add a plugin, write a Collector subclass in its own module in
app/collectors/, then add one register() line in _register_defaults() below.
Nothing in the orchestrator, the API or the other plugins changes.

Same style as the log-source registry (sources/registry.py): one explicit
list, so every available plugin is visible in one place.
"""
import inspect
from typing import Any

from app.collectors.base import (
    NAME_PATTERN,
    Collector,
    PluginConfigError,
    PluginError,
    ResourceLimits,
)



class UnknownPluginError(PluginError):
    """A job asked for a plugin name that is not registered."""


class PluginRegistry:
    def __init__(self) -> None:
        self._plugins: dict[str, type[Collector]] = {}

    # -- registration ------------------------------------------------------

    def register(self, cls: type[Collector]) -> type[Collector]:
        """Add a plugin class. Returns the class, so it works as a decorator."""
        if not (inspect.isclass(cls) and issubclass(cls, Collector)):
            raise TypeError(f"{cls!r} is not a Collector subclass")
        if inspect.isabstract(cls):
            missing = ", ".join(sorted(cls.__abstractmethods__))
            raise TypeError(f"{cls.__name__} does not implement: {missing}")
        if not NAME_PATTERN.match(cls.name or ""):
            raise ValueError(
                f"{cls.__name__}.name must be 2-32 lowercase letters, digits or "
                f"underscores, starting with a letter; got {cls.name!r}"
            )
        existing = self._plugins.get(cls.name)
        if existing is not None and existing is not cls:
            raise ValueError(
                f"plugin name {cls.name!r} is already used by "
                f"{existing.__module__}.{existing.__qualname__}"
            )
        self._plugins[cls.name] = cls
        return cls

    # -- lookup ------------------------------------------------------------

    def names(self) -> list[str]:
        return sorted(self._plugins)

    def all(self) -> list[type[Collector]]:
        return [self._plugins[n] for n in self.names()]

    def get(self, name: str) -> type[Collector]:
        try:
            return self._plugins[name]
        except KeyError:
            raise UnknownPluginError(
                f"unknown plugin {name!r}; available: {', '.join(self.names()) or 'none'}"
            ) from None

    # -- use ---------------------------------------------------------------

    def create(
        self, name: str, limits: ResourceLimits, options: dict[str, Any] | None = None
    ) -> Collector:
        """Instantiate and configure a plugin. configure() has no side
        effects, so this is also how a job's selection is validated."""
        plugin = self.get(name)(limits)
        plugin.configure(dict(options or {}))
        return plugin

    def create_all(
        self,
        names: list[str],
        limits: ResourceLimits,
        options: dict[str, dict[str, Any]] | None = None,
    ) -> list[Collector]:
        """Create and configure every selected plugin, or raise before any is
        returned. Options for a plugin that was not selected are an error,
        since they almost always mean a typo in the plugin name."""
        options = options or {}
        if len(set(names)) != len(names):
            raise PluginConfigError(f"a plugin was selected more than once: {names}")
        stray = sorted(set(options) - set(names))
        if stray:
            raise PluginConfigError(f"options given for plugins not selected: {stray}")
        return [self.create(n, limits, options.get(n)) for n in names]


registry = PluginRegistry()


def register(cls: type[Collector]) -> type[Collector]:
    return registry.register(cls)


def get_registry() -> PluginRegistry:
    """The shared registry, with the built-in plugins registered."""
    return registry


def _register_defaults() -> None:
    from app.collectors.hub_import import HubImportCollector
    from app.collectors.netshoot import NetshootCollector
    from app.collectors.rti import RtiCollector
    from app.collectors.tshark import TsharkCollector

    register(HubImportCollector)
    register(NetshootCollector)
    register(RtiCollector)
    register(TsharkCollector)


_register_defaults()
