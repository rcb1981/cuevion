"""Small inert HTML fragment contract for Compose, never Send/MIME proof.

No CSS, images, CID, active URLs, embedded content or arbitrary attributes.
Only explicit empty document wrappers and b/i aliases are canonicalized.
Unknown HTML is rejected, never silently flattened or partially discarded.
"""
import html
import re
from html.parser import HTMLParser
from api.inboxes.microsoft_graph import MicrosoftGraphError
from api.inboxes import microsoft_compose_draft_ledger as ledger
from api.inboxes.microsoft_draft_attachment_snapshot import digest

TAGS={'div','p','br','strong','em','u','s','ul','ol','li','blockquote','pre','a'}
ALIASES={'b':'strong','i':'em','strike':'s'}
PREFIX='cuevion:microsoft:compose-html:v1:'


def fail():
    raise MicrosoftGraphError('microsoft_compose_html_unsupported')


def safe_link(value):
    return (type(value) is str and len(value)<=2048 and
        re.fullmatch(r'(?:https?://|mailto:)[^\s<>"\x00-\x20\x7f]+',value,re.I) is not None)


def canonical(value):
    if type(value) is not str or len(value)>32000 or len(value.encode())>128000 or '\x00' in value:fail()
    class Fragment(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True);self.stack=[];self.out=[]
        def handle_starttag(self,tag,attrs):
            tag=ALIASES.get(tag,tag)
            if tag not in TAGS or len(self.stack)>=64:fail()
            attributes=''
            if attrs:
                if tag!='a' or len(attrs)!=1 or attrs[0][0]!='href' or not safe_link(attrs[0][1]):fail()
                attributes=' href="'+html.escape(attrs[0][1],quote=True)+'"'
            self.out.append('<'+tag+attributes+'>')
            if tag!='br':self.stack.append(tag)
        def handle_endtag(self,tag):
            tag=ALIASES.get(tag,tag)
            if not self.stack or self.stack.pop()!=tag:fail()
            self.out.append('</'+tag+'>')
        def handle_startendtag(self,tag,attrs):
            if tag!='br':fail()
            self.handle_starttag(tag,attrs)
        def handle_data(self,data):self.out.append(html.escape(data,quote=False))
        def handle_comment(self,data):fail()
        def handle_decl(self,data):fail()
        def handle_pi(self,data):fail()
        def unknown_decl(self,data):fail()
    try:
        parser=Fragment();parser.feed(value);parser.close()
        if parser.stack:fail()
        result=''.join(parser.out)
        if len(result)>32000:fail()
        return result
    except MicrosoftGraphError:raise
    except Exception:fail()


def provider_fragment(value):
    # Wrapper removal is exact, with no attributes, style or metadata discarded.
    if type(value) is not str:fail()
    envelope = re.fullmatch(r'<html>(?:<head><meta http-equiv="Content-Type" content="text/html; charset=utf-8"></head>)?<body>([\s\S]*)</body></html>', value, re.I)
    if envelope:value=envelope.group(1)
    return canonical(value)


def managed(authority, identifier, *, register=False):
    config,_,scope=ledger.context(authority)
    key=PREFIX+digest([scope,identifier]);expected={'v':1,'scope':scope}
    try:
        value,raw=ledger.segments._snapshot(config,key)
        if value is None and register:
            stored,error=ledger.base.store._write_google_durable_record_if_unchanged(config,key,raw,expected)
            if error or stored!=expected:ledger.fail('store_unavailable')
            value,_=ledger.segments._snapshot(config,key)
        if value is not None and value!=expected:ledger.fail('store_invalid')
        return value==expected
    except MicrosoftGraphError:raise
    except Exception:ledger.fail('store_unavailable')
