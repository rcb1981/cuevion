export type MailboxTitleEditorState = {
  isEditing: boolean;
  draft: string;
};

export type MailboxTitleEditorAction =
  | { type: "open"; canonicalTitle: string }
  | { type: "change"; value: string }
  | { type: "commit"; canonicalTitle: string }
  | { type: "cancel"; canonicalTitle: string };

export type MailboxTitleEditorTransition = {
  state: MailboxTitleEditorState;
  commitTitle: string | null;
};

export function createMailboxTitleEditorState(
  canonicalTitle: string,
): MailboxTitleEditorState {
  return {
    isEditing: false,
    draft: canonicalTitle,
  };
}

export function transitionMailboxTitleEditor(
  current: MailboxTitleEditorState,
  action: MailboxTitleEditorAction,
): MailboxTitleEditorTransition {
  if (action.type === "open") {
    return {
      state: {
        isEditing: true,
        draft: action.canonicalTitle,
      },
      commitTitle: null,
    };
  }

  if (!current.isEditing) {
    return { state: current, commitTitle: null };
  }

  if (action.type === "change") {
    return {
      state: {
        ...current,
        draft: action.value,
      },
      commitTitle: null,
    };
  }

  if (action.type === "cancel") {
    return {
      state: createMailboxTitleEditorState(action.canonicalTitle),
      commitTitle: null,
    };
  }

  const nextTitle = current.draft.trim();
  return {
    state: createMailboxTitleEditorState(action.canonicalTitle),
    commitTitle:
      nextTitle.length > 0 && nextTitle !== action.canonicalTitle
        ? nextTitle
        : null,
  };
}
