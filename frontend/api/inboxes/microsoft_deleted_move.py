"""Exact owner-guarded Inbox -> Deleted Items Graph move. Never permanently deletes.

This transport has a fixed Inbox source and a fixed well-known Deleted Items
destination; no arbitrary browser-provided Graph paths or target folders.
"""
from __future__ import annotations

from urllib.parse import quote

from api.inboxes.microsoft_archive_move import _json_graph
from api.inboxes.microsoft_graph import MicrosoftGraphError, _required_message_id


def move_exact_microsoft_inbox_to_deleted(
    access_token: str, message_id: str, *, before_write=None, opener=None,
) -> dict:
    exact_id = _required_message_id(message_id)
    encoded = quote(exact_id, safe="")
    destination = _json_graph(
        access_token, "/me/mailFolders/deleteditems?$select=id", opener=opener,
    )
    folder_id = destination.get("id")
    if type(folder_id) is not str:
        raise MicrosoftGraphError("microsoft_graph_invalid_response")
    _required_message_id(folder_id)

    source_path = "/me/mailFolders/inbox/messages/" + encoded
    source = _json_graph(
        access_token, source_path + "?$select=id,parentFolderId", opener=opener,
    )
    if source.get("id") != exact_id or type(source.get("parentFolderId")) is not str:
        raise MicrosoftGraphError("microsoft_graph_invalid_response")
    if before_write is not None:
        before_write()

    # Never repeat this non-idempotent request on an uncertain outcome.
    moved = _json_graph(
        access_token, source_path + "/move", method="POST",
        data=b'{"destinationId":"deleteditems"}', expected_status=201,
        opener=opener,
    )
    if moved.get("id") != exact_id or moved.get("parentFolderId") != folder_id:
        raise MicrosoftGraphError("microsoft_mutation_unconfirmed")

    try:
        confirmed = _json_graph(
            access_token,
            "/me/mailFolders/deleteditems/messages/" + encoded + "?$select=id,parentFolderId",
            opener=opener,
        )
    except MicrosoftGraphError:
        raise MicrosoftGraphError("microsoft_mutation_unconfirmed") from None
    if confirmed.get("id") != exact_id or confirmed.get("parentFolderId") != folder_id:
        raise MicrosoftGraphError("microsoft_mutation_unconfirmed")
    return {"providerMessageId": exact_id, "deleted": True}
