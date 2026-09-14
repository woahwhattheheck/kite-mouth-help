# Deterministic command-batch ledger

`command_batch.py` is a read-only pre-dispatch reconciler for the public
`COMMANDS/` board. It compiles the exact ticket and receipt bytes selected for
one run into a canonical JSON packet and a deterministic Markdown summary.

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

The host puller remains responsible for independently freezing and validating
its own exact snapshot immediately before dispatch. A previously compiled
packet can become stale as soon as any ticket or receipt byte changes.

## What the ledger closes

The standalone `validate_commands.py` preflight answers whether each ticket
satisfies the repository ticket grammar. The batch ledger adds a separate,
conservative view over one frozen name-set:

1. exact source path, byte count, and SHA-256 for every selected ticket and
   receipt;
2. ticket-to-receipt reconciliation by exact ID, kind, operation, and optional
   mirrored authority fields;
3. exact ticket-byte binding when a receipt carries `ticket_sha256`;
4. semantic duplicate detection across different idempotency IDs;
5. orphan, duplicate, malformed, legacy-unbound, and conflicting receipt
   evidence;
6. canonical packet, source-set digest, payload digest, and offline
   recompilation verification.

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
a calling workflow wants that state to produce process exit code `3`. Parse or
I/O failures use exit code `2`.

## Compile

From the repository root:

```bash
python -B command_batch.py compile \
  --commands-dir COMMANDS \
  --receipts-dir COMMANDS/RECEIPTS \
  --source-ref <exact-git-commit-or-other-caller-ref> \
  --packet-out /new/path/command-batch.json \
  --summary-out /new/path/command-batch.md
```

The two output paths must not already exist. Final-component symlinks and
ordinary overwrites are refused. The command freezes each directory name-set
once, then reads only those selected names. A ticket arriving after that freeze
waits for a later run.

The packet is canonical UTF-8 JSON with one trailing LF. Its
`payload_sha256` covers canonical payload bytes. `source_set_sha256` separately
covers the sorted path/role/size/source-digest list.

## Verify

Verification re-reads the packet, rejects duplicate JSON keys, floating-point
or non-finite numbers, noncanonical JSON, or a bad payload digest, then
recompiles from the exact current sources:

```bash
python -B command_batch.py verify \
  --commands-dir COMMANDS \
  --receipts-dir COMMANDS/RECEIPTS \
  --source-ref <same-out-of-band-ref> \
  --packet /existing/command-batch.json \
  --summary /existing/command-batch.md
```

The supplied `--source-ref` is out-of-band verifier input. A packet cannot pick
its own trusted source ref. Packet, source-set, policy, ticket, receipt, or
summary drift fails verification.

## Input safety

Each ticket and receipt is:

- limited to 64 KiB;
- required to be one ordinary final-component file, not a symlink;
- opened without following a final symlink where the platform supports it;
- read from one stable inode/file generation;
- strict UTF-8 without BOM;
- LF-delimited; and
- rejected on C0 controls, CR, NEL, Unicode line/paragraph separators, or
  embedded BOM.

Receipts require one exact `RECEIPT` header, unique keys, a filename matching
`id`, and non-empty `id`, `kind`, `operation`, `claimed_from`, and
`authenticated_player` fields. `authenticated_player` must remain exactly
`UNKNOWN`. The historical exact statement
`HTTP is not the computer` is accepted; arbitrary free-form receipt lines are
not.

## Tests

```bash
python -B -m py_compile command_batch.py command_batch_cli.py command_batch_compile.py command_batch_model.py command_batch_packet.py command_batch_sources.py test_command_batch_support.py test_command_batch_1.py test_command_batch_2.py test_command_batch_3.py test_command_batch_4.py
python -B -m unittest discover -v -p "test_command_batch_*.py"
python -O -B -m unittest discover -v -p "test_command_batch_*.py"
```

The hostile suite covers bound and legacy receipts, changed same-ID bytes,
reminted actions, duplicate IDs/actions, orphans, mismatched kind/operation,
KITE-to-GROK owner ratification, malformed encodings and separators, oversize
and nonregular inputs, symlink/path-replacement races, order invariance, packet
and source drift, duplicate JSON keys, summary tamper, output collision, and a
direct CLI compile/verify round trip.
