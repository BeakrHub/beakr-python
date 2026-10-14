"""Integrations: connect, list, status, disconnect, and query what is connected.

Connecting finishes in a browser, where the provider's OAuth consent happens:
``connect`` returns ``connect_url`` to open. Everything else works from a key
or token (``integrations:read`` / ``integrations:admin``).

The caller's connected integrations can be queried the way Beakr's own agent
does: :meth:`Integrations.tools` lists the read-only tools for them and
:meth:`Integrations.call_tool` runs one.

Example::

    link = beakr.integrations.connect("slack")
    print(link["connect_url"])
    for item in beakr.integrations.list()["data"]:
        print(item["provider"], [p["project_id"] for p in item["projects"]])
    for tool in beakr.integrations.tools(provider="slack"):
        print(tool["name"], tool["description"])
    found = beakr.integrations.call_tool("slack_search", {"query": "assay handover"})
    print(found["content"])
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from beakr_sdk._generated import operations as ops
from beakr_sdk._resource import M
from beakr_sdk._typing import iterates, returns, returns_all
from beakr_sdk.resources._base import Resource


class Integrations(Resource[M]):
    @returns(ops.build_integrations_list_integrations)
    def list(self) -> Any:
        """``{data: [...]}``: connections you can see, each with the projects it feeds."""
        return self._run(ops.build_integrations_list_integrations())

    @returns(ops.build_integrations_connect_integration)
    def connect(self, provider: str, *, scope: Literal["user", "org"] = "user") -> Any:
        """``{provider, scope, connect_url, expires_at}``: open ``connect_url`` in a browser."""
        return self._run(
            ops.build_integrations_connect_integration(provider, body={"scope": scope})
        )

    @returns(ops.build_integrations_integration_status)
    def status(self, connector_id: str) -> Any:
        """Health plus each project source's latest sync."""
        return self._run(ops.build_integrations_integration_status(connector_id))

    @returns(ops.build_integrations_disconnect_integration)
    def disconnect(self, connector_id: str) -> Any:
        """Disconnect, including the connection at the provider."""
        return self._run(ops.build_integrations_disconnect_integration(connector_id))

    @returns_all(ops.build_integrations_list_integration_tools)
    def tools(self, *, provider: str | None = None, connector_id: str | None = None) -> Any:
        """Every read-only tool for the integrations the caller has connected and may use.

        Each tool has ``name``, ``provider``, ``description``, ``input_schema`` and
        the ``connections`` it can run against. Narrow with ``provider`` or one
        ``connector_id``.
        """
        return self._list_all(
            ops.build_integrations_list_integration_tools(
                provider=provider, connector_id=connector_id
            )
        )

    @iterates(ops.build_integrations_list_integration_tools)
    def iter_tools(self, *, provider: str | None = None, connector_id: str | None = None) -> Any:
        """:meth:`tools`, one page at a time, as an iterator."""
        return self._iter(
            ops.build_integrations_list_integration_tools(
                provider=provider, connector_id=connector_id
            )
        )

    @returns(ops.build_integrations_call_integration_tool)
    def call_tool(
        self,
        name: str,
        arguments: Mapping[str, Any] | None = None,
        *,
        connector_id: str | None = None,
    ) -> Any:
        """Run one read-only tool (a name from :meth:`tools`) with its ``arguments``.

        Answers ``{tool, provider, connector_ids, content, is_error, sources}``.
        When the caller has the provider connected more than once, ``connector_id``
        picks the connection.
        """
        body: dict[str, Any] = {"arguments": dict(arguments or {})}
        if connector_id is not None:
            body["connector_id"] = connector_id
        return self._run(ops.build_integrations_call_integration_tool(name, body=body))
