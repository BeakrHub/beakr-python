"""Labels: named groups of projects with their own members (``/v1/labels``)."""

from __future__ import annotations

from typing import Any

from beakr_sdk._calls import clean_body
from beakr_sdk._generated import operations as ops
from beakr_sdk._resource import M
from beakr_sdk._typing import returns, returns_all
from beakr_sdk.resources._base import Resource


class Labels(Resource[M]):
    @returns_all(ops.build_labels_list_labels)
    def list(self) -> Any:
        return self._list_all(ops.build_labels_list_labels())

    def create(
        self,
        name: str,
        *,
        description: str | None = None,
        color: str | None = None,
        settings: dict[str, Any] | None = None,
    ) -> Any:
        body = clean_body(
            {"name": name, "description": description, "color": color, "settings": settings}
        )
        return self._run(ops.build_labels_create_label(body=body))

    @returns(ops.build_labels_get_label)
    def get(self, label_id: str) -> Any:
        return self._run(ops.build_labels_get_label(label_id))

    @returns(ops.build_labels_update_label)
    def update(self, label_id: str, **fields: Any) -> Any:
        return self._run(ops.build_labels_update_label(label_id, body=clean_body(fields)))

    @returns(ops.build_labels_delete_label)
    def delete(self, label_id: str) -> Any:
        return self._run(ops.build_labels_delete_label(label_id))

    @returns_all(ops.build_labels_list_label_members)
    def members(self, label_id: str) -> Any:
        return self._list_all(ops.build_labels_list_label_members(label_id))

    @returns(ops.build_labels_add_label_member)
    def add_member(self, label_id: str, user_id: str, *, role: str = "member") -> Any:
        return self._run(
            ops.build_labels_add_label_member(label_id, body={"user_id": user_id, "role": role})
        )

    @returns(ops.build_labels_remove_label_member)
    def remove_member(self, label_id: str, user_id: str) -> Any:
        return self._run(ops.build_labels_remove_label_member(label_id, user_id))

    @returns_all(ops.build_labels_list_label_projects)
    def projects(self, label_id: str) -> Any:
        return self._list_all(ops.build_labels_list_label_projects(label_id))

    def attach_project(self, label_id: str, project_id: str, *, role: str | None = None) -> Any:
        body = clean_body({"project_id": project_id, "role": role})
        return self._run(ops.build_labels_attach_label_project(label_id, body=body))

    @returns(ops.build_labels_update_label_project)
    def set_project_role(self, label_id: str, project_id: str, role: str) -> Any:
        return self._run(
            ops.build_labels_update_label_project(label_id, project_id, body={"role": role})
        )

    @returns(ops.build_labels_detach_label_project)
    def detach_project(self, label_id: str, project_id: str) -> Any:
        return self._run(ops.build_labels_detach_label_project(label_id, project_id))
