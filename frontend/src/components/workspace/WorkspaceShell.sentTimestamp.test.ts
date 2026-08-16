import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { transform } from "sucrase";

const workspaceShellSource = readFileSync(
  resolve(process.cwd(), "src/components/workspace/WorkspaceShell.tsx"),
  "utf8",
);

const failures: string[] = [];
const expectContract = (condition: boolean, message: string) => {
  if (!condition) {
    failures.push(message);
  }
};

const timestampHelperStart = workspaceShellSource.indexOf(
  "function resolveDesktopThreadTimestamp(",
);
const timestampHelperEnd = workspaceShellSource.indexOf(
  "function resolveMailDateMs(",
  timestampHelperStart,
);

expectContract(
  timestampHelperStart >= 0 && timestampHelperEnd > timestampHelperStart,
  "desktop thread timestamp resolution must have one testable datetime-authority helper",
);

if (timestampHelperStart >= 0 && timestampHelperEnd > timestampHelperStart) {
  const compiledHelper = transform(
    workspaceShellSource.slice(timestampHelperStart, timestampHelperEnd),
    { transforms: ["typescript"] },
  ).code;
  const loadTimestampHarness = new Function(
    `${compiledHelper}\nreturn { resolveDesktopThreadTimestamp };`,
  ) as () => {
    resolveDesktopThreadTimestamp: (
      message: { createdAt?: string; timestamp: string },
      nowMs?: number,
    ) => { dateTime: string | null; label: string };
  };
  const { resolveDesktopThreadTimestamp } = loadTimestampHarness();
  const createdAt = "2026-08-16T13:12:00+02:00";
  const laterMs = new Date("2026-08-16T18:00:00+02:00").getTime();
  const staleSyntheticSent = {
    createdAt,
    timestamp: "Sent just now",
  };
  const resolved = resolveDesktopThreadTimestamp(staleSyntheticSent, laterMs);

  expectContract(
    resolved.dateTime === createdAt && resolved.label !== "Sent just now",
    "a valid synthetic Sent createdAt must replace a stale relative display label",
  );
  expectContract(
    JSON.stringify(
      resolveDesktopThreadTimestamp(
        JSON.parse(JSON.stringify(staleSyntheticSent)),
        laterMs,
      ),
    ) === JSON.stringify(resolved),
    "persisted and reopened synthetic Sent records must still resolve from createdAt",
  );

  const providerMessage = {
    createdAt: "2026-08-15T09:30:00+02:00",
    timestamp: "August 15 at 09:30",
  };
  const providerSnapshot = JSON.stringify(providerMessage);
  const providerResolved = resolveDesktopThreadTimestamp(providerMessage, laterMs);

  expectContract(
    providerResolved.dateTime === providerMessage.createdAt &&
      providerResolved.label === providerMessage.timestamp &&
      JSON.stringify(providerMessage) === providerSnapshot,
    "provider datetime data must remain valid and unmodified",
  );
  expectContract(
    resolveDesktopThreadTimestamp(
      { createdAt: "not-a-date", timestamp: "Provider fallback" },
      laterMs,
    ).label === "Provider fallback",
    "invalid or absent createdAt must preserve the existing provider display fallback",
  );
}

const primarySendStart = workspaceShellSource.indexOf("const sendMessage = async");
const primarySendEnd = workspaceShellSource.indexOf(
  "const handleAttachmentOpen =",
  primarySendStart,
);
const primarySendSource = workspaceShellSource.slice(primarySendStart, primarySendEnd);
const autoReplyStart = workspaceShellSource.indexOf("const pendingReplies:");
const autoReplyEnd = workspaceShellSource.indexOf(
  "if (pendingReplies.length === 0)",
  autoReplyStart,
);
const autoReplySource = workspaceShellSource.slice(autoReplyStart, autoReplyEnd);
const threadMessageStart = workspaceShellSource.indexOf("const renderThreadMessage =");
const threadTimelineStart = workspaceShellSource.indexOf(
  "const renderThreadTimeline =",
  threadMessageStart,
);
const threadMessageSource = workspaceShellSource.slice(
  threadMessageStart,
  threadTimelineStart,
);

expectContract(
  /const sentAt\s*=\s*new Date\(\)\.toISOString\(\)/.test(primarySendSource) &&
    /const sentTimeLabel\s*=\s*resolveDesktopThreadTimestamp/.test(
      primarySendSource,
    ) &&
    /time:\s*sentTimeLabel/.test(primarySendSource) &&
    /createdAt:\s*sentAt/.test(primarySendSource) &&
    /timestamp:\s*sentAt/.test(primarySendSource) &&
    !/Sent just now/.test(primarySendSource),
  "successful synthetic Sent creation must store one real completion datetime",
);
expectContract(
  /const autoReplySentAt\s*=\s*new Date\(now\)\.toISOString\(\)/.test(
    autoReplySource,
  ) &&
    /const autoReplyTimeLabel\s*=\s*resolveDesktopThreadTimestamp/.test(
      autoReplySource,
    ) &&
    /time:\s*autoReplyTimeLabel/.test(autoReplySource) &&
    /createdAt:\s*autoReplySentAt/.test(autoReplySource) &&
    /timestamp:\s*autoReplySentAt/.test(autoReplySource) &&
    !/Sent just now/.test(autoReplySource),
  "automatic synthetic Sent creation must not persist a static relative label",
);
expectContract(
  /resolveDesktopThreadTimestamp\(threadMessage\)/.test(threadMessageSource) &&
    /<time dateTime=\{resolvedTimestamp\.dateTime\}>\{resolvedTimestamp\.label\}<\/time>/.test(
      threadMessageSource,
    ),
  "desktop thread members must render both semantic and visible time from datetime authority",
);

assert.equal(
  failures.length,
  0,
  `Sent timestamp contract failures:\n${failures
    .map((failure) => `- ${failure}`)
    .join("\n")}`,
);
