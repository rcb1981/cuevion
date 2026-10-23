"""No-network pagination, identity and PNG size-proof integration regressions."""
import base64
import unittest
from urllib.parse import quote
from unittest.mock import patch

from api.inboxes import microsoft_attachments_graph as meta
from api.inboxes import microsoft_attachment_download as download
from api.inboxes import microsoft_http as http, microsoft_mailbox_access as owned
from api.inboxes.microsoft_graph import MicrosoftGraphError, GRAPH_ROOT
from api.inboxes.test_microsoft_graph import FakeResponse, TOKEN
from api.inboxes.test_microsoft_attachments import file_row, MSG, MAILBOX
from api.inboxes.test_microsoft_http import _request, _body
from api.inboxes.test_microsoft_attachment_download import complete_png_fixture, ThreeStepGraphOpener, payload, ATTACHMENT, MESSAGE

COLLECTION = "/me/mailFolders/inbox/messages/" + quote(MSG, safe="") + "/attachments"

def link(skip):
    return GRAPH_ROOT + COLLECTION + "?$skip=" + str(skip)

class Pages:
    def __init__(self, pages):
        self.pages, self.calls = pages, []
    def __call__(self, request, *, timeout):
        self.calls.append(request)
        if len(self.calls) > len(self.pages):
            raise AssertionError("Unbounded Graph request")
        value = self.pages[len(self.calls)-1]
        if isinstance(value, Exception):
            raise value
        return FakeResponse(value)

class AttachmentPaginationTests(unittest.TestCase):
    def test_second_page_returns_more_than_25_without_exposing_links(self):
        pages = Pages([
            {"value": [file_row(id="file-"+str(n)) for n in range(25)], "@odata.nextLink": link(25)},
            {"value": [file_row(id="file-25")]},
        ])
        result = meta.list_microsoft_attachments(TOKEN, MSG, folder="Inbox", opener=pages)
        self.assertEqual(len(result["attachments"]), 26)
        self.assertFalse(result["hasMore"])
        self.assertEqual(len(pages.calls), 2)
        self.assertNotIn("nextLink", str(result))
        for call in pages.calls:
            self.assertIn(COLLECTION, call.full_url)
            self.assertEqual(call.get_method(), "GET")
            self.assertEqual(call.get_header("Prefer"), 'IdType="ImmutableId"')

    def test_hard_cap_four_pages_100_rows_and_no_fifth_request(self):
        pages = Pages([{"value": [file_row(id="file-"+str(n)) for n in range(i*25,(i+1)*25)],
                        "@odata.nextLink": link((i+1)*25)} for i in range(4)])
        result = meta.list_microsoft_attachments(TOKEN, MSG, folder="Inbox", opener=pages)
        self.assertEqual(len(result["attachments"]), 100)
        self.assertTrue(result["hasMore"])
        self.assertEqual(len(pages.calls), 4)

    def test_foreign_mailbox_folder_message_and_query_never_followed(self):
        links = [link(25).replace("graph.microsoft.com", "evil.example"),
                 link(25).replace("/me/", "/users/other/"),
                 link(25).replace("/inbox/", "/sentitems/"),
                 link(25).replace(quote(MSG,safe=""), "other-message"),
                 link(25)+"&$expand=attachments", link(25)+"&$skip=50",
                 link(25)+"#$skip=50", link(25).replace("https:", "http:"),
                 GRAPH_ROOT+COLLECTION+"?$skiptoken=bad%00token",
                 GRAPH_ROOT+COLLECTION+"?$skip=25&$top=100",
                 GRAPH_ROOT+COLLECTION+"?$skip=25&$select=contentBytes"]
        for candidate in links:
            with self.subTest(candidate=candidate):
                pages=Pages([{"value":[file_row()],"@odata.nextLink":candidate}])
                with self.assertRaises(MicrosoftGraphError):
                    meta.list_microsoft_attachments(TOKEN,MSG,folder="Inbox",opener=pages)
                self.assertEqual(len(pages.calls),1)

    def test_incomplete_duplicate_empty_and_failed_later_pages_fail_closed(self):
        for tail in ({}, {"value":[file_row()]}, {"value":None},
                     {"value":[],"@odata.nextLink":link(50)},
                     {"value":[file_row(id="file-2",size=True)]},
                     MicrosoftGraphError("microsoft_graph_unavailable")):
            with self.subTest(tail=type(tail).__name__):
                pages=Pages([{"value":[file_row()],"@odata.nextLink":link(25)},tail])
                with self.assertRaises(MicrosoftGraphError):
                    meta.list_microsoft_attachments(TOKEN,MSG,folder="Inbox",opener=pages)
                self.assertEqual(len(pages.calls),2)

    def test_cycle_is_rejected_without_third_request(self):
        pages=Pages([{"value":[file_row()],"@odata.nextLink":link(25)},
                     {"value":[file_row(id="file-2")],"@odata.nextLink":link(25)}])
        with self.assertRaises(MicrosoftGraphError):
            meta.list_microsoft_attachments(TOKEN,MSG,folder="Inbox",opener=pages)
        self.assertEqual(len(pages.calls),2)

    def test_mailbox_recheck_discards_inventory_after_identity_change(self):
        with patch.object(owned,"_owned_token",return_value=(TOKEN,object())), patch.object(
            meta,"list_microsoft_attachments",return_value={"attachments":[],"hasMore":False}), patch.object(
            owned,"_verify_unchanged_owned_mailbox",side_effect=owned.MicrosoftMailboxAccessError("microsoft_reconnect_required")):
            result=http.microsoft_inbox_response(_request({"mailboxId":MAILBOX,"providerMessageId":MSG,"folder":"Inbox"}),mode="attachments")
        self.assertEqual(result.status,409)
        self.assertNotIn("attachments",_body(result))

class PngSizeProofTests(unittest.TestCase):
    def test_png_proof_survives_real_service_owner_and_http_projection(self):
        image=complete_png_fixture()
        opener=ThreeStepGraphOpener(payload(contentBytes=None),{"value":[{
            "id":ATTACHMENT,"@odata.type":"#microsoft.graph.fileAttachment","name":"logo.png",
            "size":len(image)+31,"contentType":"image/png","isInline":False}]},image)
        original=download.read_microsoft_file_attachment
        with patch.object(owned,"_owned_token",return_value=(TOKEN,object())), patch.object(
            owned,"_verify_unchanged_owned_mailbox"), patch.object(download,"read_microsoft_file_attachment",
            side_effect=lambda token,msg,att,**kwargs: original(token,msg,att,opener=opener,**kwargs)):
            result=http.microsoft_inbox_response(_request({"mailboxId":MAILBOX,"providerMessageId":MESSAGE,
                "attachmentId":ATTACHMENT,"folder":"Inbox"}),mode="download")
        self.assertEqual(result.status,200)
        body=_body(result)
        self.assertIs(body["sizeVerifiedAsPng"],True)
        self.assertEqual(body["size"],len(image))
        self.assertEqual(base64.b64decode(body["base64"]),image)
        self.assertTrue(all(r.get_method()=="GET" for r,_ in opener.calls))

    def test_http_rejects_forged_proof_nonpng_crc_damage_truncation_and_trailing_html(self):
        image=complete_png_fixture()
        corrupt=bytearray(image);corrupt[35]^=1
        for data in (b"<svg onload='x'/>",bytes(corrupt),image[:-3],image+b"<script>x</script>"):
            with self.subTest(length=len(data)),patch.object(owned,"download_owned_microsoft_attachment",return_value={
                "attachmentId":ATTACHMENT,"fileName":"file.png","size":len(data),
                "base64":base64.b64encode(data).decode(),"sizeVerifiedAsPng":True}):
                result=http.microsoft_inbox_response(_request({"mailboxId":MAILBOX,"providerMessageId":MESSAGE,
                    "attachmentId":ATTACHMENT,"folder":"Inbox"}),mode="download")
                self.assertEqual(result.status,502)
                self.assertNotIn("base64",_body(result))
