from __future__ import annotations

import imaplib
import re

from .authenticated_gmail import error_payload
from .authenticated_imap import (
    configured_imap_trash_folder,
    resolve_authenticated_imap_mailbox,
)
from .imap_folder_inventory import (
    is_runtime_compatible_mailbox_name,
    read_imap_list_inventory,
)
from .imap_trash import (
    analyze_trash_role,
    configurable_trash_folder_entries,
    resolve_trash_folder_from_inventory,
)
from ..user_config_store import (
    acquire_mailbox_mutation_lease,
    release_mailbox_mutation_lease,
    resolve_owned_managed_inbox_record,
    is_valid_custom_imap_folder_name,
    save_owned_custom_imap_folder_mapping,
)
from imap_connect_preview import connect_mailbox_with_settings


def _fixed_error(status_code: int, code: str, message: str) -> tuple[int, dict]:
    return status_code, error_payload(code, message)


def _authenticated_resolution_error(resolution: object) -> tuple[int, dict]:
    code = (
        resolution.get("error", {}).get("code")
        if type(resolution) is dict and type(resolution.get("error")) is dict
        else None
    )
    public = {
        "unauthorized": (401, "unauthorized", "A valid member session is required."),
        "managed_inbox_not_found": (
            404,
            "managed_inbox_not_found",
            "The requested mailbox was not found.",
        ),
        "reconnect_required": (
            409,
            "reconnect_required",
            "Reconnect this mailbox to continue.",
        ),
        "mailbox_configuration_unavailable": (
            503,
            "mailbox_configuration_unavailable",
            "Mailbox configuration is temporarily unavailable.",
        ),
        "mailbox_secret_store_unavailable": (
            503,
            "mailbox_secret_store_unavailable",
            "Mailbox credentials are temporarily unavailable.",
        ),
    }.get(code)
    if public is None:
        public = (
            500,
            "mailbox_configuration_malformed",
            "Mailbox configuration is invalid.",
        )
    return _fixed_error(*public)


def _owned_resolution_error(resolution: object) -> tuple[int, dict]:
    status = resolution.get("status") if type(resolution) is dict else None
    if status == "unauthorized":
        return _fixed_error(401, "unauthorized", "A valid member session is required.")
    if status == "not_found":
        return _fixed_error(
            404,
            "managed_inbox_not_found",
            "The requested mailbox was not found.",
        )
    if status == "unavailable":
        return _fixed_error(
            503,
            "mailbox_configuration_unavailable",
            "Mailbox configuration is temporarily unavailable.",
        )
    if status == "conflict":
        return _fixed_error(
            409,
            "mailbox_configuration_changed",
            "Mailbox configuration changed before the request completed.",
        )
    return _fixed_error(
        500,
        "mailbox_configuration_malformed",
        "Mailbox configuration is invalid.",
    )


def _valid_resolved_mailbox(value: object, mailbox_id: str) -> bool:
    if type(value) is not dict or value.get("mailboxId") != mailbox_id:
        return False
    for field in ("ownerEmail", "email"):
        field_value = value.get(field)
        if (
            not _valid_public_text(field_value, maximum_bytes=4_096)
            or re.fullmatch(r"[^@\s]+@[^@\s]+", field_value) is None
        ):
            return False
    mappings = value.get("customImapFolderMappings")
    _folder, mapping_error = configured_imap_trash_folder(mappings)
    if "customImapFolderMappings" not in value or mapping_error is not None:
        return False
    imap = value.get("imap")
    if type(imap) is not dict or set(imap) != {
        "host",
        "port",
        "ssl",
        "username",
        "password",
    }:
        return False
    if (
        not _valid_public_text(imap.get("host"), maximum_bytes=4_096)
        or any(character.isspace() for character in imap["host"])
        or type(imap.get("port")) is not int
        or not 1 <= imap["port"] <= 65535
        or imap.get("ssl") is not True
        or not _valid_public_text(imap.get("username"), maximum_bytes=4_096)
        or not _valid_private_text(imap.get("password"), maximum_bytes=65_536)
    ):
        return False
    return True


def _valid_public_text(value: object, *, maximum_bytes: int) -> bool:
    if (
        type(value) is not str
        or not value
        or value != value.strip()
        or any(
            ord(character) < 32 or 127 <= ord(character) <= 159
            for character in value
        )
    ):
        return False
    try:
        return len(value.encode("utf-8", errors="strict")) <= maximum_bytes
    except UnicodeEncodeError:
        return False


def _valid_private_text(value: object, *, maximum_bytes: int) -> bool:
    if (
        type(value) is not str
        or not value
        or any(
            ord(character) < 32 or 127 <= ord(character) <= 159
            for character in value
        )
    ):
        return False
    try:
        return len(value.encode("utf-8", errors="strict")) <= maximum_bytes
    except UnicodeEncodeError:
        return False


def _close_mailbox(mailbox) -> None:
    if mailbox is None:
        return
    try:
        mailbox.logout()
    except Exception:
        try:
            mailbox.shutdown()
        except Exception:
            pass


def _trash_state_from_inventory(
    mailbox_id: str,
    inventory,
    *,
    configured_folder: str | None,
) -> tuple[int, dict]:
    analysis = analyze_trash_role(inventory)
    category = getattr(analysis, "category", None)
    if category == "A":
        return _fixed_error(
            502,
            "imap_folder_inventory_failed",
            "IMAP folders could not be read safely.",
        )
    if category == "D":
        return _fixed_error(
            409,
            "trash_folder_ambiguous",
            "The Trash mailbox is ambiguous.",
        )
    if category == "E":
        return _fixed_error(
            409,
            "trash_folder_unavailable",
            "No safe Trash mailbox is available.",
        )

    resolution = resolve_trash_folder_from_inventory(
        inventory,
        configured_trash_folder=configured_folder,
    )
    if category == "C":
        if (
            getattr(resolution, "error", None) is not None
            or getattr(resolution, "source", None) != "special_use"
            or type(getattr(resolution, "folder", None)) is not str
        ):
            return _fixed_error(
                502,
                "imap_folder_inventory_failed",
                "IMAP folders could not be read safely.",
            )
        mode = "automatic"
        current_folder = resolution.folder
        folder_entries = ()
    elif category == "B":
        folder_entries = configurable_trash_folder_entries(inventory)
        if (
            getattr(resolution, "error", None) is None
            and getattr(resolution, "source", None) == "configured"
            and type(getattr(resolution, "folder", None)) is str
        ):
            mode = "configured"
            current_folder = resolution.folder
        else:
            mode = "needs_mapping"
            current_folder = None
    else:
        return _fixed_error(
            502,
            "imap_folder_inventory_failed",
            "IMAP folders could not be read safely.",
        )

    folders = []
    for entry in folder_entries:
        provider_folder = getattr(entry, "mailbox", None)
        if type(provider_folder) is not str:
            return _fixed_error(
                502,
                "imap_folder_inventory_failed",
                "IMAP folders could not be read safely.",
            )
        if not is_valid_custom_imap_folder_name(provider_folder):
            continue
        folders.append({"providerFolder": provider_folder})
    return 200, {
        "ok": True,
        "mailboxId": mailbox_id,
        "trash": {"mode": mode, "currentFolder": current_folder},
        "folders": folders,
    }


def _connect_resolved_mailbox(resolved_mailbox: dict):
    imap = resolved_mailbox["imap"]
    return connect_mailbox_with_settings(
        host=imap["host"],
        port=imap["port"],
        username=imap["username"],
        password=imap["password"],
        ssl_enabled=imap["ssl"],
    )


def list_imap_folders(headers, mailbox_id: str) -> tuple[int, dict]:
    resolution = resolve_authenticated_imap_mailbox(headers, mailbox_id)
    if (
        type(resolution) is not dict
        or resolution.get("status") != "ok"
        or type(resolution.get("mailbox")) is not dict
    ):
        return _authenticated_resolution_error(resolution)
    resolved_mailbox = resolution["mailbox"]
    if not _valid_resolved_mailbox(resolved_mailbox, mailbox_id):
        return _authenticated_resolution_error(None)
    configured_folder, mapping_error = configured_imap_trash_folder(
        resolved_mailbox["customImapFolderMappings"]
    )
    if mapping_error is not None:
        return _authenticated_resolution_error(None)

    mailbox = None
    try:
        mailbox = _connect_resolved_mailbox(resolved_mailbox)
        inventory = read_imap_list_inventory(mailbox)
        return _trash_state_from_inventory(
            mailbox_id,
            inventory,
            configured_folder=configured_folder,
        )
    except imaplib.IMAP4.error:
        if mailbox is None:
            return _fixed_error(
                401,
                "invalid_credentials",
                "Stored IMAP credentials were rejected.",
            )
        return _fixed_error(
            502,
            "imap_folder_inventory_failed",
            "IMAP folders could not be read safely.",
        )
    except Exception:
        return _fixed_error(
            502,
            "imap_connection_failed" if mailbox is None else "imap_folder_inventory_failed",
            "A secure IMAP connection could not be established."
            if mailbox is None
            else "IMAP folders could not be read safely.",
        )
    finally:
        _close_mailbox(mailbox)


def _save_under_lease(
    headers,
    mailbox_id: str,
    selected_folder: str,
) -> tuple[int, dict]:
    resolution = resolve_authenticated_imap_mailbox(headers, mailbox_id)
    if (
        type(resolution) is not dict
        or resolution.get("status") != "ok"
        or type(resolution.get("mailbox")) is not dict
    ):
        return _authenticated_resolution_error(resolution)
    resolved_mailbox = resolution["mailbox"]
    if not _valid_resolved_mailbox(resolved_mailbox, mailbox_id):
        return _authenticated_resolution_error(None)

    authority = resolve_owned_managed_inbox_record(headers, mailbox_id)
    if (
        type(authority) is not dict
        or authority.get("status") != "ok"
        or type(authority.get("user")) is not dict
        or type(authority.get("inbox")) is not dict
        or authority["user"].get("email") != resolved_mailbox["ownerEmail"]
    ):
        return _owned_resolution_error(authority)
    if authority["inbox"].get("provider") != "custom_imap":
        return _fixed_error(
            404,
            "managed_inbox_not_found",
            "The requested mailbox was not found.",
        )

    mailbox = None
    try:
        mailbox = _connect_resolved_mailbox(resolved_mailbox)
        inventory = read_imap_list_inventory(mailbox)
        analysis = analyze_trash_role(inventory)
        category = getattr(analysis, "category", None)
        if category == "C":
            return _trash_state_from_inventory(
                mailbox_id,
                inventory,
                configured_folder=None,
            )
        if category != "B":
            return _trash_state_from_inventory(
                mailbox_id,
                inventory,
                configured_folder=None,
            )

        selection = resolve_trash_folder_from_inventory(
            inventory,
            configured_trash_folder=selected_folder,
        )
        if (
            getattr(selection, "error", None) is not None
            or getattr(selection, "source", None) != "configured"
            or getattr(selection, "folder", None) != selected_folder
        ):
            return _fixed_error(
                409,
                "imap_folder_selection_stale",
                "The selected Trash folder is no longer available.",
            )

        save_result = save_owned_custom_imap_folder_mapping(
            headers,
            mailbox_id,
            selected_folder,
            expected_inbox=authority["inbox"],
        )
        if type(save_result) is not dict or save_result.get("status") != "ok":
            return _owned_resolution_error(save_result)
        saved_mapping = (
            save_result.get("inbox", {}).get("customImapFolderMappings")
            if type(save_result.get("inbox")) is dict
            else None
        )
        saved_folder, saved_mapping_error = configured_imap_trash_folder(
            saved_mapping
        )
        if saved_mapping_error is not None or saved_folder != selected_folder:
            return _fixed_error(
                503,
                "imap_folder_mapping_save_unavailable",
                "The Trash folder mapping could not be verified.",
            )

        readback = resolve_authenticated_imap_mailbox(headers, mailbox_id)
        if (
            type(readback) is not dict
            or readback.get("status") != "ok"
            or type(readback.get("mailbox")) is not dict
            or not _valid_resolved_mailbox(readback["mailbox"], mailbox_id)
        ):
            return _fixed_error(
                503,
                "imap_folder_mapping_save_unavailable",
                "The Trash folder mapping could not be verified.",
            )
        readback_folder, readback_error = configured_imap_trash_folder(
            readback["mailbox"]["customImapFolderMappings"]
        )
        if readback_error is not None or readback_folder != selected_folder:
            return _fixed_error(
                503,
                "imap_folder_mapping_save_unavailable",
                "The Trash folder mapping could not be verified.",
            )
        return _trash_state_from_inventory(
            mailbox_id,
            inventory,
            configured_folder=selected_folder,
        )
    except imaplib.IMAP4.error:
        if mailbox is None:
            return _fixed_error(
                401,
                "invalid_credentials",
                "Stored IMAP credentials were rejected.",
            )
        return _fixed_error(
            502,
            "imap_folder_inventory_failed",
            "IMAP folders could not be read safely.",
        )
    except Exception:
        return _fixed_error(
            502,
            "imap_connection_failed" if mailbox is None else "imap_folder_inventory_failed",
            "A secure IMAP connection could not be established."
            if mailbox is None
            else "IMAP folders could not be read safely.",
        )
    finally:
        _close_mailbox(mailbox)


def save_imap_folder_mapping(
    headers,
    mailbox_id: str,
    selected_folder: str,
) -> tuple[int, dict]:
    if (
        not is_runtime_compatible_mailbox_name(selected_folder)
        or not is_valid_custom_imap_folder_name(selected_folder)
    ):
        return _fixed_error(
            400,
            "invalid_request",
            "The selected Trash folder is invalid.",
        )

    authority = resolve_owned_managed_inbox_record(headers, mailbox_id)
    if (
        type(authority) is not dict
        or authority.get("status") != "ok"
        or type(authority.get("user")) is not dict
        or type(authority.get("inbox")) is not dict
    ):
        return _owned_resolution_error(authority)
    if authority["inbox"].get("provider") != "custom_imap":
        return _fixed_error(
            404,
            "managed_inbox_not_found",
            "The requested mailbox was not found.",
        )
    owner_email = authority["user"].get("email")
    if type(owner_email) is not str or not owner_email:
        return _owned_resolution_error(None)

    lease = acquire_mailbox_mutation_lease(owner_email, mailbox_id)
    if type(lease) is not dict or lease.get("status") != "acquired" or type(
        lease.get("token")
    ) is not str:
        if type(lease) is dict and lease.get("status") == "held":
            return _fixed_error(
                409,
                "mailbox_mutation_in_progress",
                "Another mailbox update is already in progress.",
            )
        return _fixed_error(
            503,
            "mailbox_mutation_lease_unavailable",
            "The mailbox update could not be started safely.",
        )

    result: tuple[int, dict]
    try:
        result = _save_under_lease(headers, mailbox_id, selected_folder)
    except Exception:
        result = _fixed_error(
            500,
            "internal_error",
            "The Trash folder mapping request could not be completed.",
        )
    try:
        release = release_mailbox_mutation_lease(
            owner_email,
            mailbox_id,
            lease["token"],
        )
    except Exception:
        release = None
    if type(release) is not dict or release.get("status") != "released":
        return _fixed_error(
            503,
            "mailbox_mutation_lease_unavailable",
            "The mailbox update could not be completed safely.",
        )
    return result
