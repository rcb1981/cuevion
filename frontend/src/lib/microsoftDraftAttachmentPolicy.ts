/** Source-only gate. No env value or mailbox setting enables this feature. */
export const MICROSOFT_DRAFT_ATTACHMENTS_RELEASED = false;
export const MICROSOFT_ATTACHMENT_MAX_FILE_BYTES = 2 * 1024 * 1024;
export const MICROSOFT_ATTACHMENT_MAX_FILES = 10;
export const MICROSOFT_ATTACHMENT_MAX_TOTAL_BYTES = 8 * 1024 * 1024;

export type MicrosoftNewDraftFile = { name: string; contentType: string; base64: string };

export function validMicrosoftDraftFileMetadata(name: unknown, type: unknown): boolean {
  return typeof name === "string" && name.length >= 1 && name.length <= 255 &&
    name !== "." && name !== ".." && !/[\\/]/.test(name) && !/[\p{Cc}\p{Cf}\p{Cs}]/u.test(name) &&
    typeof type === "string" && type.length <= 128 &&
    /^[a-z0-9][a-z0-9!#$&^_.+\-]*\/[a-z0-9][a-z0-9!#$&^_.+\-]*$/.test(type);
}

export function validateMicrosoftDraftFiles(files: unknown): files is MicrosoftNewDraftFile[] {
  if (!Array.isArray(files) || !files.length || files.length > MICROSOFT_ATTACHMENT_MAX_FILES) return false;
  let total = 0;
  try {
    for (const f of files) {
      if (!f || typeof f !== "object" || Array.isArray(f) ||
          Object.keys(f).sort().join(",") !== "base64,contentType,name" ||
          !validMicrosoftDraftFileMetadata(f.name, f.contentType) || typeof f.base64 !== "string" ||
          f.base64.length > 4 * Math.ceil(MICROSOFT_ATTACHMENT_MAX_FILE_BYTES / 3)) return false;
      const bytes = atob(f.base64);
      if (bytes.length > MICROSOFT_ATTACHMENT_MAX_FILE_BYTES || btoa(bytes) !== f.base64) return false;
      total += bytes.length;
      if (total > MICROSOFT_ATTACHMENT_MAX_TOTAL_BYTES) return false;
    }
    return true;
  } catch { return false; }
}

/** Prepare ephemeral bytes only. Existing attachments never count as new files. */
export async function prepareMicrosoftDraftFiles(files: readonly File[]): Promise<MicrosoftNewDraftFile[]> {
  if (!Array.isArray(files) || !files.length || files.length > MICROSOFT_ATTACHMENT_MAX_FILES ||
      files.some(f => !Number.isSafeInteger(f.size) || f.size < 0 || f.size > MICROSOFT_ATTACHMENT_MAX_FILE_BYTES ||
        !validMicrosoftDraftFileMetadata(f.name, f.type || "application/octet-stream")) ||
      files.reduce((sum, f) => sum + f.size, 0) > MICROSOFT_ATTACHMENT_MAX_TOTAL_BYTES) {
    throw new Error("microsoft_attachment_selection_invalid");
  }
  const result: MicrosoftNewDraftFile[] = [];
  for (const file of files) {
    const bytes = new Uint8Array(await file.arrayBuffer());
    if (bytes.byteLength !== file.size || bytes.byteLength > MICROSOFT_ATTACHMENT_MAX_FILE_BYTES) {
      throw new Error("microsoft_attachment_selection_invalid");
    }
    let binary = "";
    for (let i = 0; i < bytes.length; i += 8192) binary += String.fromCharCode(...bytes.subarray(i, i + 8192));
    result.push({ name: file.name, contentType: file.type || "application/octet-stream", base64: btoa(binary) });
  }
  if (!validateMicrosoftDraftFiles(result)) throw new Error("microsoft_attachment_selection_invalid");
  return result;
}
