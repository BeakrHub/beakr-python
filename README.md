# beakr-sdk

The Python SDK for the Beakr public API: a typed client for projects, files,
Knowledge, runs, interviews, conversations and integrations, sync and async.

```bash
pip install beakr-sdk            # not on PyPI yet; see "Installing before the first release"
```

The distribution is `beakr-sdk`; the import name is `beakr_sdk`. The
[Beakr CLI](https://github.com/BeakrHub/beakr-cli) is built on this package, and
the [TypeScript SDK](https://github.com/BeakrHub/beakr-typescript) (`@beakr/sdk`)
mirrors it.

## Quickstart


```python
from beakr_sdk import Beakr

beakr = Beakr()  # api_key=..., else BEAKR_API_KEY, else your `beakr auth login`

project = beakr.projects.create("Research")
batch = beakr.files.upload_many("docs/", project_id=project["id"])  # one batch; folders kept
beakr.jobs.wait(batch)                                               # until it is indexed

hits = beakr.knowledge.sections("launch date", scope="Research")
card = beakr.knowledge.cat("Launch Plan", scope="Research")
found = beakr.knowledge.documents_search("who covers losses from defects", project_id="Research")
beakr.files.download_reference(found["results"][0]["source_ref"], "downloads/")
facts = beakr.knowledge.rows(table="facts", entity="Stripe", current_only=True)
beakr.knowledge.add_finding(
    "Stripe", date="2026-10-01", kind="decision", text="Stay on Stripe through 2027",
    evidence="{{doc:abc123}}", rationale="Board decision", scope="Research",
)

ctx = beakr.context(project_id=project["id"], format="markdown")
# Private to you. To add to an imported session, send only the new messages with
# base_index = how many were sent before; without base_index the batch is read
# as the whole session from position 0. Message text is kept only by opt-in:
# compound=True imports and queues compounding in one request, holding the text
# until that capture reads it (hold_text=True holds it for a later
# beakr.compound without queuing; store_full_text=True keeps it).
imported = beakr.conversations.import_(
    client="generic", session_id="run-42", base_index=0,
    messages=[{"role": "user", "content": "..."}, {"role": "assistant", "content": "..."}],
    compound=True,
)

for tool in beakr.integrations.tools(provider="slack"):    # your connected integrations
    print(tool["name"])
found = beakr.integrations.call_tool("slack_search", {"query": "launch date"})

link = beakr.interviews.create_link("Assay handover", questions=["Controls?"])
print(link["url"])                                           # shown once; send it
```

### Tell Knowledge what a project is about

A project's Knowledge profile steers how Beakr compiles its files: what the
project is for, the people, organizations and topics that matter, and the kinds
of things to build pages for. Set it before or after uploading; Knowledge reads
it when it next compiles files. Only the project's owners may set it.

```python
beakr.projects.set_knowledge_profile(
    project_id,
    description="Batch records, deviations and CAPAs for the Basel site",
    goals=["Track deviations to closure"],
    entity_types=["batch", "deviation", "capa", "supplier"],
    core_people=[{"name": "Priya Natarajan", "role": "Batch owner"}],
    extraction_guidance="Record each deviation's root cause and its CAPA.",
)
beakr.projects.update_knowledge_profile(project_id, focus_areas=["pH excursions"])
print(beakr.projects.knowledge_profile(project_id)["entity_types"])
```

`knowledge_profile` raises `NotFoundError` (`code == "knowledge_profile_not_found"`)
while the project has none.

### Skills

A skill is a SKILL.md folder your agent loads on demand. `skills.list` covers
your own skills, your organization's, the built-in ones and public skills from
anywhere; each carries `visibility`, `owner` (`me`, `org`, `system`, `anyone`),
`origin` (`created`, `mined`, `imported`, `system`) and `installed`, and each
is also a filter.

```python
mine = beakr.skills.list(owner="me")
builtin = beakr.skills.list(owner="system")
to_install = beakr.skills.list(visibility="public", installed=False)

skill = beakr.skills.create_from_folder("skills/plate-reader")  # or a .zip
beakr.skills.download(skill["id"], "exported/")                 # exported/plate-reader/
beakr.skills.install(to_install[0]["id"])

for proposal in beakr.skills.proposals.list():                  # drafted from your work
    beakr.skills.proposals.accept(proposal["id"])
```

`skills.get` takes an id or a slug; a slug resolves to your own skill first,
then a built-in, then one you installed, then your organization's.
`upload_bundle(skill_id, files, expected_sha256=...)` raises `ConflictError`
(`code == "skill_conflict"`) when the skill changed since you read it.

### Know when Knowledge is ready

Uploaded files reach a project's Knowledge in the background after indexing.
Wait for it before asking questions that depend on those files, and retry what
failed:

```python
from beakr_sdk.resources.knowledge_readiness import KnowledgeFailed

# The batch's own files: indexing, then Knowledge.
result = beakr.files.upload_many("docs/", project_id=project_id, wait="knowledge", timeout=1800)
print(result["batch"]["knowledge"])     # {"queued": 0, "running": 0, "created": 41, "failed": 1, ...}

# The whole project.
status = beakr.projects.wait_until_ready(project_id, timeout=900)   # KnowledgeTimeout if not
for failed in status["failed_files"]:   # newest first; knowledge_failures() lists them all
    print(failed["path"], failed["error_code"], failed["reason"])
if status["failed"]:
    beakr.projects.retry_failed(project_id)            # or retry_failed(project_id, [file_id])

try:
    beakr.projects.wait_until_ready(project_id, raise_on_failure=True)
except KnowledgeFailed as failed:
    print(failed.status["failed_files"])
```

`ready` is true when nothing is queued or running. Failed files never end a wait
early; `error_code` is one of `processing_failed`, `timed_out`,
`concurrent_update` or `compile_failed`, and a retry is safe to repeat.

Embedded mode (a backend holding a service account's key):

```python
person = beakr.external_users.create("customer-42", name="Ada Lovelace")
beakr.labels.add_member(label_id, person["user_id"])      # labels give it projects
minted = beakr.end_user_tokens.mint(external_user_id=person["user_id"], scopes=["kb:read"])
user_beakr = Beakr(api_key=minted["token"])                 # 15 minutes, no refresh
```

`AsyncBeakr` has the same methods (`await beakr.projects.list()`).

- **Auth chain**: explicit `api_key`, `BEAKR_API_KEY`, a local dev identity
  (`BEAKR_DEV_IDENTITY` + `BEAKR_DEV_EMAIL`, or the one `beakr auth dev` saved,
  which is only used for the API it was saved for), the CLI's device-flow login
  (refreshed and re-saved automatically), the CLI's saved key, then the service
  account file.
- **Base URL**: explicit `base_url`, `BEAKR_BASE_URL` / `BEAKR_API_URL`, the CLI
  config, then `https://api.thebeakr.com`.
- **Headers**: `X-Beakr-Client` (`sdk-python/<version>` unless `client_name=`),
  `X-Beakr-Agent` when `agent=` is set, `X-Beakr-Run` when `run=` is set,
  `X-Beakr-Session` when `session=` (default: `BEAKR_SESSION`) names the agent
  session behind the writes, and an `Idempotency-Key` on create calls.
- **Retries**: 429, 500, 502, 503, 504 and connection errors, with exponential
  backoff and jitter, honoring `Retry-After` (`max_retries=` to change). The
  header may be seconds or an HTTP-date (IMF-fixdate, or the obsolete RFC 850
  and asctime forms); a date already past retries at once, one that is not a
  valid date is ignored in favor of the normal backoff, and a wait beyond 60
  seconds is not taken: the error (`RateLimitError.retry_after` in seconds) is
  raised instead. A 429 is always retried. A 5xx or connection error is retried only when resending
  cannot repeat an effect: reads, PUT/DELETE, routes that deduplicate
  themselves (starting an assistant run), and every public create, which the
  engine replays on its `Idempotency-Key` -- the SDK resends the same key and
  body, so the retry returns the first result instead of a second resource
  (the list is `beakr_sdk._replay.REPLAYING_CREATES`). A keyed create that
  meets an earlier attempt still holding the key (409
  `idempotency_in_progress`) waits and retries. Other POSTs (decisions such as
  accept, token exchanges, reads with a body) are never resent after an
  ambiguous failure. 501, 505 and other 4xx are never retried.
- **Errors**: `BeakrError` with `status`, `code`, `message` and `request_id`;
  subclasses `AuthenticationError`, `PermissionDeniedError` (`required_scope`),
  `NotFoundError` (and its subclass `NotProvidedError`, below), `ConflictError`,
  `ValidationError`, `RateLimitError`, `ServerError`, `APIConnectionError`, and
  `KnowledgeCommandError` for a knowledge command the door refused.
- **Secrets stay out of output**: `repr()` and `str()` of the client, its
  `credential`, a stored sign-in, a service-account file, a request and an
  error show an API key, access or refresh token or `Authorization` header
  masked to its kind and last four characters (`bk_live_...a1b2`), so printing
  one in a test or a log does not leak it. Responses are returned as they come:
  the `key` of `keys.create` or `org.service_accounts.mint_key` and the `token`
  of `end_user_tokens.mint` are plain dict values, shown once.
- **Resources** (the documented core only): `keys`, `me`,
  `meta`, `jobs`, `organizations`, `members`, `org` (`service_accounts`),
  `external_users`, `labels`, `end_user_tokens`, `projects`, `files` (with
  `uploads`; `upload_many` for many files), `bulk_uploads`, `folders`, `knowledge` (with `pages` and `proposals`), `runs`,
  `interviews`, `threads`, `conversations`, `engine` (`context`, `compound`)
  and `integrations` (with `tools` and `call_tool`).
- **Documented routes only**: this package sends nothing outside the documented
  contract (`tests/test_shipped_surface.py` builds the wheel and fails on any
  other route in it). The API still serves some routes it no longer documents,
  with no stability promise; the Beakr CLI keeps its own private client for the
  few it calls.

### What this installation serves: `beakr.meta.capabilities()`

A self-hosted Beakr installation serves only part of the documented contract
(no organization administration or members, external users, end-user tokens,
service accounts, integrations or skills) and answers a bare 404 for the rest. Each server
lists what it serves in its own `GET /v1/public/openapi.json`;
`capabilities()` reads it once per base URL (cached for five minutes) so a
client can hide what is not there instead of failing on it:

```python
caps = beakr.meta.capabilities()
if not caps.serves("GET", "/v1/integrations"):
    print("This Beakr installation does not provide integrations")
```

`serves` takes a concrete path or a contract template. A server that publishes
no document is assumed to serve everything, and a route outside the documented
contract is always assumed served: the document cannot speak for it.

Calling one anyway raises `NotProvidedError`, a `NotFoundError` (so an existing
`except NotFoundError` still catches it), when the 404 is the router's bare
`{"detail": "Not Found"}`. Its message names the area, in the CLI's words
("This Beakr installation does not provide skills."), or the operation
(`GET /v1/me/organization`) when the path belongs to no known area; `area`,
`method` and `path` hold the same. A missing record answers 404 with an error
envelope and stays a plain `NotFoundError`.

```python
from beakr_sdk.errors import NotProvidedError

try:
    beakr.skills.list()
except NotProvidedError as exc:
    print(exc.message)  # This Beakr installation does not provide skills.
```

### The generated, typed layer: `beakr.api`

Every documented operation also has one generated, typed method, grouped by
area like the TypeScript SDK's `beakr.api`:

```python
from beakr_sdk import AsyncBeakr, Beakr
from beakr_sdk._generated import models

beakr = Beakr()
project: models.ProjectOut = beakr.api.projects.create_project(body={"name": "Assays"})
for item in beakr.api.projects.list_projects_all(member_only=True):  # follows the cursor
    print(item["name"])

other = await AsyncBeakr().api.projects.get_project(project["id"])  # the async twin
```

- `beakr_sdk._generated.models` has a `TypedDict` per contract schema (an enum or
  union schema is a type alias). Results stay plain dicts at runtime, so
  `project["id"]` keeps working; the types are for your editor and type checker
  (the package ships `py.typed`).
- `beakr_sdk._generated.operations` has, per operation, a request builder
  (`build_<area>_<method>(...)`) and the typed sync and async methods. Path
  values are one percent-encoded segment each; a list operation also has
  `<method>_all()`, which follows `next_cursor`. `OPERATIONS` maps every
  `<area>.<method>` to its HTTP method and path.
- The resources above are helpers on this layer: every request they send is
  built by a generated builder (a test fails if a resource builds one by hand;
  the only exceptions are the PUTs and GETs to presigned storage URLs). The 70 methods that send exactly one
  operation are typed by it, for each client: `beakr.projects.get(...)` is a
  `ProjectOut`, `await async_beakr.projects.get(...)` too, and `projects.list()`
  is a `list[ProjectOut]`. Multi-step helpers (uploads, flows) return `Any`.
- Path values are refused with `ValueError` when empty, `.` or `..` (they would
  reach another route), everywhere (`beakr_sdk._paths.segment`).
- Regenerate after a contract change with
  `uv run python scripts/generate_python_sdk.py`. It reads the engine's
  `docs/api/public-openapi.json` (or a path / `BEAKR_OPENAPI`), the only
  contract this package is built from; the hand-written resources build their
  requests with this layer too. `tests/test_generated_layer.py` fails when the committed layer is stale,
  checks that every operation, sync and async, sends its own request, and runs
  mypy over the layer (good and deliberately wrong usage).

## Installing before the first release

Until `beakr-sdk` is on PyPI, install it from this repository, pinned to a tag
or commit:

```bash
pip install "beakr-sdk @ git+https://github.com/BeakrHub/beakr-python@<tag-or-sha>"
```

## Development

```bash
uv sync
uv run pytest                   # the suite
uv run ruff check .
uv run mypy src/beakr_sdk
```

The generated layer is derived from the engine's documented contract. One
command regenerates it after the contract changes:

```bash
uv run python scripts/generate_python_sdk.py          # writes src/beakr_sdk/_generated
uv run python scripts/generate_python_sdk.py --check  # exit 1 when stale (CI)
```

It reads `docs/api/public-openapi.json` from `$BEAKR_ENGINE_PATH`, else a sibling
`beakr-engine` checkout, else the committed
snapshot (`BEAKR_OPENAPI` names the file directly). The rendering is
`beakr_sdk._codegen` (private): the Beakr CLI renders its private client with the
same generator.

CI reads no other repository. The files this package is generated from and
compared with are committed under `snapshots/`: `snapshots/beakr-engine` (the
documented contract, the API changelog, the compatibility rules, and the
engine's replayed-create list as `derived/replaying-creates.json`) and
`snapshots/beakr-typescript` (its `src/replay.ts`, whose replayed-create list
must match this package's). `snapshots/manifest.json` pins the commit each came
from (with `uncommitted_changes` when taken from a working tree) and each
file's SHA-256. After an engine or TypeScript SDK change, refresh them from
checkouts beside this one (or named with `--engine` / `--typescript`), then
regenerate:

```bash
uv run python scripts/refresh_snapshots.py refresh   # rewrite snapshots/ and the manifest
uv run python scripts/refresh_snapshots.py diff      # exit 1 when stale against the checkouts
uv run python scripts/refresh_snapshots.py verify    # offline: the files are the manifest's (CI)
uv run python scripts/generate_python_sdk.py snapshots/beakr-engine/docs/api/public-openapi.json
```

Tests that compare with another repository read `BEAKR_ENGINE_PATH` /
`BEAKR_TYPESCRIPT_PATH`, else sibling checkouts, else the snapshots, and fail
instead of skipping with `BEAKR_REQUIRE_ENGINE=1` (both CIs set it). The
engine's CI checks this repository and beakr-typescript out on the branch named
like its own and runs this suite against the real repositories, so drift shows
up there before a refresh here.

### Private modules

Modules whose names start with an underscore (`beakr_sdk._codegen`,
`beakr_sdk._calls`, ...) are private: no stability promise. The Beakr CLI builds
its private client on them, pinned to an exact commit of this package.

## Release

Nothing publishes automatically yet. The release workflow
(`.github/workflows/publish.yml`) is prepared but runs only when a GitHub
release is published and the repository variable `PYPI_PUBLISH_ENABLED` is
`true`; until both, nothing is uploaded.

To enable it once:

1. On PyPI, add a pending Trusted Publisher (Account settings, Publishing):
   project `beakr-sdk`, owner `BeakrHub`, repository `beakr-python`, workflow
   `publish.yml`, environment `pypi`.
2. In this repository, create the `pypi` environment (Settings, Environments),
   ideally with required reviewers, and set the repository variable
   `PYPI_PUBLISH_ENABLED` to `true`.

### Versions and release notes come from the API contract

`scripts/prepare_release.py` (identical in every Beakr client repository;
`release/config.json` names this package and its version files) compares the
engine's contract at the last release (`release/released.json`) with the current
one, using the engine's compatibility rules (vendored in
`scripts/contract_compat.py`). A breaking change needs a major bump (below 1.0,
a minor one), an added or changed operation a minor bump, a wording change a
patch. The API changelog entries added since that release become the release's
section in `CHANGELOG.md`; the `### Client changes` subsection is hand-written
and kept across reapplies.

```bash
uv run python scripts/prepare_release.py plan    # what changed, which bump it needs
uv run python scripts/prepare_release.py apply   # bump versions, write CHANGELOG.md
uv run python scripts/prepare_release.py check   # what CI and publishing run
```

### Releasing

```bash
uv run python scripts/prepare_release.py apply --date 2026-10-12
uv run python scripts/prepare_release.py check
uv build && uvx twine check dist/*

# Commit, tag v<version>, push, and publish a GitHub release for the tag.
# publish.yml runs ci.yml first, refuses a tag that does not match
# pyproject.toml, then uploads dist/* to PyPI.

# Afterwards, record what was released (the engine commit it was built from),
# then refresh the snapshot so it carries that commit's contract and changelog
# (snapshots/beakr-engine/release-base/<sha>), which the offline release check reads:
uv run python scripts/prepare_release.py record --engine-commit <engine sha>
uv run python scripts/refresh_snapshots.py refresh --engine <engine checkout>
```

After the first PyPI release, switch `beakr-cli` from its git pin to the PyPI
version (its README, "The Python SDK dependency").
