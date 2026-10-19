"""
Plugin registry (SCRUM-38). The orchestrator looks plugins up here by name
and never imports a plugin module directly.

To add a plugin, create one new module in app/collectors/:

    from app.collectors.base import Collector, CollectorResult
    from app.collectors.registry import register

    @register
    class PingPlugin(Collector):
        name = "ping"
        display_name = "Ping"
        description = "Checks that the target answers ICMP echo."

        def run(self, target_ip): ...
        def cleanup(self): ...

discover() imports every module in the package, and @register adds the
class. No existing plugin, the orchestrator or the API changes.

A module that fails to import is logged and listed in load_errors; the
other plugins still load, so one broken plugin cannot take the tool down.
"""
import importlib
import inspect
import logging
import pkgutil
from typing import Any

from app.collectors.base import (
    NAME_PATTERN,
    Collector,
    PluginConfigError,
    PluginError,
    ResourceLimits,
)

log = logging.getLogger(__name__)

DEFAULT_PACKAGE = "app.collectors"
_NOT_PLUGINS = {"base", "registry"}


class UnknownPluginError(PluginError):
    """A job asked for a plugin name that is not registered."""


class PluginRegistry:
    def __init__(self) -> None:
        self._plugins: dict[str, type[Collector]] = {}
        self.load_errors: dict[str, str] = {}   # module name -> error message
        self._discovered: set[str] = set()

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

    def discover(self, package: str = DEFAULT_PACKAGE) -> None:
        """Import every module in `package` so their @register calls run.
        Safe to call more than once."""
        if package in self._discovered:
            return
        pkg = importlib.import_module(package)
        for info in pkgutil.iter_modules(pkg.__path__):
            if info.name in _NOT_PLUGINS or info.name.startswith("_"):
                continue
            module_name = f"{package}.{info.name}"
            try:
                importlib.import_module(module_name)
            except Exception as exc:  # noqa: BLE001  one bad plugin must not stop the rest
                self.load_errors[module_name] = f"{type(exc).__name__}: {exc}"
                log.error("plugin module %s failed to load: %s", module_name, exc)
        self._discovered.add(package)

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
register = registry.register


def get_registry() -> PluginRegistry:
    """The shared registry with the built-in package already discovered."""
    registry.discover()
    return registry
