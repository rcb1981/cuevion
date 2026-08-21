export type LatestTeamAuthorityRefreshCoordinator = {
  run<T>(
    load: () => Promise<T>,
    applyIfCurrent: (value: T) => boolean | Promise<boolean>,
  ): Promise<boolean>;
  invalidate(): void;
};

export type ScopedFreshTeamInviteUrls = {
  authorityKey: string;
  epoch: number;
  urls: Record<string, string>;
};

export function createScopedFreshTeamInviteUrls(
  authorityKey: string,
  epoch = 0,
): ScopedFreshTeamInviteUrls {
  return { authorityKey, epoch, urls: {} };
}

export function resetScopedFreshTeamInviteUrls(
  state: ScopedFreshTeamInviteUrls,
  authorityKey: string,
): ScopedFreshTeamInviteUrls {
  return state.authorityKey === authorityKey
    ? state
    : createScopedFreshTeamInviteUrls(authorityKey, state.epoch + 1);
}

export function readScopedFreshTeamInviteUrls(
  state: ScopedFreshTeamInviteUrls,
  authorityKey: string,
): Record<string, string> {
  return state.authorityKey === authorityKey ? state.urls : {};
}

export function updateScopedFreshTeamInviteUrls(
  state: ScopedFreshTeamInviteUrls,
  authorityKey: string,
  expectedEpoch: number,
  update: (current: Record<string, string>) => Record<string, string>,
): ScopedFreshTeamInviteUrls {
  if (state.authorityKey !== authorityKey || state.epoch !== expectedEpoch) {
    return state;
  }
  return {
    authorityKey,
    epoch: state.epoch,
    urls: update(state.urls),
  };
}

export function createLatestTeamAuthorityRefreshCoordinator(): LatestTeamAuthorityRefreshCoordinator {
  let generation = 0;
  let latest: Promise<boolean> | null = null;

  return {
    run<T>(
      load: () => Promise<T>,
      applyIfCurrent: (value: T) => boolean | Promise<boolean>,
    ) {
      const requestGeneration = generation + 1;
      generation = requestGeneration;
      let requestPromise!: Promise<boolean>;
      requestPromise = (async () => {
        const value = await load();
        if (generation !== requestGeneration) {
          const winningRefresh = latest;
          return winningRefresh && winningRefresh !== requestPromise
            ? await winningRefresh
            : false;
        }
        return Boolean(await applyIfCurrent(value));
      })();
      latest = requestPromise;
      return requestPromise;
    },
    invalidate() {
      generation += 1;
      latest = null;
    },
  };
}

export function findTeamMemberIndexByEmail<T extends { email: string }>(
  members: readonly T[],
  selectedEmail: string | null,
): number | null {
  const canonicalEmail = selectedEmail?.trim().toLowerCase() ?? "";
  if (!canonicalEmail) {
    return null;
  }
  const index = members.findIndex(
    (member) => member.email.trim().toLowerCase() === canonicalEmail,
  );
  return index >= 0 ? index : null;
}
