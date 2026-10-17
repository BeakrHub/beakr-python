"""Typed shapes for a ``knowledge.query`` spec (the ``query`` command).

A spec is plain JSON: these are ``TypedDict`` s, so a dict literal type-checks
against them and is sent as is. ``from``, ``as``, ``with``, ``and``, ``or`` and
``not`` are Python keywords, so the shapes that carry them are declared with
the functional ``TypedDict`` form.

The engine validates every spec and refuses one it cannot run with
``invalid_arguments``, naming the key, what is allowed and, for an unknown
field, the closest field names.
"""

from __future__ import annotations

from typing import Literal, TypedDict

QueryTable = Literal["facts", "connections", "findings"]
TextOp = Literal["eq", "contains", "in"]
ValueOp = Literal["eq", "ne", "contains", "in", "gt", "gte", "lt", "lte", "between"]
LinkOp = Literal["eq", "contains"]
FieldMatch = Literal["exact", "similar", "meaning"]
GroupKey = Literal["field", "value", "entity_type", "entity"]
AggregateFn = Literal["count", "count_distinct", "min", "max", "sum", "avg"]

#: ``from`` as a table with an alias, for joins and aliased conditions.
FromTable = TypedDict("FromTable", {"table": QueryTable, "as": str})


class _Aliased(TypedDict, total=False):
    #: Which joined table a condition reads (the ``as`` of ``from`` or a join).
    alias: str


class EntityCondition(_Aliased, total=False):
    entity: str | list[str]
    op: TextOp


class EntityTypeCondition(_Aliased, total=False):
    entity_type: str | list[str]
    op: TextOp


class FieldCondition(_Aliased, total=False):
    field: str
    match: FieldMatch


class ValueCondition(_Aliased, total=False):
    #: ``gt`` .. ``lte`` take a number or a YYYY-MM-DD date; ``between`` takes [low, high].
    value: str | float | list[str] | list[float]
    op: ValueOp


WhenRange = TypedDict("WhenRange", {"from": str, "to": str}, total=False)


class WhenCondition(_Aliased, total=False):
    when: WhenRange


class AsOfCondition(TypedDict):
    """Facts only, at the top level of ``where``: each card's value on that day."""

    as_of: str


class ToCondition(_Aliased, total=False):
    to: str
    op: LinkOp


class ConnectedToCondition(_Aliased, total=False):
    connected_to: str
    op: LinkOp


AndCondition = TypedDict("AndCondition", {"and": "list[Condition]"})
OrCondition = TypedDict("OrCondition", {"or": "list[Condition]"})
NotCondition = TypedDict("NotCondition", {"not": "Condition"})

Condition = (
    EntityCondition
    | EntityTypeCondition
    | FieldCondition
    | ValueCondition
    | WhenCondition
    | AsOfCondition
    | ToCondition
    | ConnectedToCondition
    | AndCondition
    | OrCondition
    | NotCondition
)

#: ``on`` ``target_card`` needs a connections table on its ``with`` side.
Join = TypedDict(
    "Join",
    {
        "table": QueryTable,
        "as": str,
        "on": Literal["same_card", "target_card"],
        "with": str,
    },
)


class Aggregate(TypedDict):
    fn: AggregateFn
    #: A group key for ``count_distinct``; ``value_number``, ``value_amount`` or
    #: ``value_date`` for the others.
    of: str


class OrderBy(TypedDict, total=False):
    #: An aggregate's label, a group key, ``value_number``, ``value_amount``,
    #: ``value_date`` or ``when``.
    by: str
    dir: Literal["asc", "desc"]


class RankFieldsBy(TypedDict):
    rank_fields_by: str


class FieldNear(_Aliased, total=False):
    field_near: str
    #: 0.3 .. 0.99.
    threshold: float


QuerySpec = TypedDict(
    "QuerySpec",
    {
        "from": QueryTable | FromTable,
        "where": Condition,
        "join": list[Join],
        "group_by": list[str],
        "aggregate": list[Aggregate],
        "order_by": list[OrderBy],
        #: 1 .. 200; the engine's default is 50.
        "limit": int,
        #: The opaque cursor a previous reply handed out.
        "cursor": str,
        "semantic": RankFieldsBy | FieldNear,
    },
    total=False,
)

__all__ = [
    "Aggregate",
    "AggregateFn",
    "AndCondition",
    "AsOfCondition",
    "Condition",
    "ConnectedToCondition",
    "EntityCondition",
    "EntityTypeCondition",
    "FieldCondition",
    "FieldMatch",
    "FieldNear",
    "FromTable",
    "GroupKey",
    "Join",
    "LinkOp",
    "NotCondition",
    "OrCondition",
    "OrderBy",
    "QuerySpec",
    "QueryTable",
    "RankFieldsBy",
    "TextOp",
    "ToCondition",
    "ValueCondition",
    "ValueOp",
    "WhenCondition",
    "WhenRange",
]
