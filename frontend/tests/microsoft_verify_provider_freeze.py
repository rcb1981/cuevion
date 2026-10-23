#!/usr/bin/env python3
"""Fail-closed baseline guard: no Gmail, IMAP, or shared provider changes.

Usage: python verify_provider_freeze.py PATH_TO_LOCAL_CUEVION_CHECKOUT
This is an integrity gate, NOT an end-to-end functional proof.
"""
from __future__ import annotations
import hashlib,json,pathlib,sys

HERE=pathlib.Path(__file__).resolve().parent
MANIFEST=json.loads((HERE/'microsoft_reference_manifest.json').read_text(encoding='utf-8'))

def sha(b:bytes)->str:return hashlib.sha256(b).hexdigest()

# Audited exceptions already present in the owner's previous approved Microsoft
# staging ZIP (cuevion_ms_draft_editor_integrated_2026-10-22.zip).
# These are exact and pinned: never recalculate their digests from the new code.
_PREVIOUS_MS_ERROR_START = b'  microsoft_pilot_read_only: "Microsoft Drafts editing is not enabled yet.",\n'
_PREVIOUS_MS_ERROR_END = b'  microsoft_draft_edit_unconfirmed: "Save status is uncertain. Reopen Drafts before saving again.",\n'
_PREVIOUS_MS_ERROR_SHA = '099a6574e428e2eb944ad82d5e77fc0cca028bafecbe962567791a27acf1f784'
_PREVIOUS_MS_EDITOR_START = b'/** Microsoft-only conditional edit of an existing plain-text Drafts message.\n'
_PREVIOUS_MS_EDITOR_END = b'/** Bounded, owner-authorized Drafts listing; never modify or send draft content. */'
_PREVIOUS_MS_EDITOR_SHA = '7a600c5a73d2640451adbe8ef4fc808494a10c172a58d5dabe422130463f09a7'


def _remove_prior_approved_microsoft_additions(data: bytes, *, prefix: bool) -> bytes | None:
    """Strip only exact, independently approved MS-only insertions; never any arbitrary diff."""
    start, end, want = (
        (_PREVIOUS_MS_ERROR_START, _PREVIOUS_MS_ERROR_END, _PREVIOUS_MS_ERROR_SHA)
        if prefix else
        (_PREVIOUS_MS_EDITOR_START, _PREVIOUS_MS_EDITOR_END, _PREVIOUS_MS_EDITOR_SHA)
    )
    if data.count(start) != 1 or data.count(end) != 1:
        return None
    first = data.index(start)
    last = data.index(end) + (len(end) if prefix else 0)
    if first >= last or sha(data[first:last]) != want:
        return None
    # Editor addition must appear directly before the known Microsoft Drafts-list
    # boundary. No hidden extra content may be inserted into the exception span.
    return data[:first] + data[last:]

def is_ms(path:str)->bool:
    p=pathlib.PurePosixPath(path)
    return 'microsoft' in p.name.lower() or any('microsoft' in part.lower() for part in p.parts[:-1])

def is_tracked(path:str)->bool:
    p=pathlib.PurePosixPath(path)
    return bool(p.parts) and p.parts[0]=='frontend' and p.suffix in MANIFEST['tracked_suffixes'] and not any(part in MANIFEST['excluded_directory_names'] for part in p.parts)

def check(root:pathlib.Path)->list[str]:
    failures=[]
    protected=MANIFEST['protected_sha256']
    for path,want in protected.items():
        file=root/path
        if not file.is_file():failures.append('MISSING PROTECTED: '+path)
        elif sha(file.read_bytes())!=want:failures.append('MODIFIED PROTECTED: '+path)
    path=MANIFEST['shared_file'];file=root/path
    if not file.is_file():failures.append('MISSING SHARED FILE: '+path)
    else:
        data=file.read_bytes()
        start=MANIFEST['shared_region_start'].encode();end=MANIFEST['shared_region_end'].encode()
        if data.count(start)!=1 or data.count(end)!=1 or data.index(start)>=data.index(end):
            failures.append('MISSING/AMBIGUOUS MICROSOFT SHARED BOUNDARIES: '+path)
        else:
            original_prefix = _remove_prior_approved_microsoft_additions(
                data[:data.index(start)], prefix=True)
            original_suffix = _remove_prior_approved_microsoft_additions(
                data[data.index(end):], prefix=False)
            if original_prefix is None or sha(original_prefix)!=MANIFEST['shared_prefix_sha256']:
                failures.append('MODIFIED SHARED FILE BEFORE MS BOUNDARY: '+path)
            if original_suffix is None or sha(original_suffix)!=MANIFEST['shared_suffix_sha256']:
                failures.append('MODIFIED SHARED FILE AFTER MS BOUNDARY: '+path)
    baseline=set(MANIFEST['baseline_paths'])
    for file in (root/'frontend').rglob('*'):
        if not file.is_file():continue
        path=file.relative_to(root).as_posix()
        if is_tracked(path) and path not in baseline and not is_ms(path):
            failures.append('UNREVIEWED NEW NON-MS SOURCE: '+path)
    return sorted(failures)

if __name__=='__main__':
    if len(sys.argv)!=2:
        raise SystemExit('Usage: python verify_provider_freeze.py /path/to/cuevion')
    root=pathlib.Path(sys.argv[1]).resolve()
    if not (root/'frontend'/'package.json').is_file():
        raise SystemExit('ERROR: Cuevion repository not found at '+str(root))
    failures=check(root)
    if failures:
        print('FAIL — reference Gmail/IMAP source is no longer frozen:')
        for failure in failures: print(' - '+failure)
        raise SystemExit(1)
    print('PASS — '+str(len(MANIFEST['protected_sha256']))+' protected source files unchanged,')
    print('        shared API preserved outside Microsoft-only request-validation region,')
    print('        no unreviewed non-Microsoft source files added.')
