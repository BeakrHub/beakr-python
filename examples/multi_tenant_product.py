"""Beakr inside a product with ten customers: the worked example, end to end.

The companion of "Worked example: ten customers" in the Beakr developer docs
(the ``example-multi-tenant`` guide in Beakr's developer docs), and of
``multi-tenant-product.ts`` beside this file.

The product has one shared "General" project (product documentation every
customer may read) and one project per customer. Each customer's users are
external users in a label (a group) that is attached to their customer's
project and to General, so a user reads both and nothing else. The backend
holds the service-account key; each user session gets a short-lived token.

Two ways for an agent to use Beakr with that token are shown:

* ``answer_with_own_agent``: your own Claude agent with Beakr as tools
  (search, read, research), a minimal tool loop on the Anthropic SDK;
* ``answer_with_beakr_agent``: Beakr's own agent, asked with ``runs.answer``
  (the answer text, citing by readable reference, and the cited sources).

Run it with ``BEAKR_SERVICE_KEY`` (the service-account key; scopes below) and
``ANTHROPIC_API_KEY`` set, from a directory holding ``docs/general/`` and
``docs/customers/<customer id>/`` folders::

    python examples/multi_tenant_product.py setup      # projects, labels, uploads
    python examples/multi_tenant_product.py ask cust_001 user-8812 "How do I rotate our SSO cert?"
    python examples/multi_tenant_product.py check      # A cannot see B; both see General
    python examples/multi_tenant_product.py offboard cust_010

Ids Beakr returns are kept in ``tenants.json`` here; a product keeps them in
its own database.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import anthropic
from anthropic.types.beta import BetaMessageParam, BetaToolParam, BetaToolResultBlockParam

from beakr_sdk import Beakr
from beakr_sdk.errors import BeakrError, NotFoundError
from beakr_sdk.resources.knowledge import result_text
from beakr_sdk.sources import source_where

#: Your own customer ids. Beakr never needs to understand them.
CUSTOMERS = [f"cust_{n:03d}" for n in range(1, 11)]

#: What each user's token may do: read their projects and Knowledge, ask
#: research questions, run Beakr's agent and read its threads.
USER_SCOPES = ["projects:read", "kb:read", "agent:run", "conversations:read"]

#: The service-account key must hold every scope it uses or hands out.
SERVICE_SCOPES = [
    "projects:admin",  # projects, labels, project members
    "members:admin",  # external users
    "uploads:write",  # customers' documents
    "interviews:write",  # interview links
    *USER_SCOPES,  # a minted token can never carry more than its minter
]

STORE = Path("tenants.json")
MODEL = "claude-opus-5"


# -- Your product's records ---------------------------------------------------


def load_store() -> dict[str, Any]:
    if STORE.exists():
        return dict(json.loads(STORE.read_text()))
    return {"general_project_id": None, "customers": {}, "users": {}}


def save_store(store: dict[str, Any]) -> None:
    STORE.write_text(json.dumps(store, indent=2))


def backend_client() -> Beakr:
    """The backend's client, with the service-account key. Never sent to a browser."""
    return Beakr(api_key=os.environ["BEAKR_SERVICE_KEY"], client_name="acme-assist-backend")


# -- 1. One-time setup ------------------------------------------------------------


def setup(backend: Beakr, store: dict[str, Any]) -> None:
    """General, a project and a label per customer, and their documents.

    Ids are saved as soon as each object exists, so a second run picks up
    where the first stopped instead of creating duplicates.
    """
    steps = len(CUSTOMERS) + 1
    if store["general_project_id"] is None:
        progress(1, steps, "General: creating the shared project")
        general = backend.projects.create(
            "General", description="Product documentation every customer reads"
        )
        store["general_project_id"] = general["id"]
        save_store(store)
        upload_and_wait(backend, Path("docs/general"), general["id"])
    else:
        progress(1, steps, "General: already set up")
    general_id = store["general_project_id"]

    for index, customer_id in enumerate(CUSTOMERS, 2):
        if customer_id in store["customers"]:
            progress(index, steps, f"{customer_id}: already set up")
            continue
        progress(index, steps, f"{customer_id}: creating project and label")
        project = backend.projects.create(
            f"Customer {customer_id}", tags=[f"customer:{customer_id}"]
        )
        label = backend.labels.create(f"customer:{customer_id}")
        # A label gives its members read access to every project attached to it.
        backend.labels.attach_project(label["id"], project["id"])
        backend.labels.attach_project(label["id"], general_id)
        store["customers"][customer_id] = {"project_id": project["id"], "label_id": label["id"]}
        save_store(store)
        upload_and_wait(backend, Path("docs/customers") / customer_id, project["id"])
    print(f"done: {len(store['customers'])} customers set up, ids in {STORE}")


def progress(step: int, total: int, message: str) -> None:
    """One progress line per setup step, so a long run never looks hung."""
    print(f"[{step}/{total}] {message}", flush=True)


def upload_and_wait(backend: Beakr, folder: Path, project_id: str) -> None:
    """Upload a folder as one batch (subfolders become folders) and wait until
    its files are indexed: one job for the whole batch.

    Indexing makes a file searchable; compiling it into Knowledge pages
    continues in the background for a few minutes after that.
    """
    if not folder.is_dir():
        print(f"      no {folder}/ here; nothing to upload", flush=True)
        return
    print(f"      uploading {folder}/", flush=True)
    batch = backend.files.upload_many(folder, project_id=project_id)
    print("      waiting for indexing (a minute or so)", flush=True)
    backend.jobs.wait(batch, raise_on_failure=True)


# -- 2. A user signs in to your product ------------------------------------------


def session_token(backend: Beakr, store: dict[str, Any], customer_id: str, user_id: str) -> str:
    """Get or create the user's external user, put it in its customer's group,
    and mint a 30-minute token for this session."""
    customer = store["customers"][customer_id]
    user = backend.external_users.create(user_id, metadata={"customer_id": customer_id})
    # Every time, not only when the user was just created: adding a member is
    # idempotent, and this keeps a recreated group or a lost store consistent.
    backend.labels.add_member(customer["label_id"], user["user_id"])
    store["users"][user_id] = {"customer_id": customer_id, "beakr_user_id": user["user_id"]}
    save_store(store)
    minted = backend.end_user_tokens.mint(
        external_user_id=user["user_id"], scopes=USER_SCOPES, ttl_seconds=1800
    )
    return str(minted["token"])


# -- 3a. Your own agent, with Beakr as tools --------------------------------------

SYSTEM = (
    "You answer questions for one customer of Acme Assist. Use the tools to look "
    "things up in their knowledge base before answering, and say where an answer "
    "came from. If the knowledge base does not say, say so."
)

TOOLS: list[BetaToolParam] = [
    {
        "name": "search_knowledge",
        "description": (
            "Search the knowledge base this user can read: their company's pages and "
            "the shared product documentation. Returns matching passages with page titles."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "What to look for."}},
            "required": ["query"],
        },
    },
    {
        "name": "read_page",
        "description": "Read one knowledge-base page in full, with its sources, by its title.",
        "input_schema": {
            "type": "object",
            "properties": {"page": {"type": "string", "description": "The page title."}},
            "required": ["page"],
        },
    },
    {
        "name": "research",
        "description": (
            "A researched, cited answer to a question across the knowledge base and "
            "documents. Slower (5 to 45 seconds); use it for questions that need "
            "several sources."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"question": {"type": "string"}},
            "required": ["question"],
        },
    },
]


def run_tool(user_beakr: Beakr, name: str, args: dict[str, Any]) -> tuple[str, bool]:
    """Run one tool as the user (their token), answering ``(text, is_error)``."""
    try:
        if name == "search_knowledge":
            return result_text(user_beakr.knowledge.grep(args["query"], mode="semantic")), False
        if name == "read_page":
            return result_text(user_beakr.knowledge.cat(args["page"])), False
        if name == "research":
            result = user_beakr.knowledge.research(args["question"])
            cited = [f"- {source_where(c)}" for c in result["citations"]]
            return "\n".join([str(result["text"]), *cited]), False
        return f"Unknown tool {name}", True
    except BeakrError as error:
        # Give the refusal back to the model as text; it can try another way.
        return str(error), True


def answer_with_own_agent(user_token: str, question: str) -> str:
    user_beakr = Beakr(api_key=user_token)
    claude = anthropic.Anthropic()
    messages: list[BetaMessageParam] = [{"role": "user", "content": question}]
    while True:
        response = claude.beta.messages.create(
            model=MODEL,
            max_tokens=16000,
            system=SYSTEM,
            tools=TOOLS,
            messages=messages,
            # If Claude declines, the API retries on a fallback model in the same call.
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
        messages.append({"role": "assistant", "content": response.content})
        if response.stop_reason != "tool_use":
            return "".join(block.text for block in response.content if block.type == "text")
        results: list[BetaToolResultBlockParam] = []
        for block in response.content:
            if block.type == "tool_use":
                text, failed = run_tool(user_beakr, block.name, dict(block.input))
                results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": text,
                        "is_error": failed,
                    }
                )
        messages.append({"role": "user", "content": results})


# -- 3b. Beakr's own agent ----------------------------------------------------------


def answer_with_beakr_agent(user_token: str, question: str) -> str:
    """Ask Beakr's agent as the user: the answer, citing by readable reference
    (``(memo.pdf › p. 7)``), then where each cited source lives. With no
    ``project_id`` the run reads everything the user can: their customer's
    project and General."""
    user_beakr = Beakr(api_key=user_token)
    answer = user_beakr.runs.answer(question)
    cited = [f"- {source_where(source)}" for source in answer["sources"]]
    return "\n".join([answer["text"], *cited])


# -- 4. Isolation: A cannot see B, both see General --------------------------------


def check_isolation(backend: Beakr, store: dict[str, Any]) -> None:
    a, b = CUSTOMERS[0], CUSTOMERS[1]
    token_a = session_token(backend, store, a, f"demo-user-{a}")
    user_a = Beakr(api_key=token_a)
    visible = {project["id"] for project in user_a.projects.list()}
    assert store["general_project_id"] in visible
    assert store["customers"][a]["project_id"] in visible
    assert store["customers"][b]["project_id"] not in visible
    try:
        user_a.projects.get(store["customers"][b]["project_id"])
    except NotFoundError:
        pass  # another customer's project does not exist, as far as A can tell
    else:
        raise AssertionError("customer A's user could read customer B's project")
    print("ok: customer A's user sees General and A only")


# -- 5. Interview someone to fill a gap -------------------------------------------


def interview_for_customer(backend: Beakr, recipient: str) -> dict[str, Any]:
    """Make an interview link; deliver ``url`` yourself (Beakr sends nothing)."""
    link = backend.interviews.create_link(
        "How your team approves purchase orders",
        goal="Learn the purchase-order approval steps so the assistant can answer them",
        questions=["Who approves orders above the team limit?", "What happens on rejection?"],
        recipient_name=recipient,
    )
    return dict(link)


def file_interview(backend: Beakr, store: dict[str, Any], customer_id: str, link_id: str) -> bool:
    """Once finished, send the interview into the customer's project. True when sent."""
    result = backend.interviews.link_result(link_id)
    if result["status"] != "completed":
        return False
    backend.interviews.send_to_projects(link_id, [store["customers"][customer_id]["project_id"]])
    return True


# -- 6. Offboarding a customer ---------------------------------------------------


def offboard(backend: Beakr, store: dict[str, Any], customer_id: str) -> None:
    """Delete the customer's users (their personal data goes with them), the group,
    and archive the project. Archiving is reversible; ``projects.delete`` deletes
    the project permanently."""
    customer = store["customers"].pop(customer_id)
    for user_id, user in list(store["users"].items()):
        if user["customer_id"] == customer_id:
            backend.external_users.delete(user["beakr_user_id"])
            del store["users"][user_id]
    backend.labels.delete(customer["label_id"])
    backend.projects.archive(customer["project_id"])
    save_store(store)


def main(argv: list[str]) -> None:
    store = load_store()
    with backend_client() as backend:
        command = argv[0] if argv else "setup"
        if command == "setup":
            setup(backend, store)
        elif command == "ask":
            customer_id, user_id, question = argv[1], argv[2], argv[3]
            token = session_token(backend, store, customer_id, user_id)
            print(answer_with_own_agent(token, question))
            print("---")
            print(answer_with_beakr_agent(token, question))
        elif command == "check":
            check_isolation(backend, store)
        elif command == "offboard":
            offboard(backend, store, argv[1])
        else:
            raise SystemExit(f"unknown command {command}")


if __name__ == "__main__":
    main(sys.argv[1:])
