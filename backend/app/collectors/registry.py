"""
Plugin registry (SCRUM-38). The orchestrator looks plugins up here by name
and never imports a plugin module directly.

To add a plugin, write a Collector subclass in its own module in
app/collectors/, then add one "module:Class" line to PLUGINS below.
Nothing in the orchestrator, the API or the other plugins changes.

One explicit list, like the log-source registry (sources/registry.py), so
every available plugin is visible in one place. Each entry is loaded on its
own: a plugin that fails to import or is invalid is logged and listed in
load_errors, and the app still starts with the others.
"""
import importlib
import inspect
import logging
from typing import Any

from app.collectors.base import (
    NAME_PATTERN,
    Collector,
    PluginConfigError,
    PluginError,
    ResourceLimits,
)

log = logging.getLogger(__name__)

# Built-in plugins, as "module:Class". Add new plugins here.
PLUGINS = [
    "app.collectors.hub_import:HubImportCollector",
    "app.collectors.netshoot:NetshootCollector",
    "app.collectors.rti:RtiCollector",
    "app.collectors.tshark:TsharkCollector",
]


class UnknownPluginError(PluginError):
    """A job asked for a plugin name that is not registered."""


class PluginRegistry:
    def __init__(self) -> None:
        self._plugins: dict[str, type[Collector]] = {}
        self.load_errors: dict[str, str] = {}   # "module:Class" -> why it was skipped

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

    def load(self, paths: list[str]) -> None:
        """Import and register each "module:Class" entry independently.
        A failing entry is recorded in load_errors and skipped; it never
        stops the other plugins (or the app) from loading."""
        for path in paths:
            try:
                module_name, sep, class_name = path.partition(":")
                if not (sep and module_name and class_name):
                    raise ValueError("expected 'module:Class'")
                cls = getattr(importlib.import_module(module_name), class_name)
                self.register(cls)
            except Exception as exc:  # noqa: BLE001  one bad plugin must not stop the rest
                self.load_errors[path] = f"{type(exc).__name__}: {exc}"
                log.error("plugin %s skipped: %s", path, exc)

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
registry.load(PLUGINS)


def register(cls: type[Collector]) -> type[Collector]:
    return registry.register(cls)


def get_registry() -> PluginRegistry:
    """The shared registry, with the built-in plugins loaded."""
    return registry
