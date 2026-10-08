import assert from "node:assert/strict";
import {
  createEmptyOutOfOfficeDraft,
  isoToLocalDateTimeValue,
  localDateTimeValueToIso,
  normalizeOutOfOfficeUiDraft,
  validateOutOfOfficeUiDraft,
} from "./outOfOfficeUi";

const empty = createEmptyOutOfOfficeDraft();
assert.deepEqual(empty, {
  enabled: false,
  startsAt: null,
  endsAt: null,
  subject: "Out of office",
  message: "",
});

assert.deepEqual(
  normalizeOutOfOfficeUiDraft({
    enabled: true,
    startsAt: "2026-10-10T08:00:00Z",
    endsAt: "2026-10-20T16:00:00Z",
    subject: " Away ",
    message: " Back soon. ",
  }),
  {
    enabled: true,
    startsAt: "2026-10-10T08:00:00Z",
    endsAt: "2026-10-20T16:00:00Z",
    subject: "Away",
    message: " Back soon. ",
  },
);

const iso = localDateTimeValueToIso("2026-10-10T10:30");
assert.equal(typeof iso, "string");
assert.equal(isoToLocalDateTimeValue(iso), "2026-10-10T10:30");
assert.equal(localDateTimeValueToIso(""), null);
assert.equal(isoToLocalDateTimeValue("not-a-date"), "");

assert.match(
  validateOutOfOfficeUiDraft({ ...empty, enabled: true, subject: "" }) ?? "",
  /subject/i,
);
assert.match(
  validateOutOfOfficeUiDraft({ ...empty, enabled: true, message: "" }) ?? "",
  /message/i,
);
assert.match(
  validateOutOfOfficeUiDraft({
    ...empty,
    enabled: true,
    subject: "Away",
    message: "Back soon",
    startsAt: "2026-10-20T10:00:00Z",
    endsAt: "2026-10-10T10:00:00Z",
  }) ?? "",
  /later/i,
);
assert.equal(
  validateOutOfOfficeUiDraft({
    ...empty,
    enabled: true,
    subject: "Away",
    message: "Back soon",
  }),
  null,
);
