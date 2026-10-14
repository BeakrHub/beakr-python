"""Typed results for hand-written resource methods, sync and async alike.

One resource class serves both clients: on :class:`~beakr_sdk.Beakr` a method
returns its value, on :class:`~beakr_sdk.AsyncBeakr` an awaitable of it. A
resource is generic in that *mode* (``Projects[Sync]`` on the sync client,
``Projects[Async]`` on the async one), and a method that sends exactly one
public operation declares the operation's generated builder::

    @returns(ops.build_projects_get_project)
    def get(self, project_id: str) -> Any: ...

so a type checker sees ``beakr.projects.get(...)`` as ``ProjectOut`` and
``await async_beakr.projects.get(...)`` as ``ProjectOut`` too, with the method's
own parameters. At runtime the decorators return the method unchanged (they
only record which builder it uses, for ``tests/test_generated_layer.py``);
everything here is for type checkers.

- :func:`returns`: the operation's response.
- :func:`returns_all`: every item of a paginated list (``list[Item]``).
- :func:`iterates`: an iterator over a list's items, following its cursor.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from typing import TYPE_CHECKING, Any, Concatenate, Generic, TypeVar, cast, overload

from typing_extensions import ParamSpec, Self

from beakr_sdk._calls import Call, ListCall

if TYPE_CHECKING:
    from beakr_sdk._resource import Resource

__all__ = ["Async", "Sync", "iterates", "returns", "returns_all"]


class Sync:
    """The mode of a resource on the synchronous client: methods return values."""


class Async:
    """The mode of a resource on the async client: methods return awaitables."""


P = ParamSpec("P")
T = TypeVar("T")
I = TypeVar("I")  # noqa: E741 - the conventional name for an item type

#: The attribute a decorated method records its builder's name under.
BUILDER_ATTRIBUTE = "__beakr_builder__"


class Returns(Generic[P, T]):
    """A method typed by its operation: ``T`` on the sync client, ``Awaitable[T]`` on async."""

    @overload
    def __get__(self, obj: None, owner: Any) -> Self: ...
    @overload
    def __get__(self, obj: Resource[Sync], owner: Any) -> Callable[P, T]: ...
    @overload
    def __get__(self, obj: Resource[Async], owner: Any) -> Callable[P, Awaitable[T]]: ...
    def __get__(self, obj: Any, owner: Any) -> Any:  # pragma: no cover - typing only
        raise NotImplementedError


class Iterates(Generic[P, I]):
    """A method yielding a list's items: ``Iterator`` sync, ``AsyncIterator`` async."""

    @overload
    def __get__(self, obj: None, owner: Any) -> Self: ...
    @overload
    def __get__(self, obj: Resource[Sync], owner: Any) -> Callable[P, Iterator[I]]: ...
    @overload
    def __get__(self, obj: Resource[Async], owner: Any) -> Callable[P, AsyncIterator[I]]: ...
    def __get__(self, obj: Any, owner: Any) -> Any:  # pragma: no cover - typing only
        raise NotImplementedError


def _record(builder: Callable[..., Any], method: Any) -> Any:
    setattr(method, BUILDER_ATTRIBUTE, builder.__name__)
    return method


def returns(
    builder: Callable[..., Call[T]],
) -> Callable[[Callable[Concatenate[Any, P], Any]], Returns[P, T]]:
    """Type a method that returns ``self._run(builder(...))``."""

    def decorate(method: Callable[Concatenate[Any, P], Any]) -> Returns[P, T]:
        return cast("Returns[P, T]", _record(builder, method))

    return decorate


def returns_all(
    builder: Callable[..., ListCall[I]],
) -> Callable[[Callable[Concatenate[Any, P], Any]], Returns[P, list[I]]]:
    """Type a method that returns ``self._list_all(builder(...))``: every item."""

    def decorate(method: Callable[Concatenate[Any, P], Any]) -> Returns[P, list[I]]:
        return cast("Returns[P, list[I]]", _record(builder, method))

    return decorate


def iterates(
    builder: Callable[..., ListCall[I]],
) -> Callable[[Callable[Concatenate[Any, P], Any]], Iterates[P, I]]:
    """Type a method that returns ``self._iter(builder(...))``: the items, page by page."""

    def decorate(method: Callable[Concatenate[Any, P], Any]) -> Iterates[P, I]:
        return cast("Iterates[P, I]", _record(builder, method))

    return decorate
