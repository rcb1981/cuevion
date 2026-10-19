/**
 * Exact Microsoft CID images: bounded, ephemeral, owner-scoped attachment reads.
 * No raw CID may become a network URL; only image bytes from an exact Graph
 * attachment already matched to the authenticated folder/message are embedded.
 */
import {
  downloadMicrosoftAttachmentFile,
  type MicrosoftAttachmentMetadata,
  type MicrosoftAttachmentFileResponse,
} from "./inboxConnectionApi";
import { isTrustedMicrosoftInertHtmlTransport } from "./microsoftInertHtmlTransport";

// Reuse the exact, authenticated download limit instead of rejecting normal
// high-resolution inline photos at the previous 512 KiB UI-only threshold.
export const MAX_MICROSOFT_CID_IMAGE_BYTES = 2 * 1024 * 1024;
export const MAX_MICROSOFT_CID_TOTAL_BYTES = 8 * 1024 * 1024;
export const MAX_MICROSOFT_CID_IMAGES = 8;
const CID_PATTERN = /^[a-zA-Z0-9_.@+%\-]{1,200}$/;
const MIME_TYPES = new Set(["image/png", "image/jpeg", "image/gif", "image/webp"]);

// Same canonical CID matching semantics as stable Gmail/IMAP: trim,
// unwrap angle brackets and case-fold. Never turn a CID into a fetch URL.
export function normalizeMicrosoftCid(value: unknown): string {
  if (typeof value !== "string") return "";
  const candidate = value.trim().replace(/^<|>$/g, "").toLowerCase();
  return CID_PATTERN.test(candidate) ? candidate : "";
}
const INLINE_CID_PATTERN = /data-microsoft-cid="([a-zA-Z0-9_.@+%\-]{1,200})"/g;

export type MicrosoftInlineCidFolder = "Inbox" | "Sent" | "Archive" | "Drafts" | "Deleted";
export function isMicrosoftInlineCidFolder(value: string | undefined): value is MicrosoftInlineCidFolder {
  return value === "Inbox" || value === "Sent" || value === "Archive" ||
    value === "Drafts" || value === "Deleted";
}

type ExactCidRequest = {
  mailboxId: string;
  providerMessageId: string;
  folder: MicrosoftInlineCidFolder;
  html: string;
  attachments: MicrosoftAttachmentMetadata[];
};
type DownloadRequest = {
  mailboxId: string;
  providerMessageId: string;
  attachmentId: string;
  folder: MicrosoftInlineCidFolder;
};
type Download = (request: DownloadRequest) => Promise<MicrosoftAttachmentFileResponse>;

export type MicrosoftCidImageMap = Readonly<Record<string, string>>;

export function discoverMicrosoftInlineCids(html: string): string[] {
  if (!isTrustedMicrosoftInertHtmlTransport(html)) return [];
  const cids = new Set<string>();
  for (const match of html.matchAll(INLINE_CID_PATTERN)) {
    if (match[1] && CID_PATTERN.test(match[1])) cids.add(match[1]);
    if (cids.size >= MAX_MICROSOFT_CID_IMAGES) break;
  }
  return Array.from(cids);
}

function sniffMicrosoftImageMime(base64: string, size: number): string | null {
  if (size < 1 || size > MAX_MICROSOFT_CID_IMAGE_BYTES ||
      base64.length !== 4 * Math.ceil(size / 3)) return null;
  try {
    const binary = atob(base64);
    if (binary.length !== size) return null;
    const bytes = Uint8Array.from(binary, c => c.charCodeAt(0));
    if (bytes.length >= 24 &&
        [137, 80, 78, 71, 13, 10, 26, 10].every((b, i) => bytes[i] === b))
      return "image/png";
    if (bytes.length >= 4 && bytes[0] === 255 && bytes[1] === 216 &&
        bytes[2] === 255 && bytes[bytes.length - 2] === 255 &&
        bytes[bytes.length - 1] === 217) return "image/jpeg";
    if (bytes.length >= 10 && (binary.startsWith("GIF89a") || binary.startsWith("GIF87a")))
      return "image/gif";
    if (bytes.length >= 16 && binary.startsWith("RIFF") &&
        binary.slice(8, 12) === "WEBP") return "image/webp";
  } catch { /* non-canonical base64 and failed decoding stay inert */ }
  return null;
}

/**
 * Never fetch CID text directly. Match one single inline fileAttachment ID from
 * exact owner-scoped Graph metadata, then recheck the exact download response.
 * External images do not go through this path; no new Graph permission is used.
 */
export async function loadMicrosoftInlineCidImages(
  request: ExactCidRequest,
  isCurrent: () => boolean,
  download: Download = downloadMicrosoftAttachmentFile,
): Promise<MicrosoftCidImageMap> {
  if (!isCurrent() || !isMicrosoftInlineCidFolder(request.folder) ||
      !Array.isArray(request.attachments) || request.attachments.length > 25) return {};
  const needed = discoverMicrosoftInlineCids(request.html);
  const output: Record<string, string> = Object.create(null);
  let acceptedBytes = 0;
  for (const cid of needed) {
    if (!isCurrent()) break;
    const expectedCid = normalizeMicrosoftCid(cid);
    if (!expectedCid) continue;
    const candidates = request.attachments.filter((item) =>
      item.kind === "file" && normalizeMicrosoftCid(item.contentId) === expectedCid &&
      item.size > 0 && item.size <= MAX_MICROSOFT_CID_IMAGE_BYTES &&
      item.size <= MAX_MICROSOFT_CID_TOTAL_BYTES - acceptedBytes,
    );
    if (candidates.length !== 1) continue; // ambiguous CIDs fail closed
    const item = candidates[0];
    try {
      const result = await download({
        mailboxId: request.mailboxId,
        providerMessageId: request.providerMessageId,
        folder: request.folder,
        attachmentId: item.id,
      });
      if (!isCurrent()) break;
      if (!result.ok || result.attachmentId !== item.id ||
          !Number.isSafeInteger(result.size) || typeof result.size !== "number" ||
          result.size < 1 || result.size > MAX_MICROSOFT_CID_IMAGE_BYTES ||
          result.size > MAX_MICROSOFT_CID_TOTAL_BYTES - acceptedBytes ||
          typeof result.base64 !== "string") continue;
      // Graph's metadata size is not always the exact byte length. The server
      // accepts a mismatch ONLY for a fully CRC-verified PNG from this exact
      // owned attachment. Keep mismatched sizes rejected for all other formats.
      const mime = sniffMicrosoftImageMime(result.base64, result.size);
      if (!mime || !MIME_TYPES.has(mime) ||
          (result.size !== item.size && mime !== "image/png")) continue;
      acceptedBytes += result.size;
      output[cid] = "data:" + mime + ";base64," + result.base64;
    } catch {
      // A missing or malformed image is not fatal to the email's text content.
    }
  }
  return isCurrent() ? output : {};
}
