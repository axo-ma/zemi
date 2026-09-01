"""Object model for the ZEMI Arsenal tree."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Generic, Iterator, TypeVar, overload

from .libs import Libs


__all__ = ["Assistant", "Endpoint", "Llama", "Model", "NamedObjects"]


_T = TypeVar("_T")


class _ConfigObject:
    """Domain object exposing values from its source configuration."""

    config: dict[str, Any]

    def __getattr__(self, name: str) -> Any:
        try:
            return self.config[name]
        except KeyError:
            raise AttributeError(name) from None


class NamedObjects(Generic[_T]):
    """Ordered object collection accessible by index and name."""

    def __init__(
        self,
        items: list[_T],
        *,
        on_access: Callable[[_T], None] | None = None,
    ) -> None:
        self._items = tuple(items)
        self._by_name = {item.name: item for item in items}
        self._on_access = on_access

    def _access(self, item: _T) -> _T:
        if self._on_access is not None:
            self._on_access(item)
        return item

    def _iter_raw(self) -> Iterator[_T]:
        """Iterate without access side effects for internal Arsenal code."""
        return iter(self._items)

    @overload
    def __getitem__(self, key: int) -> _T: ...

    @overload
    def __getitem__(self, key: str) -> _T: ...

    def __getitem__(self, key: int | str) -> _T:
        item = self._items[key] if isinstance(key, int) else self._by_name[key]
        return self._access(item)

    def __getattr__(self, name: str) -> _T:
        try:
            item = self._by_name[name]
        except KeyError:
            raise AttributeError(name) from None
        return self._access(item)

    def __iter__(self) -> Iterator[_T]:
        for item in self._items:
            yield self._access(item)

    def __len__(self) -> int:
        return len(self._items)

    def keys(self):
        return self._by_name.keys()

    def values(self):
        return (self._access(item) for item in self._by_name.values())

    def items(self):
        return (
            (name, self._access(item))
            for name, item in self._by_name.items()
        )


@dataclass(frozen=True)
class Assistant(_ConfigObject):
    """Model assistant and its source TOML configuration."""

    config: dict[str, Any]
    clients: Libs

    @property
    def name(self) -> str:
        return self.config["name"]


@dataclass(frozen=True)
class Model(_ConfigObject):
    """Llama server model, its assistants, and its TOML configuration."""

    config: dict[str, Any]
    _base_url: str = field(repr=False)
    _connection: dict[str, Any] = field(default_factory=dict, repr=False)
    _on_access: Callable[[Model], None] | None = field(
        default=None,
        repr=False,
        compare=False,
    )
    assistants: NamedObjects[Assistant] = field(init=False)

    def __post_init__(self) -> None:
        configs = self.config.get("assistants", [])
        object.__setattr__(
            self,
            "assistants",
            NamedObjects(
                [
                    Assistant(
                        config,
                        Libs(
                            self._base_url,
                            model=self.config.get("model", self.config.get("alias")),
                            context_window=self.config.get(
                                "context_window", self.config.get("ctx_size")
                            ),
                            api_key=self._connection.get("api_key", "llama.cpp"),
                            timeout=self._connection.get("request_timeout", 60.0),
                            protocol=self._connection.get("protocol", "openai"),
                            provider=self._connection.get("provider", "custom"),
                            headers=self._connection.get("headers"),
                        ),
                    )
                    for config in configs
                ],
                on_access=(
                    None
                    if self._on_access is None
                    else lambda _assistant: self._on_access(self)
                ),
            ),
        )

    @property
    def name(self) -> str:
        return self.config["name"]


@dataclass(frozen=True)
class Llama(_ConfigObject):
    """Llama server, its models, and its source TOML configuration."""

    config: dict[str, Any]
    _on_model_access: Callable[[Llama, Model], None] | None = field(
        default=None,
        repr=False,
        compare=False,
    )
    models: NamedObjects[Model] = field(init=False)

    def __post_init__(self) -> None:
        base_url = f"http://{self.config['host']}:{self.config['port']}"
        object.__setattr__(
            self,
            "models",
            NamedObjects(
                [
                    Model(
                        config,
                        base_url,
                        {},
                        None
                        if self._on_model_access is None
                        else lambda model: self._on_model_access(self, model),
                    )
                    for config in self.config["models"]
                ],
                on_access=self._activate_model,
            ),
        )

    def _activate_model(self, model: Model) -> None:
        if self._on_model_access is not None:
            self._on_model_access(self, model)

    @property
    def name(self) -> str:
        return self.config["name"]


@dataclass(frozen=True)
class Endpoint(_ConfigObject):
    """Managed or external model endpoint."""

    config: dict[str, Any] = field(repr=False)
    _on_model_access: Callable[[Endpoint, Model], None] | None = field(
        default=None, repr=False, compare=False
    )
    models: NamedObjects[Model] = field(init=False)

    def __post_init__(self) -> None:
        connection = {
            key: self.config[key]
            for key in ("api_key", "request_timeout", "protocol", "provider", "headers")
            if key in self.config
        }
        object.__setattr__(self, "models", NamedObjects([
            Model(model, self.config["base_url"], connection,
                  None if self._on_model_access is None else
                  lambda item: self._on_model_access(self, item))
            for model in self.config["models"]
        ], on_access=self._activate_model))

    def _activate_model(self, model: Model) -> None:
        if self._on_model_access is not None:
            self._on_model_access(self, model)

    @property
    def name(self) -> str:
        return self.config["name"]
