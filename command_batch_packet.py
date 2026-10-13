"""Canonical packet rendering and exact offline verification."""
from __future__ import annotations
import json
from pathlib import Path
from typing import Any, Sequence
from command_batch_compile import compile_batch
from command_batch_model import (
    BatchError, SCHEMA, SHA256_RE, _canonical_json_bytes, _decode_text, _sha256,
)


def packet_bytes(packet: dict[str, Any]) -> bytes:
    return _canonical_json_bytes(packet, newline=True)


def _markdown_cell(value: Any) -> str:
    if value is None:
        return "—"
    return str(value).replace("|", "\\|").replace("\n", " ")


def render_summary(packet: dict[str, Any]) -> str:
    payload = packet["payload"]
    lines = [
        "# Command batch ledger",
        "",
        f"- State: `{payload['state']}`",
        f"- Source ref: `{payload['source_ref']}`",
        f"- Source-set SHA-256: `{payload['source_set_sha256']}`",
        f"- Payload SHA-256: `{packet['payload_sha256']}`",
        "- Authority: pre-dispatch review only; every execution/contact/authentication flag is `false`.",
        "",
        "## Tickets",
        "",
        "| id | kind | status | path | source SHA-256 | action SHA-256 | reasons |",
        "|---|---|---|---|---|---|---|",
    ]
    for item in payload["tickets"]:
        reasons = "; ".join(reason["code"] for reason in item["reasons"]) or "—"
        lines.append(
            "| "
            + " | ".join(
                _markdown_cell(value)
                for value in (
                    item["id"],
                    item["kind"],
                    item["status"],
                    item["path"],
                    item["source_sha256"],
                    item["action_sha256"],
                    reasons,
                )
            )
            + " |"
        )
    if not payload["tickets"]:
        lines.append("| — | — | EMPTY | — | — | — | — |")

    lines.extend(
        [
            "",
            "## Receipts",
            "",
            "| id | kind | operation | status | path | source SHA-256 | "
            "ticket SHA-256 | action SHA-256 | reasons |",
            "|---|---|---|---|---|---|---|---|---|",
        ]
    )
    for item in payload["receipts"]:
        reasons = "; ".join(reason["code"] for reason in item["reasons"]) or "—"
        lines.append(
            "| "
            + " | ".join(
                _markdown_cell(value)
                for value in (
                    item["id"],
                    item["kind"],
                    item["operation"],
                    item["status"],
                    item["path"],
                    item["source_sha256"],
                    item["ticket_sha256"],
                    item["action_sha256"],
                    reasons,
                )
            )
            + " |"
        )
    if not payload["receipts"]:
        lines.append("| — | — | — | EMPTY | — | — | — | — | — |")

    lines.extend(["", "## Blockers", ""])
    if payload["blockers"]:
        for blocker in payload["blockers"]:
            lines.append(
                f"- `{blocker['code']}` — `{blocker['subject']}` — {blocker['detail']}"
            )
    else:
        lines.append("- None.")
    lines.extend(
        [
            "",
            "## Non-authority boundary",
            "",
            "This ledger does not execute or authorize any command. The host puller "
            "must independently freeze and validate the exact bytes it consumes inside "
            "its own run.",
            "",
        ]
    )
    return "\n".join(lines)


def _reject_float(_: str) -> Any:
    raise BatchError("PACKET_NUMBER: floating-point values are forbidden")


def _reject_constant(value: str) -> Any:
    raise BatchError(f"PACKET_NUMBER: non-finite value {value!r} is forbidden")


def _pairs_no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise BatchError(f"PACKET_DUPLICATE_KEY: duplicate JSON key {key!r}")
        result[key] = value
    return result


def parse_packet_bytes(data: bytes) -> dict[str, Any]:
    text = _decode_text(data, label="packet", noun="PACKET")
    try:
        value = json.loads(
            text,
            object_pairs_hook=_pairs_no_duplicates,
            parse_float=_reject_float,
            parse_constant=_reject_constant,
        )
    except BatchError:
        raise
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise BatchError(f"PACKET_JSON: invalid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise BatchError("PACKET_SHAPE: top level must be an object")
    if value.get("schema") != SCHEMA:
        raise BatchError(f"PACKET_SCHEMA: expected {SCHEMA!r}")
    if set(value) != {"schema", "payload", "payload_sha256"}:
        raise BatchError("PACKET_SHAPE: unexpected top-level fields")
    if not isinstance(value.get("payload"), dict):
        raise BatchError("PACKET_SHAPE: payload must be an object")
    digest = value.get("payload_sha256")
    if not isinstance(digest, str) or not SHA256_RE.fullmatch(digest):
        raise BatchError("PACKET_DIGEST: payload_sha256 must be lowercase SHA-256")
    actual = _sha256(_canonical_json_bytes(value["payload"]))
    if digest != actual:
        raise BatchError(f"PACKET_DIGEST: {digest} != recomputed {actual}")
    if data != packet_bytes(value):
        raise BatchError("PACKET_CANONICAL: packet bytes are not canonical JSON")
    return value


def verify_packet(
    data: bytes,
    ticket_paths: Sequence[Path],
    receipt_paths: Sequence[Path],
    *,
    source_ref: str,
    ticket_root: Path | None = None,
    receipt_root: Path | None = None,
    summary: bytes | None = None,
) -> dict[str, Any]:
    observed = parse_packet_bytes(data)
    expected = compile_batch(
        ticket_paths,
        receipt_paths,
        source_ref=source_ref,
        ticket_root=ticket_root,
        receipt_root=receipt_root,
    )
    if data != packet_bytes(expected):
        raise BatchError("PACKET_DRIFT: packet does not recompile from the supplied exact inputs")
    if summary is not None:
        expected_summary = render_summary(expected).encode("utf-8")
        if summary != expected_summary:
            raise BatchError("SUMMARY_DRIFT: Markdown summary does not match the packet")
    return observed
