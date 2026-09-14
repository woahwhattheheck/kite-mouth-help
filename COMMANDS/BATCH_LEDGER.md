# Deterministic command-batch ledger

`command_batch.py` is a read-only pre-dispatch reconciler for the public
`COMMANDS/` board. Its CLI compiles ticket and receipt bytes from one exact,
immutable local Git commit into a canonical JSON packet and a deterministic
Markdown summary.

It is deliberately **not** another command executor.

## Authority boundary

A packet never:

- executes a ticket;
- sends a message or surfaces device data;
- contacts the PC, a network service, or a provider;
- writes a host receipt;
- authenticates `claimed_from`;
- grants `ZERO_AUTHORITY`; or
- authorizes bytes fetched in a later run.

The host puller remains responsible for independently pinning, freezing, and
validating the exact bytes it consumes immediately before dispatch. A review
packet does not grant later worktree bytes any authority.

## Exact Git source binding

The CLI requires `--source-ref` to be one full lowercase 40-character SHA-1 for
a commit already present in the local repository. It rejects symbolic refs,
abbreviations, tags, uppercase spellings, and replacement-object semantics.
The mutable worktree is never the source of the selected ticket name-set or
bytes.

For the selected immediate `.txt` entries under the configured command and
receipt trees, the Git loader binds:

1. the exact commit SHA-1;
2. the exact root-tree object ID;
3. each selected Git path, regular-file mode, blob object ID, and byte count;
4. SHA-256 of every selected blob's exact bytes; and
5. a canonical SHA-256 manifest over all of the above.

The packet's `source_ref` has the form
`git-sha1:<commit>:tree:<root-tree>:manifest:<manifest-sha256>`. The ordinary
packet `source_set` independently lists every selected path, role, size, and
byte SHA-256. Compilation must match that expected source set exactly before an
output is written. This catches staging omissions, replacements, additions,
and byte drift while preserving the existing parser's bounded stable-file
checks.

A whole lexical `COMMANDS/` directory can be renamed or replaced after the
commit is created without changing what the CLI reviews. The commit tree, not
the worktree generation, remains authoritative for that run.

## What the ledger closes

The standalone `validate_commands.py` preflight answers whether each ticket
satisfies the repository ticket grammar. The batch ledger adds a separate,
conservative view over one exact committed source set:

1. exact source path, byte count, and SHA-256 for every selected ticket and
   receipt;
2. ticket-to-receipt reconciliation by exact ID, kind, operation, and optional
   mirrored authority fields;
3. exact ticket-byte binding when a receipt carries `ticket_sha256`;
4. semantic duplicate detection across different idempotency IDs;
5. orphan, duplicate, malformed, legacy-unbound, and conflicting receipt
   evidence; and
6. canonical packet, source-set digest, payload digest, and exact-commit replay
   verification.

Ticket bodies are hashed into the semantic action digest but are not copied into
the packet or Markdown summary.

## Receipt binding

The existing receipt format remains line-oriented:

```text
RECEIPT
operation=say
id=example-1
kind=say
claimed_from=GROK
authenticated_player=UNKNOWN
ticket_sha256=<lowercase SHA-256 of exact COMMANDS/example-1.txt bytes>
action_sha256=<optional lowercase semantic-action SHA-256>
HTTP is not the computer
```

`ticket_sha256` is required before the ledger will call a receipt an exact
bound match. Older receipts without it remain visible, but produce
`LEGACY_RECEIPT_UNBOUND` and hold the batch. This is intentional: an ID and a
few repeated fields cannot prove that the receipted ticket body is still the
same body now present under that ID.

`action_sha256` is optional. When supplied, it must match the ledger's canonical
semantic action digest. Neither digest authenticates a player or independently
proves that a claimed device effect occurred; it only binds supplied evidence
to supplied bytes.

## Semantic action identity

The action digest includes:

- every ticket field except `id`, `approved`, `claimed_from`, and
  `authenticated_player`; and
- the complete body, including exact embedded newlines.

The excluded fields are idempotency/provenance/validation fields rather than the
requested side effect. Unknown extension fields are included by default so a
future behavior-affecting field cannot silently disappear from action identity.

Consequences:

- two unreceipted ticket IDs with the same action digest both HOLD as
  `DUPLICATE_ACTION`;
- a new ID with the same action as an exact digest-bound receipted ticket HOLDs
  as `ACTION_ALREADY_RECEIPTED`;
- multiple exact bound receipts for one semantic action HOLD as
  `DUPLICATE_RECEIPTED_ACTION`; and
- changing sender, destination, owner ratification, body, kind, or an unknown
  extension field changes the action digest.

This is deliberately conservative. A human can create a genuinely different
command when repeated behavior is intended; the ledger never waives exact-once
review on its own.

## Aggregate states

- `EMPTY_BATCH` — no selected tickets or receipts.
- `READY_FOR_HOST_BATCH_REVIEW` — at least one ticket is individually valid,
  unreceipted, and free of batch conflicts. This is review readiness only.
- `NO_ACTIONABLE_COMMANDS` — every selected ticket has one exact digest-bound
  matching receipt and no conflict exists.
- `HOLD` — any malformed source, orphan/duplicate/conflicting receipt,
  duplicate action, legacy-unbound receipt, changed ticket bytes, or other
  blocker exists.

A successful compile may legitimately produce `HOLD`. Use `--fail-on-hold` when
a calling workflow wants that state to produce process exit code `3`. Parse,
Git, source-binding, or I/O failures use exit code `2`.

## Compile

From the repository root, pass the exact commit to review:

```bash
commit="$(git rev-parse HEAD)"
python -B command_batch.py compile \
  --git-repo . \
  --commands-dir COMMANDS \
  --receipts-dir COMMANDS/RECEIPTS \
  --source-ref "$commit" \
  --packet-out /new/path/command-batch.json \
  --summary-out /new/path/command-batch.md
```

Both output paths must be absent. Final-component symlinks and ordinary
overwrites are refused. A failed exclusive write can leave a partial file for
forensic/manual cleanup; the writer deliberately performs no pathname unlink
after failure because a check/use cleanup race could delete a foreign
replacement.

Packet and summary publication are two separate create-exclusive writes, not an
atomic two-file transaction. If the second write fails, the first output may
remain and must be reconciled manually before retrying.

The packet is canonical UTF-8 JSON with one trailing LF. `payload_sha256` covers
canonical payload bytes. `source_set_sha256` separately covers the sorted
path/role/size/source-digest list.

## Verify

Verification reads the packet, rejects duplicate JSON keys, floating-point or
non-finite numbers, noncanonical JSON, or a bad payload digest, then recompiles
from the same exact Git commit and rechecks the Git manifest binding:

```bash
python -B command_batch.py verify \
  --git-repo . \
  --commands-dir COMMANDS \
  --receipts-dir COMMANDS/RECEIPTS \
  --source-ref "$commit" \
  --packet /existing/command-batch.json \
  --summary /existing/command-batch.md
```

The supplied commit is out-of-band verifier input. A packet cannot pick its own
trusted commit. Commit/tree/manifest, packet, source-set, policy, ticket,
receipt, or summary drift fails verification.

## Input safety

Each selected ticket and receipt:

- must be an immediate `.txt` entry in the configured Git tree;
- must be a regular Git blob mode (`100644` or `100755`), never a symlink,
  submodule, or tree;
- is limited to 64 KiB before blob extraction;
- has its Git object ID recomputed from exact blob bytes;
- is staged as exact bytes in a private run directory;
- is re-read as one stable ordinary-file generation without following a final
  symlink where the platform supports it;
- must match the expected Git path/size/SHA-256 source set after compilation;
- is strict UTF-8 without BOM;
- is LF-delimited; and
- is rejected on C0 controls, CR, NEL, Unicode line/paragraph separators, or
  embedded BOM.

Receipts require one exact `RECEIPT` header, unique keys, a filename matching
`id`, and non-empty `id`, `kind`, `operation`, `claimed_from`, and
`authenticated_player` fields. `authenticated_player` must remain exactly
`UNKNOWN`. The historical exact statement `HTTP is not the computer` is
accepted; arbitrary free-form receipt lines are not.

## Tests

```bash
python -B -m py_compile command_batch.py command_batch_cli.py command_batch_compile.py command_batch_git.py command_batch_model.py command_batch_packet.py command_batch_sources.py test_command_batch_support.py test_command_batch_1.py test_command_batch_2.py test_command_batch_3.py test_command_batch_4.py test_command_batch_5.py
python -B -m unittest discover -v -p "test_command_batch_*.py"
python -O -B -m unittest discover -v -p "test_command_batch_*.py"
```

The hostile suite covers bound and legacy receipts, changed same-ID bytes,
reminted actions, duplicate IDs/actions, orphans, mismatched kind/operation,
KITE-to-GROK owner ratification, malformed encodings and separators, oversize
and nonregular inputs, symlink/path-replacement races, whole-worktree directory
replacement, exact-commit replay after worktree mutation, Git symlink entries,
Git-stage/source-set drift, order invariance, packet and source drift, duplicate
JSON keys, summary tamper, output collision, foreign replacement survival after
failed output writes, and direct CLI compile/verify round trips.
