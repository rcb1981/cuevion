"""Move exactly one owned, unsent Drafts item to Deleted Items (never hard delete).

Server-built Graph paths only. An immutable ID and exact Drafts membership must
be confirmed before the single non-repeatable move, and the resulting immutable
ID independently confirmed in Deleted Items before reporting success.
"""
from __future__ import annotations

from urllib.parse import quote

from api.inboxes.microsoft_archive_move import _json_graph
from api.inboxes.microsoft_graph import MicrosoftGraphError, _required_message_id


def discard_exact_microsoft_draft_to_deleted(
    access_token: str, message_id: str, *, before_write=None, opener=None,
) -> dict:
    exact_id = _required_message_id(message_id)
    encoded = quote(exact_id, safe="")
    destination = _json_graph(
        access_token, "/me/mailFolders/deleteditems?$select=id", opener=opener,
    )
    destination_id = _required_message_id(destination.get("id"))
    source_path = "/me/mailFolders/drafts/messages/" + encoded
    source = _json_graph(
        access_token, source_path + "?$select=id,parentFolderId,isDraft",
        opener=opener,
    )
    # Never move an already sent item or an item outside the real Drafts folder.
    if (
        type(source) is not dict
        or source.get("id") != exact_id
        or source.get("isDraft") is not True
        or type(source.get("parentFolderId")) is not str
    ):
        raise MicrosoftGraphError("microsoft_mutation_conflict")
    _required_message_id(source["parentFolderId"])
    drafts_folder = _json_graph(
        access_token, "/me/mailFolders/drafts?$select=id", opener=opener,
    )
    if source["parentFolderId"] != _required_message_id(drafts_folder.get("id")):
        raise MicrosoftGraphError("microsoft_mutation_conflict")
    if before_write is not None:
        before_write()

    # Exactly one Graph POST, even if a timeout or ambiguous response occurs.
    try:
        moved = _json_graph(
            access_token, source_path + "/move", method="POST",
            data=b'{"destinationId":"deleteditems"}', expected_status=201,
            opener=opener,
        )
    except MicrosoftGraphError as error:
        if error.code in {
            "microsoft_reconnect_required", "microsoft_message_not_found",
            "microsoft_mutation_conflict", "microsoft_graph_rate_limited",
        }:
            # Explicit Graph rejection of this one POST. A timeout, 5xx or
            # malformed response is never evidence that the move did not run.
            raise MicrosoftGraphError("microsoft_draft_discard_not_performed") from None
        moved = None
    if moved is not None and (
        moved.get("id") != exact_id
        or moved.get("parentFolderId") != destination_id
    ):
        raise MicrosoftGraphError("microsoft_mutation_unconfirmed")
    # A lost POST acknowledgement can still be resolved by a read. Failure
    # or absence here stays uncertain; never send another POST.
    try:
        confirmed = _json_graph(
            access_token,
            "/me/mailFolders/deleteditems/messages/" + encoded
            + "?$select=id,parentFolderId",
            opener=opener,
        )
    except MicrosoftGraphError:
        raise MicrosoftGraphError("microsoft_mutation_unconfirmed") from None
    if (
        confirmed.get("id") != exact_id
        or confirmed.get("parentFolderId") != destination_id
    ):
        raise MicrosoftGraphError("microsoft_mutation_unconfirmed")
    return {"providerMessageId": exact_id, "discarded": True}
