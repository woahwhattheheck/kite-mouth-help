# Command ticket validation and batch review

Python tools for validating line-oriented command tickets and reconciling tickets
with receipts from an exact local Git commit. The tools generate deterministic
JSON review packets and Markdown summaries.

## Components

- `validate_commands.py`: bounded UTF-8 parsing and stable-file validation.
- `command_batch.py`: exact-commit source loading, duplicate detection,
  ticket-to-receipt matching, and deterministic packet verification.
- `COMMANDS/BATCH_LEDGER.md`: packet format, source binding, and CLI reference.
- `COMMANDS/HOW.txt`: ticket input format and local validation instructions.

## Local use

Use Python 3.10 or newer and Git. No third-party Python packages are required.

```bash
python -B validate_commands.py

commit="$(git rev-parse HEAD)"
python -B command_batch.py compile \
  --git-repo . \
  --commands-dir COMMANDS \
  --receipts-dir COMMANDS/RECEIPTS \
  --source-ref "$commit" \
  --packet-out /new/path/command-batch.json \
  --summary-out /new/path/command-batch.md
```

The output paths must not already exist. The selected commit must be available
locally. The repository contains input templates and technical documentation;
use a private working copy for actual tickets and receipts.

## Behavior

Validation and review do not execute tickets, send messages, authenticate
identities, or contact devices or service providers. A separate authorized
executor must validate the exact input bytes before dispatch.
