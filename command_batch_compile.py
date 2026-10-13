"""Deterministic ticket/receipt reconciliation and packet compilation."""
from __future__ import annotations
from pathlib import Path
from typing import Any, Sequence
import validate_commands as ticket_contract
from command_batch_model import (
    AUTHORITY_CEILING, MAX_INPUT_BYTES, NON_ACTION_FIELDS, POLICY_VERSION, SCHEMA,
    _action_sha256, _add_reason, _canonical_json_bytes, _decode_text, _error_code,
    _labeled_paths, _sha256, _sort_reasons, _validate_source_ref,
    BatchError,
)
from command_batch_sources import parse_receipt_bytes, read_bounded_regular


def compile_batch(
    ticket_paths: Sequence[Path],
    receipt_paths: Sequence[Path],
    *,
    source_ref: str,
    ticket_root: Path | None = None,
    receipt_root: Path | None = None,
) -> dict[str, Any]:
    """Compile exact ticket/receipt inputs into a deterministic non-authority packet."""
    source_ref = _validate_source_ref(source_ref)
    tickets: list[dict[str, Any]] = []
    receipts: list[dict[str, Any]] = []

    for label, path in _labeled_paths(ticket_paths, root=ticket_root, prefix="COMMANDS"):
        public: dict[str, Any] = {
            "action_sha256": None,
            "byte_count": None,
            "id": None,
            "kind": None,
            "path": label,
            "reasons": [],
            "receipt_path": None,
            "source_sha256": None,
            "status": "HOLD",
        }
        internal = {"public": public, "path": path, "ticket": None, "bound_receipt": False}
        try:
            source = read_bounded_regular(path, label=label, role="ticket")
            public["byte_count"] = source.byte_count
            public["source_sha256"] = source.sha256
            text = _decode_text(source.data, label=label, noun="TICKET")
            ticket = ticket_contract.parse_ticket(text)
            ticket_contract.validate_ticket(ticket, path=path)
            public["id"] = ticket.fields["id"]
            public["kind"] = ticket.fields["kind"]
            public["action_sha256"] = _action_sha256(ticket)
            internal["ticket"] = ticket
        except (BatchError, ticket_contract.TicketError) as exc:
            if isinstance(exc, BatchError):
                code, detail = _error_code(exc, "MALFORMED_TICKET")
            else:
                code, detail = "MALFORMED_TICKET", str(exc)
            _add_reason(public, code, detail)
        tickets.append(internal)

    for label, path in _labeled_paths(receipt_paths, root=receipt_root, prefix="COMMANDS/RECEIPTS"):
        public = {
            "action_sha256": None,
            "byte_count": None,
            "id": None,
            "kind": None,
            "operation": None,
            "path": label,
            "reasons": [],
            "source_sha256": None,
            "status": "HOLD",
            "ticket_sha256": None,
        }
        internal = {"public": public, "path": path, "receipt": None, "bound": False}
        try:
            source = read_bounded_regular(path, label=label, role="receipt")
            public["byte_count"] = source.byte_count
            public["source_sha256"] = source.sha256
            receipt = parse_receipt_bytes(source.data, path=path, label=label)
            public["id"] = receipt.fields["id"]
            public["kind"] = receipt.fields["kind"]
            public["operation"] = receipt.fields["operation"]
            public["action_sha256"] = receipt.fields.get("action_sha256")
            public["ticket_sha256"] = receipt.fields.get("ticket_sha256")
            internal["receipt"] = receipt
        except BatchError as exc:
            code, detail = _error_code(exc, "MALFORMED_RECEIPT")
            _add_reason(public, code, detail)
        receipts.append(internal)

    ticket_ids: dict[str, list[dict[str, Any]]] = {}
    for item in tickets:
        ticket_id = item["public"]["id"]
        if ticket_id is not None:
            ticket_ids.setdefault(ticket_id, []).append(item)
    for ticket_id, group in ticket_ids.items():
        if len(group) > 1:
            paths = ", ".join(sorted(item["public"]["path"] for item in group))
            for item in group:
                _add_reason(item["public"], "DUPLICATE_TICKET_ID", f"{ticket_id}: {paths}")

    receipt_ids: dict[str, list[dict[str, Any]]] = {}
    for item in receipts:
        receipt_id = item["public"]["id"]
        if receipt_id is not None:
            receipt_ids.setdefault(receipt_id, []).append(item)
    for receipt_id, group in receipt_ids.items():
        if len(group) > 1:
            paths = ", ".join(sorted(item["public"]["path"] for item in group))
            detail = f"{receipt_id}: {paths}"
            for item in group:
                _add_reason(item["public"], "DUPLICATE_RECEIPT_ID", detail)
            matching_tickets = ticket_ids.get(receipt_id, [])
            if len(matching_tickets) == 1:
                _add_reason(matching_tickets[0]["public"], "DUPLICATE_RECEIPT_ID", detail)

    for receipt_item in receipts:
        receipt = receipt_item["receipt"]
        if receipt is None:
            continue
        receipt_public = receipt_item["public"]
        receipt_id = receipt.fields["id"]
        candidates = ticket_ids.get(receipt_id, [])
        if not candidates:
            _add_reason(receipt_public, "ORPHAN_RECEIPT", f"no ticket with id {receipt_id!r}")
            continue
        if len(candidates) != 1:
            _add_reason(receipt_public, "AMBIGUOUS_RECEIPT", f"ticket id {receipt_id!r} is duplicated")
            continue
        ticket_item = candidates[0]
        ticket = ticket_item["ticket"]
        ticket_public = ticket_item["public"]
        ticket_public["receipt_path"] = receipt_public["path"]
        if ticket is None:
            _add_reason(receipt_public, "MALFORMED_MATCHED_TICKET", f"ticket {receipt_id!r} is malformed")
            continue

        mismatches: list[str] = []
        if receipt.fields["kind"] != ticket.fields["kind"]:
            mismatches.append("kind")
        if receipt.fields["operation"] != ticket.fields["kind"]:
            mismatches.append("operation")
        for key in sorted(set(receipt.fields) & set(ticket.fields)):
            if key in {"id", "kind"}:
                continue
            if receipt.fields[key] != ticket.fields[key]:
                mismatches.append(key)
        if mismatches:
            detail = "mismatched field(s): " + ", ".join(sorted(mismatches))
            _add_reason(receipt_public, "RECEIPT_TICKET_MISMATCH", detail)
            _add_reason(ticket_public, "RECEIPT_TICKET_MISMATCH", detail)

        bound_digest = receipt.fields.get("ticket_sha256")
        if bound_digest is None:
            detail = "receipt has no ticket_sha256 binding"
            _add_reason(receipt_public, "LEGACY_RECEIPT_UNBOUND", detail)
            _add_reason(ticket_public, "LEGACY_RECEIPT_UNBOUND", detail)
        elif bound_digest != ticket_public["source_sha256"]:
            detail = f"receipt {bound_digest} != ticket {ticket_public['source_sha256']}"
            _add_reason(receipt_public, "RECEIPT_TICKET_DIGEST_MISMATCH", detail)
            _add_reason(ticket_public, "RECEIPT_TICKET_DIGEST_MISMATCH", detail)
        action_digest = receipt.fields.get("action_sha256")
        if action_digest is not None and action_digest != ticket_public["action_sha256"]:
            detail = f"receipt {action_digest} != action {ticket_public['action_sha256']}"
            _add_reason(receipt_public, "RECEIPT_ACTION_DIGEST_MISMATCH", detail)
            _add_reason(ticket_public, "RECEIPT_ACTION_DIGEST_MISMATCH", detail)

        if not receipt_public["reasons"] and not ticket_public["reasons"]:
            receipt_item["bound"] = True
            ticket_item["bound_receipt"] = True

    for item in tickets:
        public = item["public"]
        if public["reasons"]:
            public["status"] = "HOLD"
        elif item["bound_receipt"]:
            public["status"] = "ALREADY_RECEIPTED"
        elif item["ticket"] is not None:
            public["status"] = "READY_FOR_HOST_REVIEW"

    receipts_by_path = {item["public"]["path"]: item for item in receipts}
    receipted_by_action: dict[str, list[dict[str, Any]]] = {}
    for item in tickets:
        public = item["public"]
        if public["status"] == "ALREADY_RECEIPTED":
            receipted_by_action.setdefault(public["action_sha256"], []).append(item)

    duplicate_receipted_actions = {
        digest: group for digest, group in receipted_by_action.items() if len(group) > 1
    }
    for action_digest, group in duplicate_receipted_actions.items():
        ids = ", ".join(sorted(item["public"]["id"] for item in group))
        detail = f"semantic action {action_digest} has bound receipts under ticket ids {ids}"
        for item in tickets:
            public = item["public"]
            if public["action_sha256"] == action_digest:
                _add_reason(public, "DUPLICATE_RECEIPTED_ACTION", detail)
                public["status"] = "HOLD"
        for item in group:
            receipt_path = item["public"]["receipt_path"]
            receipt_item = receipts_by_path.get(receipt_path)
            if receipt_item is not None:
                _add_reason(receipt_item["public"], "DUPLICATE_RECEIPTED_ACTION", detail)

    receipted_actions = {
        digest for digest, group in receipted_by_action.items() if len(group) == 1
    }
    for item in tickets:
        public = item["public"]
        if public["status"] == "READY_FOR_HOST_REVIEW" and public["action_sha256"] in receipted_actions:
            _add_reason(
                public,
                "ACTION_ALREADY_RECEIPTED",
                "a digest-bound receipt already covers the same semantic action under another id",
            )
            public["status"] = "HOLD"

    ready_by_action: dict[str, list[dict[str, Any]]] = {}
    for item in tickets:
        public = item["public"]
        if public["status"] == "READY_FOR_HOST_REVIEW":
            ready_by_action.setdefault(public["action_sha256"], []).append(item)
    for action_digest, group in ready_by_action.items():
        if len(group) > 1:
            ids = ", ".join(sorted(item["public"]["id"] for item in group))
            for item in group:
                _add_reason(
                    item["public"],
                    "DUPLICATE_ACTION",
                    f"semantic action {action_digest} appears under ticket ids {ids}",
                )
                item["public"]["status"] = "HOLD"

    for item in receipts:
        public = item["public"]
        if public["reasons"]:
            public["status"] = "HOLD"
        elif item["bound"]:
            public["status"] = "MATCHED_BOUND"

    for item in tickets:
        _sort_reasons(item["public"])
    for item in receipts:
        _sort_reasons(item["public"])

    public_tickets = sorted(
        (item["public"] for item in tickets),
        key=lambda item: (item["id"] is None, item["id"] or "", item["path"]),
    )
    public_receipts = sorted(
        (item["public"] for item in receipts),
        key=lambda item: (item["id"] is None, item["id"] or "", item["path"]),
    )

    blockers: list[dict[str, str]] = []
    for item in public_tickets:
        for reason in item["reasons"]:
            blockers.append(
                {
                    "code": reason["code"],
                    "detail": reason["detail"],
                    "subject": item["path"],
                }
            )
    for item in public_receipts:
        for reason in item["reasons"]:
            blockers.append(
                {
                    "code": reason["code"],
                    "detail": reason["detail"],
                    "subject": item["path"],
                }
            )
    blockers.sort(key=lambda item: (item["code"], item["subject"], item["detail"]))

    ready_count = sum(item["status"] == "READY_FOR_HOST_REVIEW" for item in public_tickets)
    received_count = sum(item["status"] == "ALREADY_RECEIPTED" for item in public_tickets)
    held_ticket_count = sum(item["status"] == "HOLD" for item in public_tickets)
    held_receipt_count = sum(item["status"] == "HOLD" for item in public_receipts)
    if blockers:
        state = "HOLD"
    elif not public_tickets and not public_receipts:
        state = "EMPTY_BATCH"
    elif ready_count:
        state = "READY_FOR_HOST_BATCH_REVIEW"
    else:
        state = "NO_ACTIONABLE_COMMANDS"

    source_set = [
        {
            "byte_count": item["byte_count"],
            "path": item["path"],
            "role": "ticket",
            "sha256": item["source_sha256"],
        }
        for item in public_tickets
    ] + [
        {
            "byte_count": item["byte_count"],
            "path": item["path"],
            "role": "receipt",
            "sha256": item["source_sha256"],
        }
        for item in public_receipts
    ]
    source_set.sort(key=lambda item: (item["role"], item["path"]))

    payload: dict[str, Any] = {
        "authority": AUTHORITY_CEILING,
        "blockers": blockers,
        "counts": {
            "blockers": len(blockers),
            "receipts_held": held_receipt_count,
            "receipts_matched_bound": sum(
                item["status"] == "MATCHED_BOUND" for item in public_receipts
            ),
            "receipts_total": len(public_receipts),
            "tickets_already_receipted": received_count,
            "tickets_held": held_ticket_count,
            "tickets_ready": ready_count,
            "tickets_total": len(public_tickets),
        },
        "policy": {
            "input_max_bytes": MAX_INPUT_BYTES,
            "policy_version": POLICY_VERSION,
            "receipt_requires_ticket_sha256_for_bound_match": True,
            "semantic_digest_excludes": sorted(NON_ACTION_FIELDS),
            "ticket_grammar": "validate_commands.py",
        },
        "receipts": public_receipts,
        "source_ref": source_ref,
        "source_set": source_set,
        "source_set_sha256": _sha256(_canonical_json_bytes(source_set)),
        "state": state,
        "tickets": public_tickets,
    }
    return {
        "payload": payload,
        "payload_sha256": _sha256(_canonical_json_bytes(payload)),
        "schema": SCHEMA,
    }
