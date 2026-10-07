const elements = {
  loading: document.querySelector("#loading"),
  signedOut: document.querySelector("#signed-out"),
  denied: document.querySelector("#denied"),
  unavailable: document.querySelector("#unavailable"),
  adminPanel: document.querySelector("#admin-panel"),
  retry: document.querySelector("#retry"),
  form: document.querySelector("#invite-form"),
  name: document.querySelector("#invitee-name"),
  email: document.querySelector("#invitee-email"),
  create: document.querySelector("#create-invite"),
  result: document.querySelector("#result"),
  resultName: document.querySelector("#result-name"),
  resultMeta: document.querySelector("#result-meta"),
  link: document.querySelector("#invite-link"),
  copy: document.querySelector("#copy-link"),
  revoke: document.querySelector("#revoke-invite"),
  next: document.querySelector("#new-invite"),
  feedback: document.querySelector("#feedback"),
};

let currentInvitationId = null;

function showOnly(section) {
  for (const candidate of [
    elements.loading,
    elements.signedOut,
    elements.denied,
    elements.unavailable,
    elements.adminPanel,
  ]) {
    candidate.classList.toggle("hidden", candidate !== section);
  }
}

function setFeedback(message) {
  elements.feedback.textContent = message;
}

async function request(operation, body) {
  const response = await fetch(
    "/api/tester/invite?op=" + encodeURIComponent(operation),
    {
      method: "POST",
      credentials: "include",
      cache: "no-store",
      headers: {
        "Content-Type": "application/json",
        Accept: "application/json",
      },
      body: JSON.stringify(body),
    },
  );
  const payload = await response.json().catch(() => null);
  return { response, payload };
}

async function loadCapability() {
  showOnly(elements.loading);
  try {
    const { response, payload } = await request("capability", {});
    if (
      response.ok &&
      payload &&
      payload.ok === true &&
      payload.canManageTesterAccess === true
    ) {
      showOnly(elements.adminPanel);
      return;
    }
    if (response.status === 401) {
      showOnly(elements.signedOut);
      return;
    }
    if (response.status === 403) {
      showOnly(elements.denied);
      return;
    }
    showOnly(elements.unavailable);
  } catch {
    showOnly(elements.unavailable);
  }
}

function resetForm() {
  currentInvitationId = null;
  elements.result.classList.add("hidden");
  elements.form.classList.remove("hidden");
  elements.name.value = "";
  elements.email.value = "";
  elements.link.value = "";
  setFeedback("");
  elements.name.focus();
}

elements.form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const inviteeName = elements.name.value.trim();
  const inviteeEmail = elements.email.value.trim();
  if (!inviteeName || !inviteeEmail) {
    setFeedback("Enter the tester's name and email address.");
    return;
  }

  elements.create.disabled = true;
  setFeedback("Creating invitation…");
  try {
    const { response, payload } = await request("issue", {
      inviteeName,
      inviteeEmail,
    });
    if (
      !response.ok ||
      !payload ||
      payload.ok !== true ||
      typeof payload.inviteUrl !== "string" ||
      !payload.inviteUrl.startsWith(
        "https://app.cuevion.com/#tester_invite=",
      ) ||
      !payload.invite ||
      typeof payload.invite.invitationId !== "string"
    ) {
      const code = payload?.error?.code;
      if (code === "live_invitation_exists") {
        setFeedback("An active invitation already exists for this email.");
      } else if (code === "account_already_provisioned") {
        setFeedback("This email already has Cuevion access.");
      } else {
        setFeedback("The tester invitation could not be created.");
      }
      return;
    }

    currentInvitationId = payload.invite.invitationId;
    elements.resultName.textContent =
      "Invite ready for " + payload.invite.inviteeName;
    elements.resultMeta.textContent =
      payload.invite.inviteeEmail +
      " · expires " +
      new Date(payload.invite.expiresAt).toLocaleString();
    elements.link.value = payload.inviteUrl;
    elements.form.classList.add("hidden");
    elements.result.classList.remove("hidden");
    setFeedback("Tester invitation created.");
  } catch {
    setFeedback("Tester Access is temporarily unavailable.");
  } finally {
    elements.create.disabled = false;
  }
});

elements.copy.addEventListener("click", async () => {
  if (!elements.link.value) return;
  try {
    await navigator.clipboard.writeText(elements.link.value);
    setFeedback("Invite link copied.");
  } catch {
    elements.link.focus();
    elements.link.select();
    setFeedback("Copy failed. Copy the selected link manually.");
  }
});

elements.revoke.addEventListener("click", async () => {
  if (!currentInvitationId) return;
  elements.revoke.disabled = true;
  setFeedback("Revoking invitation…");
  try {
    const { response, payload } = await request("cancel", {
      invitationId: currentInvitationId,
    });
    if (!response.ok || !payload || payload.ok !== true) {
      setFeedback("The tester invitation could not be revoked.");
      return;
    }
    resetForm();
    setFeedback("Tester invitation revoked.");
  } catch {
    setFeedback("Tester Access is temporarily unavailable.");
  } finally {
    elements.revoke.disabled = false;
  }
});

elements.next.addEventListener("click", resetForm);
elements.retry.addEventListener("click", loadCapability);

void loadCapability();
