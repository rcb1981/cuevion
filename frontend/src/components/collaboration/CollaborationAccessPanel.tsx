import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type FormEvent,
} from "react";
import {
  isCanonicalCollaborationExternalGuestEmail,
  isValidCollaborationParticipantUserId,
  type CollaborationExternalGuest,
  type CollaborationOwnerReadDto,
} from "../../lib/collaborationOwnerReadApi";
import {
  addParticipantToCollaborationForOwner,
  createCollaborationForOwner,
  createCollaborationWithGuestForOwner,
  issueGuestInvitationForOwner,
  revokeGuestInvitationForOwner,
  type CollaborationOwnerCreateState,
} from "../../lib/collaborationOwnerWriteApi";
import { buildCollaborationGuestInviteLink } from "../../lib/collaborationGuestInviteLink";
import type { CollaborationOwnerSourceLocator } from "../../lib/collaborationOwnerSourceLocator";

export type CollaborationTeamRosterMember = {
  memberUserId: string | null;
  displayName: string;
  email: string;
  status: "active";
};

type ParticipantType = "team" | "external";
type CollaborationAccessFailureContext = "start" | "manage";
type MutationState =
  | { status: "idle" }
  | { status: "loading" }
  | { status: "failure"; message: string };

type SecureLinkState = {
  inviteId: string;
  url: string;
};

type CollaborationAccessPanelProps = {
  mode: "hidden" | "start" | "access";
  contextKey: string;
  viewerIdentityKey: string;
  locator: CollaborationOwnerSourceLocator | null;
  collaboration: CollaborationOwnerReadDto | null;
  teamMembers: CollaborationTeamRosterMember[];
  currentMemberUserId: string | null;
  currentUserEmail: string;
  onCanonicalCollaboration: (
    collaboration: CollaborationOwnerReadDto,
    expectedContextKey: string,
  ) => void;
  onRequestOverlayClose: () => void;
  onSecureLinkVisibilityChange: (visible: boolean) => void;
};

const primaryButtonClass =
  "inline-flex min-h-10 items-center justify-center rounded-full bg-pine px-5 text-[0.68rem] font-medium uppercase tracking-[0.14em] text-[color:rgba(251,248,242,0.98)] transition-[background-color,transform] duration-150 hover:bg-moss active:scale-[0.99] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--workspace-accent-border)] disabled:cursor-not-allowed disabled:opacity-45";
const secondaryButtonClass =
  "inline-flex min-h-10 items-center justify-center rounded-full border border-[var(--workspace-border-soft)] bg-[var(--workspace-card)] px-4 text-[0.66rem] font-medium uppercase tracking-[0.12em] text-[var(--workspace-text-soft)] transition-colors duration-150 hover:border-[var(--workspace-border)] hover:bg-[var(--workspace-hover-surface)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--workspace-accent-border)] disabled:cursor-not-allowed disabled:opacity-45";
const fieldClass =
  "w-full min-w-0 rounded-[14px] border border-[var(--workspace-border-soft)] bg-[var(--workspace-card)] px-3.5 py-2.5 text-[0.86rem] leading-6 text-[var(--workspace-text)] outline-none placeholder:text-[var(--workspace-text-faint)] focus:border-[var(--workspace-accent-border)] focus-visible:ring-2 focus-visible:ring-[var(--workspace-accent-border)]";

function normalizeEmail(value: string) {
  return value.trim().toLowerCase();
}

export function getEligibleCollaborationTeamMembers({
  teamMembers,
  currentMemberUserId,
  currentUserEmail,
  collaboration,
}: {
  teamMembers: CollaborationTeamRosterMember[];
  currentMemberUserId: string | null;
  currentUserEmail: string;
  collaboration: CollaborationOwnerReadDto | null;
}) {
  const participantUserIds = new Set(
    collaboration?.participants.map((participant) => participant.userId) ?? [],
  );
  const normalizedCurrentEmail = normalizeEmail(currentUserEmail);

  return teamMembers.filter((member) => {
    if (
      member.status !== "active" ||
      !isValidCollaborationParticipantUserId(member.memberUserId) ||
      member.memberUserId === currentMemberUserId ||
      normalizeEmail(member.email) === normalizedCurrentEmail ||
      participantUserIds.has(member.memberUserId)
    ) {
      return false;
    }
    return true;
  });
}

export function getCollaborationAccessFailureMessage(
  status: string,
  context: CollaborationAccessFailureContext = "manage",
) {
  if (
    context === "start" &&
    (status === "not_found" ||
      status === "service_unavailable" ||
      status === "invalid_source_locator" ||
      status === "invalid_response" ||
      status === "internal_error")
  ) {
    return "Collaboration changes are temporarily unavailable.";
  }
  if (status === "unauthorized") {
    return "Sign in again to change Collaboration access.";
  }
  if (status === "forbidden") {
    return "You don’t have permission to change Collaboration access.";
  }
  if (status === "not_found" || status === "invalid_collaboration_id") {
    return "This Collaboration is no longer available.";
  }
  if (status === "conflict") {
    return "Collaboration access changed. Review the current access and try again.";
  }
  if (status === "rate_limited") {
    return "Too many Collaboration changes were requested. Try again shortly.";
  }
  if (status === "service_unavailable") {
    return "Collaboration changes are temporarily unavailable.";
  }
  if (status === "network_failure") {
    return "The change may not have completed. Check your connection and try again explicitly.";
  }
  if (
    status === "invalid_response" ||
    status === "internal_error" ||
    status === "invalid_source_locator" ||
    status === "invalid_state" ||
    status === "invalid_participant_user_id" ||
    status === "invalid_invited_email" ||
    status === "invalid_invite_id"
  ) {
    return "Collaboration changes are temporarily unavailable.";
  }
  return "Collaboration changes are temporarily unavailable.";
}

export function getExternalGuestStatusLabel(
  status: CollaborationExternalGuest["status"],
) {
  return {
    pending: "Pending",
    active: "Active",
    logged_out: "Left collaboration",
    revoked: "Revoked",
    expired: "Expired",
  }[status];
}

function formatExpiry(expiresAt: number) {
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(expiresAt * 1_000));
}

function buildTransientSecureLink(token: string) {
  if (typeof window === "undefined") {
    return null;
  }
  return buildCollaborationGuestInviteLink(token, window.location.origin);
}

export function CollaborationAccessPanel({
  mode,
  contextKey,
  viewerIdentityKey,
  locator,
  collaboration,
  teamMembers,
  currentMemberUserId,
  currentUserEmail,
  onCanonicalCollaboration,
  onRequestOverlayClose,
  onSecureLinkVisibilityChange,
}: CollaborationAccessPanelProps) {
  const [participantType, setParticipantType] = useState<ParticipantType | null>(null);
  const [selectedTeamMemberId, setSelectedTeamMemberId] = useState("");
  const [initialState, setInitialState] =
    useState<CollaborationOwnerCreateState>("needs_review");
  const [startExternalEmail, setStartExternalEmail] = useState("");
  const [startMutation, setStartMutation] = useState<MutationState>({ status: "idle" });
  const [isAddTeamOpen, setIsAddTeamOpen] = useState(false);
  const [addTeamMemberId, setAddTeamMemberId] = useState("");
  const [addTeamMutation, setAddTeamMutation] = useState<MutationState>({ status: "idle" });
  const [isInviteGuestOpen, setIsInviteGuestOpen] = useState(false);
  const [inviteEmail, setInviteEmail] = useState("");
  const [inviteMutation, setInviteMutation] = useState<MutationState>({ status: "idle" });
  const [pendingRevokeId, setPendingRevokeId] = useState<string | null>(null);
  const [revokeMutation, setRevokeMutation] = useState<MutationState>({ status: "idle" });
  const [secureLink, setSecureLink] = useState<SecureLinkState | null>(null);
  const [copyFeedback, setCopyFeedback] = useState("");
  const [isLinkCloseConfirmationOpen, setIsLinkCloseConfirmationOpen] = useState(false);
  const firstParticipantTypeRef = useRef<HTMLInputElement | null>(null);
  const externalParticipantTypeRef = useRef<HTMLInputElement | null>(null);
  const requestGenerationRef = useRef(0);
  const previousAuthorityRef = useRef({
    contextKey,
    viewerIdentityKey,
    collaborationId: collaboration?.collaborationId ?? null,
  });

  const clearSecureLink = useCallback(() => {
    setSecureLink(null);
    setCopyFeedback("");
    setIsLinkCloseConfirmationOpen(false);
  }, []);

  useEffect(() => {
    onSecureLinkVisibilityChange(Boolean(secureLink));
  }, [onSecureLinkVisibilityChange, secureLink]);

  useEffect(
    () => () => {
      requestGenerationRef.current += 1;
      onSecureLinkVisibilityChange(false);
    },
    [onSecureLinkVisibilityChange],
  );

  useEffect(() => {
    const previous = previousAuthorityRef.current;
    const collaborationId = collaboration?.collaborationId ?? null;
    const authorityChanged =
      previous.contextKey !== contextKey ||
      previous.viewerIdentityKey !== viewerIdentityKey ||
      (previous.collaborationId !== null &&
        previous.collaborationId !== collaborationId);

    previousAuthorityRef.current = { contextKey, viewerIdentityKey, collaborationId };
    if (authorityChanged) {
      requestGenerationRef.current += 1;
      clearSecureLink();
      setParticipantType(null);
      setSelectedTeamMemberId("");
      setStartExternalEmail("");
      setStartMutation({ status: "idle" });
      setIsAddTeamOpen(false);
      setIsInviteGuestOpen(false);
      setPendingRevokeId(null);
    }
  }, [clearSecureLink, collaboration?.collaborationId, contextKey, viewerIdentityKey]);

  const eligibleTeamMembers = useMemo(
    () =>
      getEligibleCollaborationTeamMembers({
        teamMembers,
        currentMemberUserId,
        currentUserEmail,
        collaboration,
      }),
    [collaboration, currentMemberUserId, currentUserEmail, teamMembers],
  );

  useEffect(() => {
    if (mode === "start") {
      (eligibleTeamMembers.length > 0
        ? firstParticipantTypeRef.current
        : externalParticipantTypeRef.current
      )?.focus();
    }
  }, [eligibleTeamMembers.length, mode]);

  if (mode === "hidden") {
    return null;
  }

  const mutationInFlight =
    startMutation.status === "loading" ||
    addTeamMutation.status === "loading" ||
    inviteMutation.status === "loading" ||
    revokeMutation.status === "loading";

  const beginRequest = () => {
    const requestId = requestGenerationRef.current + 1;
    requestGenerationRef.current = requestId;
    return requestId;
  };
  const isCurrentRequest = (requestId: number) =>
    requestGenerationRef.current === requestId &&
    previousAuthorityRef.current.contextKey === contextKey &&
    previousAuthorityRef.current.viewerIdentityKey === viewerIdentityKey;

  const applyNewInvitation = (
    invitationCreated: boolean,
    invitation: { inviteId: string },
    token?: string,
  ) => {
    if (!invitationCreated || !token) {
      clearSecureLink();
      setInviteMutation({
        status: "failure",
        message:
          "An invitation for this guest already exists. Cuevion does not store its secure link.",
      });
      return;
    }

    const url = buildTransientSecureLink(token);
    if (!url) {
      clearSecureLink();
      setInviteMutation({
        status: "failure",
        message: "The invitation was created, but its secure link could not be shown safely.",
      });
      return;
    }

    setSecureLink({ inviteId: invitation.inviteId, url });
    setCopyFeedback("");
    setIsLinkCloseConfirmationOpen(false);
  };

  const submitStart = (event: FormEvent) => {
    event.preventDefault();
    if (!locator || !participantType || mutationInFlight) {
      return;
    }

    const normalizedExternalEmail = normalizeEmail(startExternalEmail);
    if (
      participantType === "external" &&
      normalizedExternalEmail !== "" &&
      !isCanonicalCollaborationExternalGuestEmail(normalizedExternalEmail)
    ) {
      setStartMutation({ status: "failure", message: "Enter a valid email or leave it blank." });
      return;
    }
    if (participantType === "team" && !selectedTeamMemberId) {
      return;
    }

    clearSecureLink();
    const requestId = beginRequest();
    setStartMutation({ status: "loading" });
    void (async () => {
      if (participantType === "team") {
        const result = await createCollaborationForOwner(
          locator,
          initialState,
          selectedTeamMemberId,
        );
        if (!isCurrentRequest(requestId)) {
          return;
        }
        if (result.status !== "success") {
          setStartMutation({
            status: "failure",
            message: getCollaborationAccessFailureMessage(result.status, "start"),
          });
          return;
        }
        setStartMutation({ status: "idle" });
        onCanonicalCollaboration(result.collaboration, contextKey);
        return;
      }

      const result = await createCollaborationWithGuestForOwner(
        locator,
        initialState,
        normalizedExternalEmail || undefined,
      );
      if (!isCurrentRequest(requestId)) {
        return;
      }
      if (result.status !== "success") {
        setStartMutation({
          status: "failure",
          message: getCollaborationAccessFailureMessage(result.status, "start"),
        });
        return;
      }
      setStartMutation({ status: "idle" });
      onCanonicalCollaboration(result.collaboration, contextKey);
      applyNewInvitation(
        result.invitationCreated,
        result.invitation,
        result.invitationCreated ? result.token : undefined,
      );
    })().catch(() => {
      if (isCurrentRequest(requestId)) {
        setStartMutation({
          status: "failure",
          message: getCollaborationAccessFailureMessage("network_failure", "start"),
        });
      }
    });
  };

  const submitAddTeamMember = (event: FormEvent) => {
    event.preventDefault();
    if (
      !collaboration ||
      collaboration.viewerAccess !== "owner" ||
      collaboration.participants.length >= 16 ||
      !eligibleTeamMembers.some((member) => member.memberUserId === addTeamMemberId) ||
      mutationInFlight
    ) {
      return;
    }

    const collaborationId = collaboration.collaborationId;
    const requestId = beginRequest();
    setAddTeamMutation({ status: "loading" });
    void addParticipantToCollaborationForOwner(collaborationId, addTeamMemberId)
      .then((result) => {
        if (!isCurrentRequest(requestId)) {
          return;
        }
        if (result.status !== "success") {
          setAddTeamMutation({
            status: "failure",
            message: getCollaborationAccessFailureMessage(result.status),
          });
          return;
        }
        setAddTeamMutation({ status: "idle" });
        setAddTeamMemberId("");
        setIsAddTeamOpen(false);
        onCanonicalCollaboration(result.collaboration, contextKey);
      })
      .catch(() => {
        if (isCurrentRequest(requestId)) {
          setAddTeamMutation({
            status: "failure",
            message: getCollaborationAccessFailureMessage("network_failure"),
          });
        }
      });
  };

  const submitGuestInvitation = (event: FormEvent) => {
    event.preventDefault();
    if (
      !collaboration ||
      collaboration.viewerAccess !== "owner" ||
      collaboration.externalGuests.length >= 16 ||
      mutationInFlight
    ) {
      return;
    }

    const normalizedInvitedEmail = normalizeEmail(inviteEmail);
    if (
      normalizedInvitedEmail !== "" &&
      !isCanonicalCollaborationExternalGuestEmail(normalizedInvitedEmail)
    ) {
      setInviteMutation({ status: "failure", message: "Enter a valid email or leave it blank." });
      return;
    }

    clearSecureLink();
    const collaborationId = collaboration.collaborationId;
    const requestId = beginRequest();
    setInviteMutation({ status: "loading" });
    void issueGuestInvitationForOwner(
      collaborationId,
      normalizedInvitedEmail || undefined,
    )
      .then((result) => {
        if (!isCurrentRequest(requestId)) {
          return;
        }
        if (result.status !== "success") {
          setInviteMutation({
            status: "failure",
            message: getCollaborationAccessFailureMessage(result.status),
          });
          return;
        }

        setInviteMutation({ status: "idle" });
        setInviteEmail("");
        onCanonicalCollaboration(result.collaboration, contextKey);
        applyNewInvitation(
          result.invitationCreated,
          result.invitation,
          result.invitationCreated ? result.token : undefined,
        );
      })
      .catch(() => {
        if (isCurrentRequest(requestId)) {
          setInviteMutation({
            status: "failure",
            message: getCollaborationAccessFailureMessage("network_failure"),
          });
        }
      });
  };

  const confirmRevoke = (inviteId: string) => {
    if (
      !collaboration ||
      collaboration.viewerAccess !== "owner" ||
      mutationInFlight
    ) {
      return;
    }

    const collaborationId = collaboration.collaborationId;
    const requestId = beginRequest();
    setRevokeMutation({ status: "loading" });
    void revokeGuestInvitationForOwner(collaborationId, inviteId)
      .then((result) => {
        if (!isCurrentRequest(requestId)) {
          return;
        }
        if (result.status !== "success") {
          setRevokeMutation({
            status: "failure",
            message: getCollaborationAccessFailureMessage(result.status),
          });
          return;
        }

        if (secureLink?.inviteId === inviteId) {
          clearSecureLink();
        }
        setPendingRevokeId(null);
        setRevokeMutation({ status: "idle" });
        onCanonicalCollaboration(result.collaboration, contextKey);
      })
      .catch(() => {
        if (isCurrentRequest(requestId)) {
          setRevokeMutation({
            status: "failure",
            message: getCollaborationAccessFailureMessage("network_failure"),
          });
        }
      });
  };

  const copySecureLink = () => {
    if (!secureLink) {
      return;
    }
    if (!navigator.clipboard?.writeText) {
      setCopyFeedback("Couldn’t copy automatically. Select the link and copy it manually.");
      return;
    }
    void navigator.clipboard.writeText(secureLink.url).then(
      () => setCopyFeedback("Link copied"),
      () =>
        setCopyFeedback("Couldn’t copy automatically. Select the link and copy it manually."),
    );
  };

  const secureLinkPanel = secureLink ? (
    <section
      data-collaboration-secure-link
      className="space-y-3 rounded-[18px] border border-[var(--workspace-accent-border)] bg-[linear-gradient(180deg,var(--workspace-card-featured-start),var(--workspace-card-featured-end))] p-4"
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 className="text-[0.9rem] font-medium text-[var(--workspace-text)]">
            Secure guest link
          </h3>
          <p className="mt-1 text-[0.76rem] leading-5 text-[var(--workspace-text-faint)]">
            This link is shown only now. Cuevion does not store the invitation link.
          </p>
        </div>
        <button
          type="button"
          onClick={() => setIsLinkCloseConfirmationOpen(true)}
          className={secondaryButtonClass}
        >
          Close link
        </button>
      </div>
      <label className="block space-y-1.5">
        <span className="text-[0.68rem] font-medium uppercase tracking-[0.12em] text-[var(--workspace-text-faint)]">
          Secure guest link
        </span>
        <input
          readOnly
          value={secureLink.url}
          onFocus={(event) => event.currentTarget.select()}
          className={`${fieldClass} font-mono text-[0.76rem]`}
        />
      </label>
      <div className="flex flex-wrap items-center gap-3">
        <button type="button" onClick={copySecureLink} className={primaryButtonClass}>
          Copy secure link
        </button>
        <div aria-live="polite" className="text-[0.76rem] leading-5 text-[var(--workspace-text-faint)]">
          {copyFeedback}
        </div>
      </div>
      {isLinkCloseConfirmationOpen ? (
        <div
          role="alertdialog"
          aria-modal="true"
          aria-labelledby="collaboration-link-close-title"
          className="space-y-3 rounded-[14px] border border-[var(--workspace-border-soft)] bg-[var(--workspace-card)] p-3"
        >
          <div id="collaboration-link-close-title" className="text-[0.82rem] font-medium text-[var(--workspace-text)]">
            Cuevion doesn’t store this link. Make sure you’ve copied it before closing.
          </div>
          <div className="flex flex-wrap justify-end gap-2">
            <button type="button" onClick={() => setIsLinkCloseConfirmationOpen(false)} className={secondaryButtonClass}>
              Back
            </button>
            <button type="button" onClick={clearSecureLink} className={primaryButtonClass}>
              Close anyway
            </button>
          </div>
        </div>
      ) : null}
    </section>
  ) : null;

  if (mode === "start") {
    const normalizedExternalEmail = normalizeEmail(startExternalEmail);
    const validExternalEmail =
      normalizedExternalEmail === "" ||
      isCanonicalCollaborationExternalGuestEmail(normalizedExternalEmail);
    const canSubmit =
      Boolean(locator) &&
      !mutationInFlight &&
      ((participantType === "team" && Boolean(selectedTeamMemberId)) ||
        (participantType === "external" && validExternalEmail));

    return (
      <form
        data-collaboration-owner-start
        className="space-y-4"
        onSubmit={submitStart}
      >
        <section className="space-y-3 rounded-[22px] border border-[var(--workspace-border-soft)] bg-[linear-gradient(180deg,var(--workspace-card),var(--workspace-card-subtle))] px-4 py-5 sm:px-5">
          <div>
            <h3 className="text-[1rem] font-medium tracking-tight text-[var(--workspace-text)]">
              Start collaboration
            </h3>
            <p className="mt-1.5 text-[0.82rem] leading-6 text-[var(--workspace-text-faint)]">
              Choose who should have access to this email’s Collaboration.
            </p>
          </div>

          <fieldset className="grid gap-3 sm:grid-cols-2">
            <legend className="sr-only">Participant type</legend>
            <label className={`flex cursor-pointer gap-3 rounded-[16px] border p-3.5 ${participantType === "team" ? "border-[var(--workspace-accent-border)] bg-[var(--workspace-card-featured-start)]" : "border-[var(--workspace-border-soft)] bg-[var(--workspace-card)]"} ${eligibleTeamMembers.length === 0 ? "cursor-not-allowed opacity-65" : ""}`}>
              <input
                ref={firstParticipantTypeRef}
                type="radio"
                name="collaboration-participant-type"
                value="team"
                checked={participantType === "team"}
                disabled={eligibleTeamMembers.length === 0}
                onChange={() => {
                  setParticipantType("team");
                  setStartMutation({ status: "idle" });
                }}
                className="mt-1 h-4 w-4 accent-[var(--workspace-accent)]"
              />
              <span className="min-w-0">
                <span className="block text-[0.86rem] font-medium text-[var(--workspace-text)]">Team member</span>
                <span className="mt-1 block text-[0.76rem] leading-5 text-[var(--workspace-text-faint)]">
                  Collaborate with someone in your Cuevion Team. Team members can see shared messages and internal notes.
                </span>
              </span>
            </label>
            <label className={`flex cursor-pointer gap-3 rounded-[16px] border p-3.5 ${participantType === "external" ? "border-[var(--workspace-accent-border)] bg-[var(--workspace-card-featured-start)]" : "border-[var(--workspace-border-soft)] bg-[var(--workspace-card)]"}`}>
              <input
                ref={externalParticipantTypeRef}
                type="radio"
                name="collaboration-participant-type"
                value="external"
                checked={participantType === "external"}
                onChange={() => {
                  setParticipantType("external");
                  setStartMutation({ status: "idle" });
                }}
                className="mt-1 h-4 w-4 accent-[var(--workspace-accent)]"
              />
              <span className="min-w-0">
                <span className="block text-[0.86rem] font-medium text-[var(--workspace-text)]">External guest</span>
                <span className="mt-1 block text-[0.76rem] leading-5 text-[var(--workspace-text-faint)]">
                  Invite someone without a Cuevion account. External guests can only see shared collaboration messages.
                </span>
              </span>
            </label>
          </fieldset>

          {participantType === "team" ? (
            <fieldset className="space-y-2">
              <legend className="text-[0.68rem] font-medium uppercase tracking-[0.12em] text-[var(--workspace-text-faint)]">Team member</legend>
              {eligibleTeamMembers.map((member) => (
                <label key={member.memberUserId} className="flex cursor-pointer items-start gap-3 rounded-[14px] border border-[var(--workspace-border-soft)] bg-[var(--workspace-card)] p-3">
                  <input
                    type="radio"
                    name="collaboration-team-member"
                    value={member.memberUserId ?? ""}
                    checked={selectedTeamMemberId === member.memberUserId}
                    onChange={() => setSelectedTeamMemberId(member.memberUserId ?? "")}
                    className="mt-1 h-4 w-4 accent-[var(--workspace-accent)]"
                  />
                  <span className="min-w-0">
                    <span className="block break-words text-[0.84rem] font-medium text-[var(--workspace-text)]">{member.displayName}</span>
                    <span className="block break-all text-[0.74rem] text-[var(--workspace-text-faint)]">{member.email}</span>
                  </span>
                </label>
              ))}
            </fieldset>
          ) : null}

          {eligibleTeamMembers.length === 0 ? (
            <div className="rounded-[14px] bg-[var(--workspace-card)] px-3.5 py-3 text-[0.78rem] leading-5 text-[var(--workspace-text-faint)]">
              <div className="font-medium text-[var(--workspace-text-soft)]">No other eligible Team members yet.</div>
              <div>Add a Team member in Team Settings first. External guest access remains available.</div>
            </div>
          ) : null}

          {participantType === "external" ? (
            <label className="block space-y-1.5">
              <span className="text-[0.68rem] font-medium uppercase tracking-[0.12em] text-[var(--workspace-text-faint)]">Email (optional)</span>
              <input
                type="email"
                value={startExternalEmail}
                onChange={(event) => {
                  setStartExternalEmail(event.target.value);
                  setStartMutation({ status: "idle" });
                }}
                placeholder="guest@example.com"
                className={fieldClass}
              />
              <span className="block text-[0.74rem] leading-5 text-[var(--workspace-text-faint)]">
                Email is optional and only helps identify the guest. Access is controlled by the secure link, which you’ll share yourself.
              </span>
            </label>
          ) : null}

          <fieldset className="space-y-2">
            <legend className="text-[0.68rem] font-medium uppercase tracking-[0.12em] text-[var(--workspace-text-faint)]">Reason</legend>
            <div className="grid gap-2 sm:grid-cols-3">
              {([
                ["needs_review", "Needs input"],
                ["needs_action", "Needs action"],
                ["note_only", "Notes only"],
              ] as const).map(([value, label]) => (
                <label key={value} className="flex cursor-pointer items-center gap-2 rounded-[12px] border border-[var(--workspace-border-soft)] bg-[var(--workspace-card)] px-3 py-2.5 text-[0.78rem] text-[var(--workspace-text-soft)]">
                  <input type="radio" name="collaboration-initial-state" value={value} checked={initialState === value} onChange={() => setInitialState(value)} className="h-4 w-4 accent-[var(--workspace-accent)]" />
                  {label}
                </label>
              ))}
            </div>
          </fieldset>

          {startMutation.status === "failure" ? (
            <div role="alert" data-collaboration-owner-create-feedback className="rounded-[14px] bg-[var(--workspace-card)] px-3.5 py-3 text-[0.78rem] leading-5 text-[var(--workspace-text-faint)]">
              {startMutation.message}
            </div>
          ) : null}
          <div className="flex flex-wrap justify-end gap-2">
            <button type="button" onClick={onRequestOverlayClose} className={secondaryButtonClass}>Cancel</button>
            <button type="submit" disabled={!canSubmit} className={primaryButtonClass}>
              {startMutation.status === "loading" ? "Starting collaboration…" : "Start collaboration"}
            </button>
          </div>
        </section>
        {secureLinkPanel}
      </form>
    );
  }

  if (!collaboration) {
    return null;
  }

  const isOwner = collaboration.viewerAccess === "owner";
  const teamLimitReached = collaboration.participants.length >= 16;
  const externalGuests = isOwner ? collaboration.externalGuests : [];
  const guestLimitReached = externalGuests.length >= 16;

  return (
    <section data-collaboration-access-panel className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h3 className="text-[0.72rem] font-medium uppercase tracking-[0.16em] text-[var(--workspace-text-faint)]">Access</h3>
          <p className="mt-1 text-[0.76rem] leading-5 text-[var(--workspace-text-faint)]">Team members can see Shared messages and Internal Notes. External guests can see Shared messages only.</p>
        </div>
      </div>

      <div className="grid gap-3">
        <section className="space-y-2 rounded-[18px] border border-[var(--workspace-border-soft)] bg-[var(--workspace-card)] p-4">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h4 className="text-[0.84rem] font-medium text-[var(--workspace-text)]">Team members</h4>
            {isOwner ? (
              <button type="button" onClick={() => setIsAddTeamOpen((current) => !current)} disabled={teamLimitReached || eligibleTeamMembers.length === 0 || mutationInFlight} className={secondaryButtonClass}>Add Team member</button>
            ) : null}
          </div>
          <div className="space-y-2">
            {collaboration.participants.map((participant) => (
              <div key={participant.userId} className="flex min-w-0 flex-wrap items-center justify-between gap-2 rounded-[12px] bg-[var(--workspace-card-subtle)] px-3 py-2.5">
                <span className="min-w-0 break-words text-[0.82rem] font-medium text-[var(--workspace-text)]">{participant.displayName}</span>
                <span className="text-[0.62rem] font-medium uppercase tracking-[0.12em] text-[var(--workspace-text-faint)]">{participant.access === "owner" ? "Owner" : "Team member"}</span>
              </div>
            ))}
          </div>
          {teamLimitReached ? <p className="text-[0.76rem] text-[var(--workspace-text-faint)]">Team participant limit reached.</p> : null}
          {isOwner && eligibleTeamMembers.length === 0 && !teamLimitReached ? <p className="text-[0.76rem] leading-5 text-[var(--workspace-text-faint)]">No other eligible Team members yet. Add a Team member in Team Settings first.</p> : null}
          {isOwner && isAddTeamOpen && !teamLimitReached ? (
            <form onSubmit={submitAddTeamMember} className="space-y-2 rounded-[14px] bg-[var(--workspace-card-subtle)] p-3">
              <label className="block space-y-1.5">
                <span className="text-[0.68rem] font-medium uppercase tracking-[0.12em] text-[var(--workspace-text-faint)]">Team member</span>
                <select value={addTeamMemberId} onChange={(event) => { setAddTeamMemberId(event.target.value); setAddTeamMutation({ status: "idle" }); }} className={fieldClass}>
                  <option value="">Select one Team member</option>
                  {eligibleTeamMembers.map((member) => <option key={member.memberUserId} value={member.memberUserId ?? ""}>{member.displayName} — {member.email}</option>)}
                </select>
              </label>
              {addTeamMutation.status === "failure" ? <div role="alert" className="text-[0.76rem] leading-5 text-[var(--workspace-text-faint)]">{addTeamMutation.message}</div> : null}
              <div className="flex flex-wrap justify-end gap-2">
                <button type="button" onClick={() => setIsAddTeamOpen(false)} className={secondaryButtonClass}>Cancel</button>
                <button type="submit" disabled={!addTeamMemberId || mutationInFlight} className={primaryButtonClass}>{addTeamMutation.status === "loading" ? "Adding…" : "Add Team member"}</button>
              </div>
            </form>
          ) : null}
        </section>

        {isOwner ? (
          <section className="space-y-2 rounded-[18px] border border-[var(--workspace-border-soft)] bg-[var(--workspace-card)] p-4">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <h4 className="text-[0.84rem] font-medium text-[var(--workspace-text)]">External guests</h4>
              <button type="button" onClick={() => setIsInviteGuestOpen((current) => !current)} disabled={guestLimitReached || mutationInFlight} className={secondaryButtonClass}>Invite external guest</button>
            </div>
            {externalGuests.length > 0 ? (
              <div className="space-y-2">
                {externalGuests.map((guest) => {
                  const canRevoke = guest.status === "pending" || guest.status === "active";
                  const visibleName = guest.displayName ?? guest.invitedEmail ?? "Secure-link guest";
                  return (
                    <div key={guest.inviteId} className="space-y-2 rounded-[12px] bg-[var(--workspace-card-subtle)] px-3 py-2.5">
                      <div className="flex min-w-0 flex-wrap items-start justify-between gap-2">
                        <div className="min-w-0">
                          <div className="break-words text-[0.82rem] font-medium text-[var(--workspace-text)]">{visibleName}</div>
                          {guest.displayName && guest.invitedEmail ? <div className="break-all text-[0.72rem] text-[var(--workspace-text-faint)]">{guest.invitedEmail}</div> : null}
                          {(guest.status === "pending" || guest.status === "active") ? <div className="text-[0.7rem] text-[var(--workspace-text-faint)]">Expires {formatExpiry(guest.expiresAt)}</div> : null}
                        </div>
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="text-[0.62rem] font-medium uppercase tracking-[0.12em] text-[var(--workspace-text-faint)]">{getExternalGuestStatusLabel(guest.status)}</span>
                          {canRevoke ? <button type="button" disabled={mutationInFlight} onClick={() => { setPendingRevokeId(guest.inviteId); setRevokeMutation({ status: "idle" }); }} className={secondaryButtonClass}>Revoke access</button> : null}
                        </div>
                      </div>
                      {pendingRevokeId === guest.inviteId ? (
                        <div role="alertdialog" aria-modal="true" aria-labelledby="collaboration-revoke-guest-title" className="space-y-2 rounded-[12px] border border-[var(--workspace-border-soft)] bg-[var(--workspace-card)] p-3">
                          <div id="collaboration-revoke-guest-title" className="text-[0.78rem] font-medium text-[var(--workspace-text)]">Revoke this guest’s access?</div>
                          {revokeMutation.status === "failure" ? <div role="alert" className="text-[0.74rem] leading-5 text-[var(--workspace-text-faint)]">{revokeMutation.message}</div> : null}
                          <div className="flex flex-wrap justify-end gap-2">
                            <button type="button" onClick={() => setPendingRevokeId(null)} className={secondaryButtonClass}>Back</button>
                            <button type="button" onClick={() => confirmRevoke(guest.inviteId)} disabled={mutationInFlight} className={primaryButtonClass}>{revokeMutation.status === "loading" ? "Revoking…" : "Revoke access"}</button>
                          </div>
                        </div>
                      ) : null}
                    </div>
                  );
                })}
              </div>
            ) : <p className="text-[0.76rem] text-[var(--workspace-text-faint)]">No external guests.</p>}
            {guestLimitReached ? <p className="text-[0.76rem] text-[var(--workspace-text-faint)]">External guest invitation limit reached.</p> : null}
            {!isInviteGuestOpen && inviteMutation.status === "failure" ? <div role="alert" aria-live="polite" className="text-[0.76rem] leading-5 text-[var(--workspace-text-faint)]">{inviteMutation.message}</div> : null}
            {isInviteGuestOpen && !guestLimitReached ? (
              <form onSubmit={submitGuestInvitation} className="space-y-2 rounded-[14px] bg-[var(--workspace-card-subtle)] p-3">
                <label className="block space-y-1.5">
                  <span className="text-[0.68rem] font-medium uppercase tracking-[0.12em] text-[var(--workspace-text-faint)]">Email (optional)</span>
                  <input type="email" value={inviteEmail} onChange={(event) => { setInviteEmail(event.target.value); setInviteMutation({ status: "idle" }); }} placeholder="guest@example.com" className={fieldClass} />
                  <span className="block text-[0.74rem] leading-5 text-[var(--workspace-text-faint)]">Email is optional and only helps identify the guest. Access is controlled by the secure link, which you’ll share yourself.</span>
                </label>
                {inviteMutation.status === "failure" ? <div role="alert" aria-live="polite" className="text-[0.76rem] leading-5 text-[var(--workspace-text-faint)]">{inviteMutation.message}</div> : null}
                <div className="flex flex-wrap justify-end gap-2">
                  <button type="button" onClick={() => setIsInviteGuestOpen(false)} className={secondaryButtonClass}>Cancel</button>
                  <button type="submit" disabled={mutationInFlight} className={primaryButtonClass}>{inviteMutation.status === "loading" ? "Creating secure link…" : "Create secure link"}</button>
                </div>
              </form>
            ) : null}
          </section>
        ) : null}
      </div>
      {secureLinkPanel}
    </section>
  );
}
