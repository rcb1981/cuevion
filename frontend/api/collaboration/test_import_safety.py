from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import unittest
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import patch

CURRENT_DIR = Path(__file__).resolve().parent
FRONTEND_ROOT = CURRENT_DIR.parents[1]
PACKAGE = "api.collaboration"


def _deployment_env() -> dict[str, str]:
    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    return environment


DEPLOYMENT_PATH_ASSERTIONS = (
    "repository_root = os.path.realpath(os.path.join(os.getcwd(), '..')); "
    "assert all(os.path.realpath(path or os.getcwd()) != repository_root "
    "for path in sys.path), sys.path"
)


class CollaborationV2ImportSafetyTests(unittest.TestCase):
    def test_candidate_imports_do_not_require_secrets_or_perform_io(self):
        script = textwrap.dedent(
            f"""
            import importlib
            import imaplib
            import os
            import smtplib
            import sys
            from unittest.mock import patch

            {DEPLOYMENT_PATH_ASSERTIONS}
            short_names = ("models", "redis_store", "authorization", "source_message", "guest_session", "mutations", "http_boundary", "application")
            with patch.dict(os.environ, {{}}, clear=True), patch(
                "urllib.request.urlopen", side_effect=AssertionError("network during import")
            ), patch(
                "socket.create_connection", side_effect=AssertionError("socket during import")
            ), patch(
                "imaplib.IMAP4", side_effect=AssertionError("IMAP during import")
            ), patch(
                "imaplib.IMAP4_SSL", side_effect=AssertionError("IMAPS during import")
            ), patch(
                "smtplib.SMTP", side_effect=AssertionError("SMTP during import")
            ), patch(
                "smtplib.SMTP_SSL", side_effect=AssertionError("SMTPS during import")
            ):
                before_path = list(sys.path)
                for short_name in short_names:
                    importlib.import_module(f"{PACKAGE}.{{short_name}}")
            assert sys.path == before_path
            imported = {{name: sys.modules[f"{PACKAGE}.{{name}}"] for name in short_names}}
            assert imported["source_message"].resolve_internal_collaboration_context is imported["authorization"].resolve_internal_collaboration_context
            assert imported["mutations"]._is_guest_mutation_capability is imported["guest_session"]._is_guest_mutation_capability
            assert sys.modules["models"] is imported["models"]
            assert sys.modules["redis_store"] is imported["redis_store"]
            assert all(name not in sys.modules for name in short_names[2:])
            assert "api.inboxes.fetch-gmail" not in sys.modules
            assert "api.inboxes.authenticated_gmail" not in sys.modules
            assert "authenticated_gmail" not in sys.modules
            """
        )
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=FRONTEND_ROOT,
            env=_deployment_env(),
            text=True,
            capture_output=True,
            timeout=15,
            check=False,
        )
        self.assertEqual(
            result.returncode,
            0,
            msg=f"stdout={result.stdout!r} stderr={result.stderr!r}",
        )

    def test_top_level_identity_cannot_coexist_for_any_v2_module_in_either_import_order(self):
        forwarding_aliases = {"models", "redis_store"}
        short_names = (
            "models", "redis_store", "authorization", "source_message",
            "guest_session", "mutations", "http_boundary", "application",
        )
        for short_name in short_names:
            for order in ("package_first", "top_level_first"):
                canonical_name = f"{PACKAGE}.{short_name}"
                script = textwrap.dedent(
                    f"""
                    import importlib
                    import os
                    import sys

                    {DEPLOYMENT_PATH_ASSERTIONS}
                    sys.path.insert(0, {str(CURRENT_DIR)!r})
                    before_path = list(sys.path)
                    canonical_name = {canonical_name!r}
                    if {order!r} == "package_first":
                        canonical = importlib.import_module(canonical_name)
                    try:
                        top_level = importlib.import_module({short_name!r})
                    except ImportError:
                        if {short_name in forwarding_aliases!r}:
                            raise
                    else:
                        if not {short_name in forwarding_aliases!r}:
                            raise AssertionError("top-level Collaboration identity was accepted")
                    if {order!r} == "top_level_first":
                        canonical = importlib.import_module(canonical_name)
                    assert sys.modules[canonical_name] is canonical
                    if {short_name in forwarding_aliases!r}:
                        assert top_level is canonical
                        assert sys.modules[{short_name!r}] is canonical
                    else:
                        assert {short_name!r} not in sys.modules
                    assert canonical.__name__ == canonical_name
                    assert sys.path == before_path
                    """
                )
                result = subprocess.run(
                    [sys.executable, "-c", script],
                    cwd=FRONTEND_ROOT,
                    env=_deployment_env(),
                    text=True,
                    capture_output=True,
                    timeout=15,
                    check=False,
                )
                self.assertEqual(
                    result.returncode,
                    0,
                    msg=(
                        f"{short_name}/{order}: stdout={result.stdout!r} "
                        f"stderr={result.stderr!r}"
                    ),
                )

    def test_shared_provider_aliases_forward_to_canonical_object_in_both_orders(self):
        api_dir = FRONTEND_ROOT / "api"
        inbox_dir = api_dir / "inboxes"
        providers = (
            ("api.user_config_store", "user_config_store", (api_dir,)),
            (
                "api.inboxes.mailbox_secret_store",
                "mailbox_secret_store",
                (inbox_dir, api_dir),
            ),
            (
                "api.inboxes.authenticated_gmail",
                "authenticated_gmail",
                (inbox_dir, api_dir),
            ),
            (
                "api.inboxes.authenticated_imap",
                "authenticated_imap",
                (inbox_dir, api_dir),
            ),
            (
                "api.inboxes.oauth_token_store",
                "oauth_token_store",
                (inbox_dir,),
            ),
            (
                "api.inboxes.fetch-gmail",
                "fetch-gmail",
                (inbox_dir, api_dir),
            ),
        )
        for canonical_name, legacy_name, search_paths in providers:
            for order in ("package_first", "top_level_first"):
                script = textwrap.dedent(
                    f"""
                    import importlib
                    import os
                    import sys

                    {DEPLOYMENT_PATH_ASSERTIONS}
                    for path in {tuple(map(str, search_paths))!r}:
                        sys.path.insert(0, path)
                    before_path = list(sys.path)
                    canonical_name = {canonical_name!r}
                    legacy_name = {legacy_name!r}
                    if {order!r} == "package_first":
                        canonical = importlib.import_module(canonical_name)
                        legacy = importlib.import_module(legacy_name)
                    else:
                        legacy = importlib.import_module(legacy_name)
                        canonical = importlib.import_module(canonical_name)
                    assert canonical is legacy
                    assert sys.modules[canonical_name] is canonical
                    assert sys.modules[legacy_name] is canonical
                    assert canonical.__name__ == canonical_name
                    assert sys.path == before_path
                    """
                )
                result = subprocess.run(
                    [sys.executable, "-c", script],
                    cwd=FRONTEND_ROOT,
                    env=_deployment_env(),
                    text=True,
                    capture_output=True,
                    timeout=15,
                    check=False,
                )
                self.assertEqual(
                    result.returncode,
                    0,
                    msg=(
                        f"{canonical_name}/{order}: stdout={result.stdout!r} "
                        f"stderr={result.stderr!r}"
                    ),
                )

    def test_all_active_handlers_import_in_both_relevant_orders(self):
        active_handlers = (
            "api.beta.login",
            "api.beta.logout",
            "api.beta.session",
            "api.collaboration.thread",
            "api.collaboration.invite",
            "api.contact.support",
            "api.inboxes.connect-imap",
            "api.inboxes.connect-oauth",
            "api.inboxes.credentials",
            "api.inboxes.download-attachment",
            "api.inboxes.fetch-gmail-thread",
            "api.inboxes.fetch-gmail",
            "api.inboxes.message-action",
            "api.inboxes.oauth-callback",
            "api.inboxes.send-gmail",
            "api.organizer.soundcloud-resolve",
            "api.team.invite",
            "api.team.members",
            "api.user.config",
        )
        canonical_helpers = (
            "api.collaboration.models",
            "api.collaboration.redis_store",
            "api.collaboration.authorization",
            "api.collaboration.source_message",
            "api.collaboration.guest_session",
            "api.collaboration.mutations",
            "api.collaboration.http_boundary",
            "api.collaboration.application",
            "api.user_config_store",
            "api.inboxes.mailbox_secret_store",
            "api.inboxes.authenticated_gmail",
            "api.inboxes.authenticated_imap",
            "api.inboxes.oauth_token_store",
            "api.inboxes.fetch-gmail",
            "imap_connect_preview",
        )
        script_template = """
            import importlib
            import imaplib
            import os
            import smtplib
            import sys
            from unittest.mock import patch

            {path_assertions}
            handlers = {handlers!r}
            helpers = {helpers!r}
            order = {order!r}
            with patch.dict(os.environ, {{}}, clear=True), patch(
                "urllib.request.urlopen", side_effect=AssertionError("network during import")
            ), patch(
                "socket.create_connection", side_effect=AssertionError("socket during import")
            ), patch(
                "imaplib.IMAP4", side_effect=AssertionError("IMAP during import")
            ), patch(
                "imaplib.IMAP4_SSL", side_effect=AssertionError("IMAPS during import")
            ), patch(
                "smtplib.SMTP", side_effect=AssertionError("SMTP during import")
            ), patch(
                "smtplib.SMTP_SSL", side_effect=AssertionError("SMTPS during import")
            ):
                if order == "helpers_first":
                    for name in helpers:
                        importlib.import_module(name)
                for name in handlers:
                    importlib.import_module(name)
                if order == "handlers_first":
                    for name in helpers:
                        importlib.import_module(name)
            assert sys.modules["models"] is sys.modules["api.collaboration.models"]
            assert sys.modules["redis_store"] is sys.modules["api.collaboration.redis_store"]
            for canonical, legacy in (
                ("api.user_config_store", "user_config_store"),
                ("api.inboxes.mailbox_secret_store", "mailbox_secret_store"),
                ("api.inboxes.authenticated_gmail", "authenticated_gmail"),
                ("api.inboxes.authenticated_imap", "authenticated_imap"),
                ("api.inboxes.oauth_token_store", "oauth_token_store"),
                ("api.inboxes.fetch-gmail", "fetch-gmail"),
            ):
                assert sys.modules[canonical] is sys.modules[legacy]
        """
        for order in ("helpers_first", "handlers_first"):
            script = textwrap.dedent(
                script_template.format(
                    path_assertions=DEPLOYMENT_PATH_ASSERTIONS,
                    handlers=active_handlers,
                    helpers=canonical_helpers,
                    order=order,
                )
            )
            result = subprocess.run(
                [sys.executable, "-c", script],
                cwd=FRONTEND_ROOT,
                env=_deployment_env(),
                text=True,
                capture_output=True,
                timeout=30,
                check=False,
            )
            self.assertEqual(
                result.returncode,
                0,
                msg=f"{order}: stdout={result.stdout!r} stderr={result.stderr!r}",
            )

    def test_active_collaboration_and_inbox_handlers_do_not_import_inactive_boundaries(self):
        active_handlers = (
            "api.collaboration.thread",
            "api.collaboration.invite",
            "api.inboxes.connect-imap",
            "api.inboxes.connect-oauth",
            "api.inboxes.credentials",
            "api.inboxes.download-attachment",
            "api.inboxes.fetch-gmail-thread",
            "api.inboxes.fetch-gmail",
            "api.inboxes.message-action",
            "api.inboxes.oauth-callback",
            "api.inboxes.send-gmail",
        )
        script = textwrap.dedent(
            f"""
            import importlib
            import imaplib
            import os
            import smtplib
            import sys
            from unittest.mock import patch

            {DEPLOYMENT_PATH_ASSERTIONS}
            inactive_names = (
                "api.collaboration.http_boundary", "http_boundary",
                "api.collaboration.application", "application",
            )
            assert all(name not in sys.modules for name in inactive_names)
            with patch.dict(os.environ, {{}}, clear=True), patch(
                "urllib.request.urlopen", side_effect=AssertionError("network during import")
            ), patch(
                "socket.create_connection", side_effect=AssertionError("socket during import")
            ), patch(
                "imaplib.IMAP4", side_effect=AssertionError("IMAP during import")
            ), patch(
                "imaplib.IMAP4_SSL", side_effect=AssertionError("IMAPS during import")
            ), patch(
                "smtplib.SMTP", side_effect=AssertionError("SMTP during import")
            ), patch(
                "smtplib.SMTP_SSL", side_effect=AssertionError("SMTPS during import")
            ):
                for handler_name in {active_handlers!r}:
                    importlib.import_module(handler_name)
                    assert all(name not in sys.modules for name in inactive_names), handler_name
            """
        )
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=FRONTEND_ROOT,
            env=_deployment_env(),
            text=True,
            capture_output=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(
            result.returncode,
            0,
            msg=f"stdout={result.stdout!r} stderr={result.stderr!r}",
        )

    def test_arbitrary_alternate_dotted_identities_fail(self):
        modules = (
            ("api.collaboration.models_copy", "api/collaboration/models.py"),
            ("api.collaboration.redis_store_copy", "api/collaboration/redis_store.py"),
            ("api.collaboration.authorization_copy", "api/collaboration/authorization.py"),
            ("api.collaboration.source_message_copy", "api/collaboration/source_message.py"),
            ("api.collaboration.guest_session_copy", "api/collaboration/guest_session.py"),
            ("api.collaboration.mutations_copy", "api/collaboration/mutations.py"),
            ("api.collaboration.http_boundary_copy", "api/collaboration/http_boundary.py"),
            ("api.collaboration.application_copy", "api/collaboration/application.py"),
            ("collaboration.application", "api/collaboration/application.py"),
            ("api.user_config_store_copy", "api/user_config_store.py"),
            ("api.inboxes.mailbox_secret_store_copy", "api/inboxes/mailbox_secret_store.py"),
            ("api.inboxes.authenticated_gmail_copy", "api/inboxes/authenticated_gmail.py"),
            ("api.inboxes.authenticated_imap_copy", "api/inboxes/authenticated_imap.py"),
            ("api.inboxes.oauth_token_store_copy", "api/inboxes/oauth_token_store.py"),
            ("api.inboxes.fetch_gmail_copy", "api/inboxes/fetch-gmail.py"),
            ("frontend.imap_connect_preview", "imap_connect_preview.py"),
        )
        script = textwrap.dedent(
            f"""
            import importlib
            import importlib.util
            import os
            import sys

            {DEPLOYMENT_PATH_ASSERTIONS}
            for alias, path in {modules!r}:
                spec = importlib.util.spec_from_file_location(alias, path)
                module = importlib.util.module_from_spec(spec)
                sys.modules[alias] = module
                try:
                    spec.loader.exec_module(module)
                except ImportError:
                    pass
                else:
                    raise AssertionError(f"alternate module identity accepted: {{alias}}")
                finally:
                    sys.modules.pop(alias, None)
            authorization = importlib.import_module("api.collaboration.authorization")
            guest_session = importlib.import_module("api.collaboration.guest_session")
            assert authorization._InternalCollaborationCapability.__module__ == "api.collaboration.authorization"
            assert guest_session._GuestMutationCapability.__module__ == "api.collaboration.guest_session"
            """
        )
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=FRONTEND_ROOT,
            env=_deployment_env(),
            text=True,
            capture_output=True,
            timeout=15,
            check=False,
        )
        self.assertEqual(
            result.returncode,
            0,
            msg=f"stdout={result.stdout!r} stderr={result.stderr!r}",
        )

    def test_protected_v1_routes_import_with_only_canonical_shared_module_objects(self):
        script = textwrap.dedent(
            f"""
            import importlib
            import os
            import sys
            from unittest.mock import patch

            {DEPLOYMENT_PATH_ASSERTIONS}
            with patch("urllib.request.urlopen", side_effect=AssertionError("network during route import")):
                thread = importlib.import_module("api.collaboration.thread")
                invite = importlib.import_module("api.collaboration.invite")
            canonical_models = sys.modules["api.collaboration.models"]
            canonical_store = sys.modules["api.collaboration.redis_store"]
            assert sys.modules["models"] is canonical_models
            assert sys.modules["redis_store"] is canonical_store
            assert thread.normalize_collaboration_thread_record is canonical_models.normalize_collaboration_thread_record
            assert invite.get_invite is canonical_store.get_invite
            """
        )
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=FRONTEND_ROOT,
            env=_deployment_env(),
            text=True,
            capture_output=True,
            timeout=15,
            check=False,
        )
        self.assertEqual(
            result.returncode,
            0,
            msg=f"stdout={result.stdout!r} stderr={result.stderr!r}",
        )

    def test_canonical_capabilities_interoperate_and_copied_classes_are_rejected(self):
        from . import authorization, guest_session, models, mutations, redis_store, source_message

        seconds = 1_800_000_000
        milliseconds = seconds * 1000
        thread = {
            "v": 2,
            "collaborationId": "A" * 22,
            "ownerEmail": "owner@example.com",
            "workspaceId": "owner@example.com",
            "mailboxId": "mailbox-1",
            "sourceRef": {"provider": "google", "providerMessageId": "gmail-1"},
            "sourceMessage": {
                "subject": "Review",
                "senderDisplay": "Sender",
                "fromDisplay": "sender@example.com",
                "timestamp": "today",
                "bodyText": "Body",
            },
            "state": "needs_review",
            "messages": [],
            "createdAt": milliseconds + 100,
            "updatedAt": milliseconds + 100,
        }

        def mint_internal(action: str):
            result = authorization.resolve_internal_collaboration_context(
                [],
                "mailbox-1",
                collaboration_id=thread["collaborationId"],
                required_action=action,
                user_resolver=lambda _headers: (
                    {"email": "owner@example.com", "name": "Owner"},
                    None,
                ),
                mailbox_resolver=lambda _headers, mailbox_id: {
                    "status": "ok",
                    "user": {"email": "owner@example.com"},
                    "inbox": {"id": mailbox_id, "provider": "google"},
                },
                thread_loader=lambda _collaboration_id: {
                    "status": "ok", "record": thread,
                },
            )
            self.assertEqual(result["status"], "ok")
            return result["context"]

        reply_context = mint_internal("reply")
        create_context = mint_internal("create")
        issue_context = mint_internal("issue_invite")
        self.assertEqual(
            type(reply_context).__module__, "api.collaboration.authorization"
        )
        with patch.object(models.time, "time_ns", return_value=(milliseconds + 101) * 1_000_000):
            self.assertIsNotNone(models.build_v2_owner_shared_message(reply_context, "Reply"))

        with patch.object(mutations.time, "time_ns", return_value=(milliseconds + 101) * 1_000_000):
            mutation_result = mutations.append_internal_v2_message(
                reply_context,
                "Reply",
                visibility="shared",
                thread_loader=lambda *_args, **_kwargs: {"status": "ok", "record": thread},
                thread_saver=lambda record, _expected, **_kwargs: {
                    "status": "ok", "record": record,
                },
            )
        self.assertEqual(mutation_result["status"], "ok")

        raw_message = (
            b"From: Sender <sender@example.com>\r\n"
            b"Subject: Review\r\n"
            b"Date: today\r\n\r\nBody"
        )
        source_result = source_message.resolve_source_message(
            [],
            {"mailboxId": "mailbox-1", "sourceRef": {"providerMessageId": "gmail-1"}},
            authorization_resolver=lambda *_args, **_kwargs: {
                "status": "ok", "context": create_context, "error": None,
            },
            google_fetcher=lambda *_args: {"status": "ok", "rawMessage": raw_message},
        )
        self.assertEqual(source_result["status"], "ok")

        with patch.object(
            guest_session,
            "_create_v2_invite",
            side_effect=lambda record, **_kwargs: {
                "status": "ok", "record": record, "created": True,
            },
        ):
            issue_result = guest_session.issue_v2_invitation(
                issue_context,
                thread["collaborationId"],
                now=seconds + 100,
                thread_loader=lambda *_args, **_kwargs: {
                    "status": "ok", "record": thread,
                },
            )
        self.assertEqual(issue_result["status"], "ok")

        @dataclass(frozen=True)
        class CopiedInternalCapability:
            _sentinel: object
            owner_email: str
            workspace_id: str
            mailbox_id: str
            mailbox_provider: str
            collaboration_id: str
            action: str
            actor_kind: str
            actor_display_name: str

        copied_internal = CopiedInternalCapability(
            authorization._INTERNAL_CAPABILITY_SENTINEL,
            reply_context.owner_email,
            reply_context.workspace_id,
            reply_context.mailbox_id,
            reply_context.mailbox_provider,
            reply_context.collaboration_id,
            reply_context.action,
            reply_context.actor_kind,
            reply_context.actor_display_name,
        )
        self.assertFalse(authorization._is_internal_capability(copied_internal))
        self.assertIsNone(models.build_v2_owner_shared_message(copied_internal, "Reply"))
        self.assertEqual(
            mutations.append_internal_v2_message(
                copied_internal,
                "Reply",
                visibility="shared",
                thread_loader=lambda *_args, **_kwargs: self.fail("copy reached storage"),
            )["error"]["code"],
            "forbidden",
        )
        self.assertEqual(
            source_message.resolve_source_message(
                [],
                {"mailboxId": "mailbox-1", "sourceRef": {"providerMessageId": "gmail-1"}},
                authorization_resolver=lambda *_args, **_kwargs: {
                    "status": "ok", "context": copied_internal, "error": None,
                },
                google_fetcher=lambda *_args: self.fail("copy reached provider"),
            )["error"]["code"],
            "storage_protocol_error",
        )
        self.assertEqual(
            guest_session.issue_v2_invitation(
                copied_internal,
                thread["collaborationId"],
                now=seconds + 100,
                thread_loader=lambda *_args, **_kwargs: self.fail("copy reached storage"),
            )["error"]["code"],
            "invalid_request",
        )

        raw_session_id = "s" * 43
        csrf_token = "c" * 43
        session = {
            "v": 2,
            "sessionHash": models.hash_v2_secret(raw_session_id),
            "inviteId": "I" * 22,
            "ownerEmail": "owner@example.com",
            "workspaceId": "owner@example.com",
            "mailboxId": "mailbox-1",
            "collaborationId": thread["collaborationId"],
            "allowedActions": ["read", "reply"],
            "visibility": "shared_only",
            "identityAssurance": "link_possession",
            "guestDisplayName": "Reviewer",
            "createdAt": seconds + 100,
            "lastUsedAt": seconds + 100,
            "expiresAt": seconds + 500,
            "status": "active",
            "csrfTokenHash": models.hash_v2_secret(csrf_token),
            "revokedAt": None,
            "loggedOutAt": None,
        }
        invite = {
            "v": 2,
            "inviteId": session["inviteId"],
            "tokenHash": "a" * 64,
            "ownerEmail": session["ownerEmail"],
            "workspaceId": session["workspaceId"],
            "mailboxId": session["mailboxId"],
            "collaborationId": session["collaborationId"],
            "identityAssurance": "link_possession",
            "allowedActions": ["read", "reply"],
            "visibility": "shared_only",
            "createdBy": {"ownerEmail": session["ownerEmail"], "displayName": "Owner"},
            "createdAt": seconds + 50,
            "expiresAt": seconds + 500,
            "status": "exchanged",
            "exchangedAt": seconds + 100,
            "exchangeCount": 1,
            "revokedAt": None,
            "revokedBy": None,
            "activeSessionHash": session["sessionHash"],
        }
        headers = [
            ("Origin", "https://app.cuevion.com"),
            ("Content-Type", "application/json"),
            ("X-Cuevion-CSRF", csrf_token),
            ("Cookie", f"{guest_session.GUEST_SESSION_COOKIE_NAME}={raw_session_id}"),
        ]
        with patch.dict(
            guest_session.os.environ,
            {"VERCEL_ENV": "production", "CUEVION_APP_ORIGIN": "https://app.cuevion.com"},
            clear=True,
        ), patch.object(
            guest_session, "_load_v2_guest_session_record",
            return_value={"status": "ok", "record": session},
        ), patch.object(
            guest_session, "_load_v2_invite_by_id",
            return_value={"status": "ok", "record": invite},
        ):
            guest_result = guest_session.resolve_guest_v2_mutation_context(
                "POST", headers, now=seconds + 101
            )
        self.assertEqual(guest_result["status"], "ok")
        guest_context = guest_result["context"]
        self.assertEqual(
            type(guest_context).__module__, "api.collaboration.guest_session"
        )
        self.assertIs(
            mutations._is_guest_mutation_capability,
            guest_session._is_guest_mutation_capability,
        )
        self.assertIsNotNone(models.build_v2_guest_shared_reply(
            guest_context, "Guest reply", _created_at=milliseconds + 101
        ))

        with patch.object(mutations.time, "time", return_value=seconds + 101), patch.object(
            mutations.time, "time_ns", return_value=(milliseconds + 101) * 1_000_000
        ):
            guest_mutation_result = mutations.append_guest_v2_reply(
                guest_context,
                "Guest reply",
                thread_loader=lambda *_args, **_kwargs: {"status": "ok", "record": thread},
                thread_saver=lambda record, _expected, **_kwargs: {
                    "status": "ok", "record": record,
                },
            )
        self.assertEqual(guest_mutation_result["status"], "ok")

        with patch.object(
            redis_store, "resolve_v2_index_hmac_keys", return_value=(b"k" * 32, None)
        ), patch.object(
            redis_store, "_v2_eval", return_value={"status": "saved"}
        ) as redis_eval:
            redis_result = redis_store._append_v2_guest_reply_if_expected(
                thread,
                thread["updatedAt"],
                session_context=guest_context,
                now=seconds + 101,
            )
        self.assertEqual(redis_result["status"], "ok")
        redis_eval.assert_called_once()

        @dataclass(frozen=True)
        class CopiedGuestCapability:
            _sentinel: object
            session_hash: str
            invite_id: str
            owner_email: str
            workspace_id: str
            mailbox_id: str
            collaboration_id: str
            guest_display_name: str
            expires_at: int
            created_at: int
            last_used_at: int

        copied_guest = CopiedGuestCapability(
            guest_session._GUEST_MUTATION_SENTINEL,
            guest_context.session_hash,
            guest_context.invite_id,
            guest_context.owner_email,
            guest_context.workspace_id,
            guest_context.mailbox_id,
            guest_context.collaboration_id,
            guest_context.guest_display_name,
            guest_context.expires_at,
            guest_context.created_at,
            guest_context.last_used_at,
        )
        self.assertFalse(guest_session._is_guest_mutation_capability(copied_guest))
        self.assertIsNone(models.build_v2_guest_shared_reply(copied_guest, "Guest reply"))
        self.assertEqual(
            mutations.append_guest_v2_reply(
                copied_guest,
                "Guest reply",
                thread_loader=lambda *_args, **_kwargs: self.fail("copy reached storage"),
            )["error"]["code"],
            "session_revoked",
        )
        with patch.object(redis_store, "_v2_eval") as redis_eval:
            rejected = redis_store._append_v2_guest_reply_if_expected(
                thread,
                thread["updatedAt"],
                session_context=copied_guest,
                now=seconds + 101,
            )
        self.assertEqual(rejected["error"]["code"], "invalid_request")
        redis_eval.assert_not_called()

    def test_active_routes_do_not_reference_inactive_modules(self):
        for name in ("thread.py", "invite.py"):
            source = (CURRENT_DIR / name).read_text()
            for forbidden in (
                "from authorization", "import authorization",
                "from guest_session", "import guest_session",
                "from mutations", "import mutations",
                "http_boundary", "from application", "import application",
                "collaboration.application", "resolve_internal_collaboration_context",
                "collab:v2",
            ):
                self.assertNotIn(forbidden, source)

    def test_http_boundary_import_is_canonical_inactive_and_io_free(self):
        script = textwrap.dedent(
            f"""
            import builtins
            import importlib
            import json
            import os
            import re
            import sys
            from unittest.mock import patch

            {DEPLOYMENT_PATH_ASSERTIONS}
            forbidden_modules = (
                "api.collaboration.redis_store",
                "api.user_config_store",
                "api.inboxes.authenticated_gmail",
                "api.inboxes.authenticated_imap",
                "api.inboxes.oauth_token_store",
                "imaplib",
                "smtplib",
            )
            assert all(name not in sys.modules for name in forbidden_modules)
            with patch("os.getenv", side_effect=AssertionError("environment read")), patch.object(
                os._Environ, "__getitem__", side_effect=AssertionError("environment read")
            ), patch(
                "builtins.open", side_effect=AssertionError("file I/O during import")
            ), patch(
                "urllib.request.urlopen", side_effect=AssertionError("network during import")
            ), patch(
                "socket.create_connection", side_effect=AssertionError("socket during import")
            ):
                boundary = importlib.import_module("api.collaboration.http_boundary")
            assert boundary.__name__ == "api.collaboration.http_boundary"
            assert all(name.lower() != "handler" for name in vars(boundary))
            assert all(name not in sys.modules for name in forbidden_modules)
            assert {{
                name for name in sys.modules
                if name.startswith("api.")
            }} <= {{"api.collaboration", "api.collaboration.http_boundary"}}
            """
        )
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=FRONTEND_ROOT,
            env=_deployment_env(),
            text=True,
            capture_output=True,
            timeout=15,
            check=False,
        )
        self.assertEqual(
            result.returncode,
            0,
            msg=f"stdout={result.stdout!r} stderr={result.stderr!r}",
        )

        source = (CURRENT_DIR / "http_boundary.py").read_text()
        self.assertNotIn("BaseHTTPRequestHandler", source)
        self.assertNotIn("os.environ", source)
        self.assertNotIn("os.getenv", source)

    def test_application_import_is_canonical_inactive_and_io_free(self):
        script = textwrap.dedent(
            f"""
            import builtins
            import importlib
            import imaplib
            import io
            import os
            import socket
            import smtplib
            import subprocess
            import sys
            import urllib.request
            from unittest.mock import patch

            {DEPLOYMENT_PATH_ASSERTIONS}
            foundations = (
                "api.collaboration.models",
                "api.collaboration.redis_store",
                "api.collaboration.authorization",
                "api.collaboration.guest_session",
            )
            forbidden_modules = (
                "api.collaboration.thread",
                "api.collaboration.invite",
                "api.collaboration.source_message",
                "api.collaboration.mutations",
                "api.collaboration.http_boundary",
                "api.inboxes.connect-imap",
                "api.inboxes.connect-oauth",
                "api.inboxes.credentials",
                "api.inboxes.download-attachment",
                "api.inboxes.fetch-gmail-thread",
                "api.inboxes.fetch-gmail",
                "api.inboxes.message-action",
                "api.inboxes.oauth-callback",
                "api.inboxes.send-gmail",
                "api.user_config_store",
                "api.inboxes.authenticated_gmail",
                "api.inboxes.authenticated_imap",
                "api.inboxes.oauth_token_store",
            )
            assert all(name not in sys.modules for name in foundations)
            assert "api.collaboration.application" not in sys.modules
            assert "application" not in sys.modules
            redis_calls = []

            def profile_redis_calls(frame, event, _argument):
                if (
                    event == "call"
                    and frame.f_globals.get("__name__") == "api.collaboration.redis_store"
                    and frame.f_code.co_name in {{
                        "_perform_v2_rest_command",
                        "_v2_command",
                        "_v2_eval",
                        "_v2_read_json",
                    }}
                ):
                    redis_calls.append(frame.f_code.co_name)

            with patch("os.getenv", side_effect=AssertionError("environment read")), patch.object(
                os._Environ, "__getitem__", side_effect=AssertionError("environment read")
            ), patch(
                "builtins.open", side_effect=AssertionError("file I/O during import")
            ), patch(
                "io.open", side_effect=AssertionError("file I/O during import")
            ), patch(
                "os.open", side_effect=AssertionError("file I/O during import")
            ), patch(
                "urllib.request.urlopen", side_effect=AssertionError("network during import")
            ), patch(
                "socket.create_connection", side_effect=AssertionError("socket during import")
            ), patch(
                "imaplib.IMAP4", side_effect=AssertionError("IMAP during import")
            ), patch(
                "imaplib.IMAP4_SSL", side_effect=AssertionError("IMAPS during import")
            ), patch(
                "smtplib.SMTP", side_effect=AssertionError("SMTP during import")
            ), patch(
                "smtplib.SMTP_SSL", side_effect=AssertionError("SMTPS during import")
            ), patch(
                "subprocess.Popen", side_effect=AssertionError("process startup during import")
            ):
                sys.setprofile(profile_redis_calls)
                try:
                    application = importlib.import_module("api.collaboration.application")
                finally:
                    sys.setprofile(None)
            assert application.__name__ == "api.collaboration.application"
            assert all(name.lower() != "handler" for name in vars(application))
            assert "application" not in sys.modules
            assert all(name not in sys.modules for name in forbidden_modules)
            assert all(name in sys.modules for name in foundations)
            assert redis_calls == []
            """
        )
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=FRONTEND_ROOT,
            env=_deployment_env(),
            text=True,
            capture_output=True,
            timeout=15,
            check=False,
        )
        self.assertEqual(
            result.returncode,
            0,
            msg=f"stdout={result.stdout!r} stderr={result.stderr!r}",
        )

        source = (CURRENT_DIR / "application.py").read_text()
        for forbidden in (
            "BaseHTTPRequestHandler",
            "def handler",
            "class handler",
            "os.environ",
            "os.getenv",
        ):
            self.assertNotIn(forbidden, source)

    def test_active_inbox_routes_and_frontend_do_not_reference_inactive_application_modules(self):
        inbox_dir = FRONTEND_ROOT / "api" / "inboxes"
        for path in inbox_dir.glob("*.py"):
            if not path.name.startswith("test_"):
                source = path.read_text()
                self.assertNotIn("http_boundary", source, msg=str(path))
                for forbidden in (
                    "api.collaboration.application",
                    "collaboration/application",
                    "from application",
                    "import application",
                ):
                    self.assertNotIn(forbidden, source, msg=str(path))

        frontend_src = FRONTEND_ROOT / "src"
        for path in frontend_src.rglob("*"):
            if path.is_file() and path.suffix in {
                ".css", ".html", ".js", ".jsx", ".json", ".ts", ".tsx",
            }:
                source = path.read_text()
                self.assertNotIn("http_boundary", source, msg=str(path))
                for forbidden in (
                    "api.collaboration.application",
                    "collaboration/application",
                    "application.py",
                ):
                    self.assertNotIn(forbidden, source, msg=str(path))


if __name__ == "__main__":
    unittest.main()
