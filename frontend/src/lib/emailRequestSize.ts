// Vercel caps Function request bodies at 4.5 MB. Reserve at least 500,000
// bytes of headroom; count the complete serialized UTF-8 body, not raw files.
// Keep in sync with api/inboxes/email_request_size.py.
export const MAX_EMAIL_REQUEST_BYTES = 4_000_000;
export const EMAIL_REQUEST_TOO_LARGE_MESSAGE =
  "This message and its attachments exceed the supported size. Reduce the attachments or message content and try again. Your message and selected files have been kept.";

export function isEmailRequestTooLarge(serializedBody: string): boolean {
  return new TextEncoder().encode(serializedBody).byteLength > MAX_EMAIL_REQUEST_BYTES;
}
