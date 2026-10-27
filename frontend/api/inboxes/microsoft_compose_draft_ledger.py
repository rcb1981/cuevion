"""Permanent, segmented Compose CAS history and indexed active barriers."""
import copy
import hmac
import json
import secrets
from api.inboxes import microsoft_compose_segments as segments
from api.inboxes import microsoft_draft_attachment_ledger as base
from api.inboxes.microsoft_draft_attachment_snapshot import digest, authority_digest
from api.inboxes.microsoft_token_authority import _canonical_authority
from api.inboxes.microsoft_graph import MicrosoftGraphError

PREFIX = 'cuevion:microsoft:compose-draft:v1:'
LEGACY_MAX_OPERATIONS = 32
MAX_SEQUENCE = 9007199254740991
PAGE_SIZE = 32
MAX_BYTES = 64 * 1024
PHASES = {'reserved', 'started', 'confirmed_performed', 'confirmed_not_performed'}


class ComposeLedgerError(MicrosoftGraphError):
    pass


def fail(code):
    raise ComposeLedgerError('microsoft_compose_' + code)


def context(authority):
    a = _canonical_authority(authority)
    config = base.store._resolve_durable_store_config()
    if type(config) is not dict or not config:
        fail('store_unavailable')
    return config, PREFIX + digest([a.owner_email, a.mailbox_id]), authority_digest(a, generation=False)


def validate_item(item):
    if (type(item) is not dict or set(item) != {'action','draftId','binding','authority','claim','phase'}
            or item['action'] not in {'create','update'} or item['phase'] not in PHASES
            or any(type(item[k]) is not str or not base.HASH.fullmatch(item[k]) for k in ('binding','authority','claim'))
            or item['draftId'] is None and (item['action'] != 'create' or item['phase'] == 'confirmed_performed')):
        fail('store_invalid')
    if item['draftId'] is not None:base._required_message_id(item['draftId'])


def legacy(value, scope):
    if (type(value) is not dict or set(value) != {'v','scope','operations'} or type(value['v']) is not int
            or value['v'] != 1 or value['scope'] != scope or type(value['operations']) is not dict
            or len(value['operations']) > LEGACY_MAX_OPERATIONS):fail('store_invalid')
    for identifier,item in value['operations'].items():
        base._id(identifier);validate_item(item)


def read(authority):
    config,key,scope=context(authority)
    value,raw=segments._snapshot(config,key)
    if value is None:
        if segments._snapshot(config,key+':initialized')[0] is not None:fail('store_unavailable')
        return {'v':1,'scope':scope,'operations':{}},None
    if value.get('v')==1:
        legacy(value,scope)
    else:
        if (set(value) != {'v','scope','operations','order','barriers','count','legacy'} or type(value['v']) is not int
                or value['v']!=2 or value['scope']!=scope or type(value['count']) is not int
                or not 1<=value['count']<=MAX_SEQUENCE or any(not segments._hash(value[k]) for k in ('operations','order','barriers'))
                or value['legacy'] is not None and not segments._hash(value['legacy'])):fail('store_invalid')
        if value['legacy'] is not None:
            archived=segments.segment(authority,value['legacy'])
            if set(archived)!={'v','scope','kind','original'} or archived['v']!=2 or archived['kind']!='legacy':fail('store_invalid')
            legacy(archived['original'],scope)
        if segments._snapshot(config,key+':initialized')[0]!={'v':2,'scope':scope}:fail('store_unavailable')
    return value,raw


def write(authority, raw, root):
    config,key,scope=context(authority)
    marker={'v':2,'scope':scope}
    previous,marker_raw=segments._snapshot(config,key+':initialized')
    if previous is not None and previous!=marker:fail('store_unavailable')
    if previous is None:
        base.store._write_google_durable_record_if_unchanged(config,key+':initialized',marker_raw,marker)
        if segments._snapshot(config,key+':initialized')[0]!=marker:fail('store_unavailable')
    try:
        stored,error=base.store._write_google_durable_record_if_unchanged(config,key,raw,root)
        # A lost mutable root ACK never grants a mutation claim.
        if error or stored!=root or read(authority)[0]!=root:fail('store_unavailable')
    except ComposeLedgerError:raise
    except Exception:fail('store_unavailable')


def receipt(identifier, item):
    uncertain=item['phase'] in {'reserved','started'}
    return {'requestId':identifier,'action':item['action'],'providerMessageId':item['draftId'],
            'outcome':'uncertain' if uncertain else item['phase'],'blocked':uncertain}


def entry(authority, identifier, root=None):
    base._id(identifier);root=root if root is not None else read(authority)[0]
    if root['v']==1:
        item=root['operations'].get(identifier)
        return None if item is None else {'id':identifier,'sequence':list(root['operations']).index(identifier)+1,'item':item}
    value=segments.get(authority,root['operations'],'operations',identifier)
    if value is None:return None
    if (type(value) is not dict or set(value)!={'id','sequence','item'} or value['id']!=identifier
            or type(value['sequence']) is not int or not 1<=value['sequence']<=root['count']):fail('store_invalid')
    validate_item(value['item']);return value


def operation(authority, identifier):
    found=entry(authority,identifier)
    if found is None:fail('operation_invalid')
    return receipt(identifier,found['item'])


def lookup(authority, identifier, binding):
    found=entry(authority,identifier)
    if found is None:return None
    if found['item']['binding']!=binding:fail('operation_conflict')
    return receipt(identifier,found['item'])


def barrier_keys(item):
    return ([['create']] if item['action']=='create' else []) + ([['draft',item['draftId']]] if item['draftId'] is not None else [])


def blockers(authority, root, key):
    if root['v']==1:
        ids=[k for k,v in root['operations'].items() if key in barrier_keys(v) and v['phase'] in {'reserved','started'}]
    else:
        ids=segments.get(authority,root['barriers'],'barriers',key)
        if ids is None:return []
        # A legacy key can have at most 32 blockers. New reservations require zero.
        if type(ids) is not list or len(ids)>LEGACY_MAX_OPERATIONS or len(ids)!=len(set(ids)):fail('store_invalid')
    for identifier in ids:
        found=entry(authority,identifier,root)
        if found is None or key not in barrier_keys(found['item']) or found['item']['phase'] not in {'reserved','started'}:fail('store_invalid')
    return ids


def assert_unblocked(authority, draft_id):
    root,_=read(authority)
    if blockers(authority,root,['create'] if draft_id is None else ['draft',draft_id]):fail('operation_blocked')


def upgrade(authority, root):
    if root['v']==2:return copy.deepcopy(root)
    value={'v':2,'scope':root['scope'],'operations':None,'order':None,'barriers':None,'count':0,'legacy':None}
    if root['operations']:
        value['legacy']=segments.store_segment(authority,{'v':2,'scope':root['scope'],'kind':'legacy','original':root})
    pending={}
    for identifier,item in root['operations'].items():
        value['count']+=1
        e={'id':identifier,'sequence':value['count'],'item':item}
        value['operations']=segments.put(authority,value['operations'],'operations',identifier,e)
        value['order']=segments.put(authority,value['order'],'order',value['count'],identifier)
        if item['phase'] in {'reserved','started'}:
            for key in barrier_keys(item):pending.setdefault(tuple(key),[]).append(identifier)
    for key,ids in pending.items():value['barriers']=segments.put(authority,value['barriers'],'barriers',list(key),ids)
    return value


def reserve(authority, identifier, action, draft_id, binding):
    base._id(identifier)
    if action not in {'create','update'} or action=='create' and draft_id is not None:fail('operation_invalid')
    if action=='update':base._required_message_id(draft_id)
    root,raw=read(authority);prior=entry(authority,identifier,root)
    if prior is not None:
        if prior['item']['binding']!=binding:fail('operation_conflict')
        return {**receipt(identifier,prior['item']),'new':False}
    if blockers(authority,root,['create'] if draft_id is None else ['draft',draft_id]):fail('operation_blocked')
    value=upgrade(authority,root)
    if value['count']>=MAX_SEQUENCE:fail('store_unavailable')
    claim=secrets.token_urlsafe(32)
    item={'action':action,'draftId':draft_id,'binding':binding,'authority':authority_digest(authority),'claim':digest(claim),'phase':'reserved'}
    validate_item(item);value['count']+=1
    e={'id':identifier,'sequence':value['count'],'item':item}
    value['operations']=segments.put(authority,value['operations'],'operations',identifier,e)
    value['order']=segments.put(authority,value['order'],'order',e['sequence'],identifier)
    for key in barrier_keys(item):value['barriers']=segments.put(authority,value['barriers'],'barriers',key,[identifier])
    write(authority,raw,value)
    return {**receipt(identifier,item),'new':True,'claim':claim}


def advance(authority, identifier, claim, phase, *, draft_id=None):
    root,raw=read(authority);found=entry(authority,identifier,root)
    item=copy.deepcopy(found['item']) if found else None
    if (item is None or item['authority']!=authority_digest(authority) or type(claim) is not str
            or not hmac.compare_digest(item['claim'],digest(claim)) or item['phase'] not in {'reserved','started'}
            or phase not in PHASES or phase=='reserved' or item['phase']=='started' and phase=='started' and draft_id is None):fail('operation_conflict')
    old=copy.deepcopy(item)
    if draft_id is not None:
        base._required_message_id(draft_id)
        if item['draftId'] is not None and item['draftId']!=draft_id:fail('operation_conflict')
        item['draftId']=draft_id
    item['phase']=phase;validate_item(item)
    value=upgrade(authority,root)
    # Remove/add only this claim; preserve all other legacy blockers.
    keys=barrier_keys(old)+[k for k in barrier_keys(item) if k not in barrier_keys(old)]
    for key in keys:
        ids=[x for x in blockers(authority,root,key) if x!=identifier]
        if phase in {'reserved','started'} and key in barrier_keys(item):ids.append(identifier)
        value['barriers']=segments.put(authority,value['barriers'],'barriers',key,ids)
    value['operations']=segments.put(authority,value['operations'],'operations',identifier,{**found,'item':item})
    write(authority,raw,value)
    return receipt(identifier,item)


def page(authority, before=None, *, draft_id=None, request_id=None):
    root,_=read(authority);count=len(root['operations']) if root['v']==1 else root['count']
    if before is not None and (type(before) is not int or not 1<=before<=count+1):fail('operation_invalid')
    end=count if before is None else before-1;start=max(1,end-PAGE_SIZE+1)
    ids=[]
    for seq in range(start,end+1):
        identifier=list(root['operations'])[seq-1] if root['v']==1 else segments.get(authority,root['order'],'order',seq)
        found=entry(authority,identifier,root)
        if found is None or found['sequence']!=seq:fail('store_invalid')
        ids.append(identifier)
    relevant=blockers(authority,root,['create'])
    if draft_id is not None:relevant+=blockers(authority,root,['draft',draft_id])
    if request_id is not None and entry(authority,request_id,root) is not None:relevant.append(request_id)
    values=[entry(authority,k,root) for k in dict.fromkeys(ids+relevant)]
    values.sort(key=lambda x:x['sequence'])
    return {'operations':[receipt(v['id'],v['item']) for v in values],'nextCursor':start if start>1 else None}


def inspect(authority):
    # Compatibility for offline/audit callers. Runtime barriers and recovery use
    # indexed lookup and bounded pages, never this whole-history aggregation.
    values={};before=None
    while True:
        result=page(authority,before)
        for item in result['operations']:values[item['requestId']]=item
        before=result['nextCursor']
        if before is None:return list(values.values())
