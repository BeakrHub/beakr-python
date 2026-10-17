"""Beakr Python SDK.

Sync::

    from beakr_sdk import Beakr

    beakr = Beakr()  # BEAKR_API_KEY, else the `beakr auth login` credentials
    project = beakr.projects.create("Research")
    beakr.files.upload("paper.pdf", project_id=project["id"])
    print(beakr.knowledge.sections("main result", scope="Research"))

Async: ``AsyncBeakr`` takes the same arguments and has the same methods.
Errors are typed (:mod:`beakr_sdk.errors`).

The clients cover the documented public contract: account and keys,
organization members and service accounts, external users, labels and
end-user tokens, projects, files, Knowledge, runs, interviews, conversations
and integrations. ``beakr.api`` has one generated method per documented
operation. Modules whose names start with an underscore are private.
"""

from beakr_sdk._version import __version__
from beakr_sdk.answers import RunFailedError, RunNeedsInputError
from beakr_sdk.async_client import AsyncBeakr
from beakr_sdk.client import Beakr
from beakr_sdk.errors import (
    APIConnectionError,
    AuthenticationError,
    BeakrError,
    BeakrUploadInterrupted,
    ConflictError,
    KnowledgeCommandError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitError,
    ServerError,
    ValidationError,
)
from beakr_sdk.query_spec import QuerySpec

__all__ = [
    "APIConnectionError",
    "AsyncBeakr",
    "AuthenticationError",
    "Beakr",
    "BeakrError",
    "BeakrUploadInterrupted",
    "ConflictError",
    "KnowledgeCommandError",
    "NotFoundError",
    "PermissionDeniedError",
    "QuerySpec",
    "RateLimitError",
    "RunFailedError",
    "RunNeedsInputError",
    "ServerError",
    "ValidationError",
    "__version__",
]
