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
from beakr_sdk.capabilities import Capabilities
from beakr_sdk.client import Beakr
from beakr_sdk.errors import (
    CONFIRM_LOCK_HEADER,
    APIConnectionError,
    AuthenticationError,
    BeakrError,
    BeakrUploadInterrupted,
    BoundaryBelowMinimumError,
    ConflictError,
    CrossProjectOwnerRequiredError,
    CrossProjectWriteRefusedError,
    KnowledgeCommandError,
    NotFoundError,
    NotProvidedError,
    PermissionDeniedError,
    PersonReviewRequiredError,
    ProjectContentLockedError,
    ProjectProfileRequiredError,
    RateLimitError,
    ServerError,
    SessionAlreadyStartedError,
    SessionEndedError,
    SessionLockConfirmError,
    SessionLockedToProjectError,
    SessionNeedsSelectionError,
    SessionProjectRequiredError,
    SessionReadOnlyError,
    SessionRequiredError,
    SessionRouteUnavailableError,
    SessionSelectionInvalidError,
    SessionWidenRefusedError,
    SessionWriteScopeError,
    SettingRetiredError,
    UnexpectedResponseError,
    UnsafePathError,
    ValidationError,
)
from beakr_sdk.query_spec import QuerySpec
from beakr_sdk.resources.knowledge import cross_project_hold

__all__ = [
    "CONFIRM_LOCK_HEADER",
    "APIConnectionError",
    "AsyncBeakr",
    "AuthenticationError",
    "Beakr",
    "BeakrError",
    "BeakrUploadInterrupted",
    "BoundaryBelowMinimumError",
    "Capabilities",
    "ConflictError",
    "CrossProjectOwnerRequiredError",
    "CrossProjectWriteRefusedError",
    "KnowledgeCommandError",
    "NotFoundError",
    "NotProvidedError",
    "PermissionDeniedError",
    "PersonReviewRequiredError",
    "ProjectContentLockedError",
    "ProjectProfileRequiredError",
    "QuerySpec",
    "RateLimitError",
    "RunFailedError",
    "RunNeedsInputError",
    "ServerError",
    "SessionAlreadyStartedError",
    "SessionEndedError",
    "SessionLockConfirmError",
    "SessionLockedToProjectError",
    "SessionNeedsSelectionError",
    "SessionProjectRequiredError",
    "SessionReadOnlyError",
    "SessionRequiredError",
    "SessionRouteUnavailableError",
    "SessionSelectionInvalidError",
    "SessionWidenRefusedError",
    "SessionWriteScopeError",
    "SettingRetiredError",
    "UnexpectedResponseError",
    "UnsafePathError",
    "ValidationError",
    "__version__",
    "cross_project_hold",
]
