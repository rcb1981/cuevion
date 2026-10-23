/**
 * Cuevion Microsoft mail request preparation for the real outbound pipeline.
 * Pure code: no draft creation, network requests, browser storage or sending.
 * Production send permission remains under the server's source-only gate.
 */
export type MicrosoftComposeMode = 'new' | 'reply' | 'reply_all' | 'forward';
export type MicrosoftSelectedSource = Readonly<{
  serverMailboxId: string;
  providerMessageId: string;
  providerFolder: string;
}>;
export type MicrosoftComposeInput = Readonly<{
  mode: MicrosoftComposeMode;
  mailboxId: string;
  body: string;
  to?: string;
  cc?: string;
  subject?: string;
  selectedProviderMessageId?: string | null;
  source?: MicrosoftSelectedSource | null;
  sourceIsExact?: boolean;
  busy?: boolean;
  connected?: boolean;
}>;

export type MicrosoftWireCompose =
  | Readonly<{mode: 'new'; body:string; to:string[]; cc:string[]; subject:string}>
  | Readonly<{mode:'reply'|'reply_all'; sourceProviderMessageId:string; body:string}>
  | Readonly<{mode:'forward'; sourceProviderMessageId:string; to:string[]; cc:string[]; body:string}>;

export type MicrosoftComposeValidation =
  | Readonly<{ok:true; mailboxId:string; compose:MicrosoftWireCompose}>
  | Readonly<{ok:false; code:'disconnected'|'busy'|'invalid_mailbox'|'invalid_body'|
      'unverified_source'|'wrong_mailbox'|'wrong_folder'|'invalid_source'|
      'invalid_recipients'|'invalid_subject'|'unsupported_mode'}>;

const VALID_MAILBOX = /^[A-Za-z0-9][A-Za-z0-9:_-]{0,159}$/;
const VALID_MESSAGE = /^[^\x00-\x20\x7f-\x9f?#]{1,512}$/;
const VALID_EMAIL = /^[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+$/;
const encoder = new TextEncoder();

function validMessageId(value: unknown): value is string {
  return typeof value === 'string' && VALID_MESSAGE.test(value) &&
    value !== '.' && !value.includes('..') && encoder.encode(value).byteLength <= 512;
}

/** The wire protocol expects normalized, unique recipients, never guessed recipients. */
export function normalizeMicrosoftRecipientList(value: unknown): string[] | null {
  if (typeof value !== 'string') return null;
  if (!value.trim()) return [];
  const entries = value.split(',').map(v => v.trim().toLowerCase());
  if (entries.length > 20 || entries.some(v => !VALID_EMAIL.test(v))) return null;
  if (new Set(entries).size !== entries.length) return null;
  return entries;
}

export function prepareMicrosoftComposeRequest(input: MicrosoftComposeInput): MicrosoftComposeValidation {
  if (!input || !['new','reply','reply_all','forward'].includes(input.mode))
    return {ok:false,code:'unsupported_mode'};
  if (!input.connected) return {ok:false,code:'disconnected'};
  if (input.busy) return {ok:false,code:'busy'};
  if (!VALID_MAILBOX.test(input.mailboxId)) return {ok:false,code:'invalid_mailbox'};
  if (typeof input.body !== 'string' || !input.body.trim() || input.body.includes('\0') ||
      input.body.length > 64_000 || encoder.encode(input.body).byteLength > 256_000)
    return {ok:false,code:'invalid_body'};

  if (input.mode === 'new') {
    const to=normalizeMicrosoftRecipientList(input.to);
    const cc=normalizeMicrosoftRecipientList(input.cc ?? '');
    if (!to?.length || !cc || to.length + cc.length > 20 ||
        new Set([...to,...cc]).size !== to.length + cc.length)
      return {ok:false,code:'invalid_recipients'};
    const subject=input.subject;
    if (typeof subject !== 'string' || !subject.trim() || subject.length > 250 ||
        /[\x00-\x1f\x7f]/.test(subject)) return {ok:false,code:'invalid_subject'};
    return {ok:true,mailboxId:input.mailboxId,
      compose:{mode:'new',to,cc,subject:subject.trim(),body:input.body}};
  }

  // Only the exact confirmed real Inbox source is valid. In particular do not
  // permit an old list row, a stale selected message, virtual Filtered, Sent,
  // Archive, Deleted Items, or a source belonging to another mailbox.
  if (!input.sourceIsExact || !input.source) return {ok:false,code:'unverified_source'};
  if (input.source.serverMailboxId !== input.mailboxId) return {ok:false,code:'wrong_mailbox'};
  if (input.source.providerFolder !== 'INBOX') return {ok:false,code:'wrong_folder'};
  if (!validMessageId(input.source.providerMessageId) ||
      input.selectedProviderMessageId !== input.source.providerMessageId)
    return {ok:false,code:'invalid_source'};
  const sourceProviderMessageId=input.source.providerMessageId;
  if (input.mode === 'reply' || input.mode === 'reply_all') {
    // Microsoft Graph is the only authority on Reply-To and Reply All recipients.
    if (input.to?.trim() || input.cc?.trim() || input.subject?.trim())
      return {ok:false,code:'invalid_recipients'};
    return {ok:true,mailboxId:input.mailboxId,
      compose:{mode:input.mode,sourceProviderMessageId,body:input.body}};
  }
  // Forward differs from Reply: the user MUST supply recipients. A validated
  // forward draft must preserve the original forwarded content on Graph.
  const to=normalizeMicrosoftRecipientList(input.to);
  const cc=normalizeMicrosoftRecipientList(input.cc ?? '');
  if (!to?.length || !cc || to.length+cc.length>20 ||
      new Set([...to,...cc]).size !== to.length+cc.length || input.subject?.trim())
    return {ok:false,code:'invalid_recipients'};
  return {ok:true,mailboxId:input.mailboxId,
    compose:{mode:'forward',sourceProviderMessageId,to,cc,body:input.body}};
}
