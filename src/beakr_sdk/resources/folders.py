"""Folders inside a project: ``/v1/folders``."""

from __future__ import annotations

from typing import Any

from beakr_sdk._calls import clean_body
from beakr_sdk._generated import operations as ops
from beakr_sdk._resource import M
from beakr_sdk._typing import returns, returns_all
from beakr_sdk.resources._base import Resource


class Folders(Resource[M]):
    @returns_all(ops.build_folders_list_folders)
    def list(
        self,
        project_id: str,
        *,
        parent_folder_id: str | None = None,
        root_only: bool | None = None,
    ) -> Any:
        return self._list_all(
            ops.build_folders_list_folders(
                project_id=project_id, parent_folder_id=parent_folder_id, root_only=root_only
            )
        )

    def create(
        self,
        name: str,
        *,
        project_id: str,
        parent_folder_id: str | None = None,
        description: str | None = None,
    ) -> Any:
        body = clean_body(
            {
                "name": name,
                "project_id": project_id,
                "parent_folder_id": parent_folder_id,
                "description": description,
            }
        )
        return self._run(ops.build_folders_create_folder(body=body))

    @returns(ops.build_folders_get_folder)
    def get(self, folder_id: str) -> Any:
        return self._run(ops.build_folders_get_folder(folder_id))

    @returns_all(ops.build_folders_list_folder_resources)
    def contents(self, folder_id: str) -> Any:
        """The files in a folder."""
        return self._list_all(ops.build_folders_list_folder_resources(folder_id))

    def update(
        self,
        folder_id: str,
        *,
        name: str | None = None,
        description: str | None = None,
        parent_folder_id: str | None = None,
    ) -> Any:
        body = clean_body(
            {"name": name, "description": description, "parent_folder_id": parent_folder_id}
        )
        return self._run(ops.build_folders_update_folder(folder_id, body=body))

    @returns(ops.build_folders_delete_folder)
    def delete(self, folder_id: str) -> Any:
        return self._run(ops.build_folders_delete_folder(folder_id))
