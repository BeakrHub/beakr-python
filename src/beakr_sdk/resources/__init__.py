"""Resource groups of the documented core, attached to both clients by :func:`attach_resources`.

Only resources over the documented public contract live here, and this package
has no others: routes the API serves without documenting them are the Beakr
CLI's private client's business.
"""

from __future__ import annotations

from typing import Any

from beakr_sdk.resources.bulk_uploads import BulkUploads
from beakr_sdk.resources.conversations import Conversations
from beakr_sdk.resources.end_user_tokens import EndUserTokens
from beakr_sdk.resources.engine import Engine
from beakr_sdk.resources.external_users import ExternalUsers
from beakr_sdk.resources.files import Files
from beakr_sdk.resources.folders import Folders
from beakr_sdk.resources.integrations import Integrations
from beakr_sdk.resources.interviews import Interviews
from beakr_sdk.resources.jobs import Jobs
from beakr_sdk.resources.keys import Keys
from beakr_sdk.resources.knowledge import Knowledge
from beakr_sdk.resources.labels import Labels
from beakr_sdk.resources.me import Me, Meta
from beakr_sdk.resources.members import Members
from beakr_sdk.resources.org_admin import OrgAdmin
from beakr_sdk.resources.organizations import Organizations
from beakr_sdk.resources.projects import Projects
from beakr_sdk.resources.runs import Runs, Threads

__all__ = ["attach_resources"]


def attach_resources(client: Any) -> None:
    # Account and access
    client.keys = Keys(client)
    client.me = Me(client)
    client.meta = Meta(client)
    client.jobs = Jobs(client)
    # Organization and people
    client.organizations = Organizations(client)
    client.members = Members(client)
    client.org = OrgAdmin(client)
    # Embedded mode: your product's users, the groups that give them projects,
    # and the short-lived tokens you mint for them.
    client.external_users = ExternalUsers(client)
    client.labels = Labels(client)
    client.end_user_tokens = EndUserTokens(client)
    # Projects and files
    client.projects = Projects(client)
    client.files = Files(client)
    client.bulk_uploads = BulkUploads(client)
    client.folders = Folders(client)
    # Knowledge and Ask
    client.knowledge = Knowledge(client)
    client.runs = Runs(client)
    client.interviews = Interviews(client)
    # Conversations
    client.threads = Threads(client)
    client.conversations = Conversations(client)
    client.engine = Engine(client)
    # Integrations
    client.integrations = Integrations(client)
    # The two engine calls agents use most, at top level.
    client.context = client.engine.context
    client.compound = client.engine.compound
    # Wait for any long operation: ``beakr.wait(run)`` (W1.2.7).
    client.wait = client.jobs.wait
