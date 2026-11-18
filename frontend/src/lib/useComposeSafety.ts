import { useCallback, useContext, useLayoutEffect, useMemo, useState, type SetStateAction } from "react";
import { MailboxSnapshotContext } from "./MailboxSnapshotContext";
import { createComposeSafety, type ComposeSafety } from "./composeSafety";

export function useComposeSafety(getConnectionIdentity: (mailboxId: string) => string | null) {
  const session = useContext(MailboxSnapshotContext);
  const safety = useMemo(() => createComposeSafety(session, getConnectionIdentity), [session, getConnectionIdentity]);
  useLayoutEffect(() => {
    safety.activate();
    const invalidate = () => safety.invalidate();
    session?.signal.addEventListener("abort", invalidate);
    return () => {
      invalidate();
      session?.signal.removeEventListener("abort", invalidate);
    };
  }, [safety, session]);
  return safety;
}

export function useComposeField<T>(safety: ComposeSafety, initial: T) {
  const [value, setValue] = useState(initial);
  const setField = useCallback((update: SetStateAction<T>) => {
    if (safety.edit()) setValue(update);
  }, [safety]);
  return [value, setField] as const;
}
