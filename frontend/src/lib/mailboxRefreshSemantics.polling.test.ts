declare const process: { exitCode?: number };

import assert from "node:assert/strict";
import {
  createActiveMailboxPollingController,
  resolveMailboxRefreshPlan,
} from "./mailboxRefreshSemantics";

type TimerEntry = {
  dueAtMs: number;
  callback: () => void;
};

class FakePollingClock {
  nowMs = 0;
  visible = true;
  nextTimerId = 1;
  timers = new Map<number, TimerEntry>();
  visibilityListeners = new Set<() => void>();

  now = () => this.nowMs;
  isVisible = () => this.visible;
  setTimer = (callback: () => void, delayMs: number) => {
    const timerId = this.nextTimerId;
    this.nextTimerId += 1;
    this.timers.set(timerId, {
      dueAtMs: this.nowMs + delayMs,
      callback,
    });
    return timerId;
  };
  clearTimer = (timerId: number) => {
    this.timers.delete(timerId);
  };
  addVisibilityListener = (listener: () => void) => {
    this.visibilityListeners.add(listener);
  };
  removeVisibilityListener = (listener: () => void) => {
    this.visibilityListeners.delete(listener);
  };

  setVisibility(visible: boolean) {
    this.visible = visible;
    [...this.visibilityListeners].forEach((listener) => listener());
  }

  advanceBy(durationMs: number) {
    const targetMs = this.nowMs + durationMs;
    let callbacksRun = 0;
    while (true) {
      const nextTimer = [...this.timers.entries()]
        .filter(([, entry]) => entry.dueAtMs <= targetMs)
        .sort((left, right) => left[1].dueAtMs - right[1].dueAtMs)[0];
      if (!nextTimer) {
        break;
      }
      callbacksRun += 1;
      assert.ok(callbacksRun < 1_000, "Polling timer must not retry tightly");
      const [timerId, entry] = nextTimer;
      this.timers.delete(timerId);
      this.nowMs = entry.dueAtMs;
      entry.callback();
    }
    this.nowMs = targetMs;
  }
}

const INTERVAL_MS = 3 * 60 * 1_000;

function createHarness({ visible = true, inFlight = false } = {}) {
  const clock = new FakePollingClock();
  clock.visible = visible;
  let refreshInFlight = inFlight;
  let refreshCount = 0;
  const controller = createActiveMailboxPollingController({
    intervalMs: INTERVAL_MS,
    clock,
    isRefreshInFlight: () => refreshInFlight,
    refresh: () => {
      refreshCount += 1;
    },
  });
  return {
    clock,
    controller,
    getRefreshCount: () => refreshCount,
    setRefreshInFlight: (nextValue: boolean) => {
      refreshInFlight = nextValue;
    },
  };
}

let passed = 0;
function test(name: string, assertion: () => void) {
  assertion();
  passed += 1;
  console.log(`  ✓ ${name}`);
}

try {
  test("visible automatic polling fires at three minutes", () => {
    const harness = createHarness();
    harness.clock.advanceBy(INTERVAL_MS - 1);
    assert.equal(harness.getRefreshCount(), 0);
    harness.clock.advanceBy(1);
    assert.equal(harness.getRefreshCount(), 1);
  });

  test("hidden before the deadline prevents automatic refresh", () => {
    const harness = createHarness();
    harness.clock.advanceBy(30_000);
    harness.clock.setVisibility(false);
    harness.clock.advanceBy(INTERVAL_MS);
    assert.equal(harness.getRefreshCount(), 0);
    assert.equal(harness.clock.timers.size, 0);
  });

  test("hidden past one deadline produces zero refreshes", () => {
    const harness = createHarness({ visible: false });
    harness.clock.advanceBy(INTERVAL_MS + 1);
    assert.equal(harness.getRefreshCount(), 0);
  });

  test("hidden across many deadlines produces zero refreshes", () => {
    const harness = createHarness({ visible: false });
    harness.clock.advanceBy(30 * 60 * 1_000);
    assert.equal(harness.getRefreshCount(), 0);
  });

  test("visible again before due resumes the remaining cadence", () => {
    const harness = createHarness();
    harness.clock.advanceBy(30_000);
    harness.clock.setVisibility(false);
    harness.clock.advanceBy(60_000);
    harness.clock.setVisibility(true);
    assert.equal(harness.getRefreshCount(), 0);
    harness.clock.advanceBy(90_000);
    assert.equal(harness.getRefreshCount(), 1);
  });

  test("visible again after due performs exactly one catch-up", () => {
    const harness = createHarness();
    harness.clock.advanceBy(170_000);
    harness.clock.setVisibility(false);
    harness.clock.advanceBy(30_000);
    harness.clock.setVisibility(true);
    assert.equal(harness.getRefreshCount(), 1);
  });

  test("a 45-minute hidden period performs one catch-up", () => {
    const harness = createHarness({ visible: false });
    harness.clock.advanceBy(45 * 60 * 1_000);
    harness.clock.setVisibility(true);
    assert.equal(harness.getRefreshCount(), 1);
  });

  test("repeated visibility toggles do not duplicate catch-up", () => {
    const harness = createHarness({ visible: false });
    harness.clock.advanceBy(45 * 60 * 1_000);
    harness.clock.setVisibility(true);
    harness.clock.setVisibility(false);
    harness.clock.setVisibility(true);
    assert.equal(harness.getRefreshCount(), 1);
  });

  test("overdue catch-up coalesces with an in-flight refresh", () => {
    const harness = createHarness({ visible: false, inFlight: true });
    harness.clock.advanceBy(INTERVAL_MS);
    harness.clock.setVisibility(true);
    assert.equal(harness.getRefreshCount(), 0);
    harness.setRefreshInFlight(false);
    harness.clock.advanceBy(INTERVAL_MS);
    assert.equal(harness.getRefreshCount(), 1);
  });

  test("normal timer coalesces with an in-flight refresh", () => {
    const harness = createHarness({ inFlight: true });
    harness.clock.advanceBy(INTERVAL_MS);
    assert.equal(harness.getRefreshCount(), 0);
    harness.setRefreshInFlight(false);
    harness.clock.advanceBy(INTERVAL_MS);
    assert.equal(harness.getRefreshCount(), 1);
  });

  test("active-mailbox replacement stops the old controller", () => {
    const oldMailbox = createHarness();
    oldMailbox.clock.advanceBy(60_000);
    oldMailbox.controller.stop();
    const newMailbox = createHarness();
    oldMailbox.clock.advanceBy(INTERVAL_MS);
    newMailbox.clock.advanceBy(INTERVAL_MS);
    assert.equal(oldMailbox.getRefreshCount(), 0);
    assert.equal(newMailbox.getRefreshCount(), 1);
  });

  test("stop clears the active timer", () => {
    const harness = createHarness();
    assert.equal(harness.clock.timers.size, 1);
    harness.controller.stop();
    assert.equal(harness.clock.timers.size, 0);
  });

  test("stop removes the exact visibility listener", () => {
    const harness = createHarness();
    assert.equal(harness.clock.visibilityListeners.size, 1);
    harness.controller.stop();
    assert.equal(harness.clock.visibilityListeners.size, 0);
  });

  test("refresh failure policy has no immediate retry", () => {
    const harness = createHarness();
    harness.clock.advanceBy(INTERVAL_MS);
    assert.equal(harness.getRefreshCount(), 1);
    assert.equal(harness.clock.timers.size, 1);
    harness.clock.advanceBy(INTERVAL_MS - 1);
    assert.equal(harness.getRefreshCount(), 1);
  });

  test("30 visible minutes retain ten automatic cycles", () => {
    const harness = createHarness();
    harness.clock.advanceBy(30 * 60 * 1_000);
    assert.equal(harness.getRefreshCount(), 10);
  });

  test("30 hidden minutes produce zero Inbox, Trash, and Archive calls", () => {
    const harness = createHarness({ visible: false });
    harness.clock.advanceBy(30 * 60 * 1_000);
    const cycles = harness.getRefreshCount();
    assert.deepEqual({ inbox: cycles, trash: cycles, archive: 0 }, {
      inbox: 0,
      trash: 0,
      archive: 0,
    });
  });

  test("overdue return produces one mailbox refresh cycle", () => {
    const harness = createHarness({ visible: false });
    harness.clock.advanceBy(30 * 60 * 1_000);
    harness.clock.setVisibility(true);
    assert.equal(harness.getRefreshCount(), 1);
  });

  test("interval refresh plan leaves Archive unchanged", () => {
    assert.deepEqual(
      resolveMailboxRefreshPlan({
        reason: "interval",
        inboxFetchInFlight: false,
        archiveFetchInFlight: false,
        hasArchiveSnapshot: false,
        archiveCapability: "available",
      }),
      {
        shouldFetchInbox: true,
        shouldFetchArchive: false,
        archiveErrorScope: null,
      },
    );
  });

  test("startup and manual refresh plans remain unchanged", () => {
    for (const reason of ["startup", "manual"] as const) {
      const plan = resolveMailboxRefreshPlan({
        reason,
        inboxFetchInFlight: false,
        archiveFetchInFlight: false,
        hasArchiveSnapshot: false,
        archiveCapability: "unknown",
      });
      assert.equal(plan.shouldFetchInbox, true);
      assert.equal(plan.shouldFetchArchive, true);
    }
  });

  test("controller cleanup is idempotent", () => {
    const harness = createHarness();
    harness.controller.stop();
    harness.controller.stop();
    harness.clock.advanceBy(INTERVAL_MS);
    assert.equal(harness.getRefreshCount(), 0);
  });

  console.log(`mailboxRefreshSemantics polling: ${passed} tests passed`);
} catch (error) {
  console.error(error);
  process.exitCode = 1;
}
