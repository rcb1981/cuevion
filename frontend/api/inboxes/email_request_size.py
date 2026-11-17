"""Outbound upload budget, below Vercel's 4.5 MB Function body limit."""
# Complete UTF-8 JSON, including base64, metadata, recipients and message text.
# Keep in sync with src/lib/emailRequestSize.ts. Do not use raw file sizes.
MAX_EMAIL_REQUEST_BYTES = 4_000_000
EMAIL_REQUEST_TOO_LARGE_MESSAGE = (
    "This message and its attachments exceed the supported size. "
    "Reduce the attachments or message content and try again. "
    "Your message and selected files have been kept."
)
