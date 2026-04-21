export const onboardingCopy = {
  en: {
    brand: {
      name: "Cuevion",
    },
    navigation: {
      back: "Back",
      next: "Next",
      startSetup: "Start setup",
      completeSetup: "Complete setup",
      goToDashboard: "Open workspace",
    },
    sidebar: {
      workspaceSetup: "Workspace Setup",
      description: "Built for modern music workflows.",
      progressLabel: "Setup Progress",
      helperLabel: "Quick note",
      progressStep: (currentStep: number, totalSteps: number) =>
        `Setup Progress Step ${currentStep} of ${totalSteps}`,
      stepHelper: {
        1: "Choose the roles that best reflect how you work day to day.",
        2: "Start with the number of inboxes you want Cuevion to organize.",
        3: "Pick the inboxes that shape how your workflow will be structured.",
        4: "You can fine-tune visibility later as your workflow evolves.",
        5: "Connect each inbox one by one. We'll verify access before setup completes.",
      },
    },
    welcome: {
      title: "Organize your inbox workflow",
      text: "Set up your workspace in a few steps.",
    },
    roles: {
      title: "Choose your role",
      primaryRequired: "Primary role is required.",
      secondaryTitle: "Do you also wear another hat?",
      secondaryOptional: "Secondary role is optional.",
      noSecondaryRole: "No Secondary Role",
      noSecondaryRoleDescription:
        "Keep the workspace configured around one main responsibility.",
      showMoreRoles: "+ Show more roles",
      hideMoreRoles: "+ Hide more roles",
      primary: [
        {
          id: "label_ar_manager",
          label: "Label & A&R Manager",
          description: "Combined demos, releases and artist communication",
        },
        {
          id: "label_manager",
          label: "Label Manager",
          description: "Releases, planning and operations",
        },
        {
          id: "ar_manager",
          label: "A&R Manager",
          description: "Demo intake and artist communication",
        },
        {
          id: "dj",
          label: "DJ",
          description: "Performance, promo and bookings",
        },
        {
          id: "producer",
          label: "Producer",
          description: "Creative work and production",
        },
        {
          id: "dj_producer",
          label: "DJ / Producer",
          description: "Performance, production and music development",
        },
        {
          id: "label_owner",
          label: "Label Owner",
          description: "Strategy and oversight",
        },
      ],
      secondary: [
        {
          id: "label_ar_manager",
          label: "Label & A&R Manager",
          description: "Combined demos, releases and artist communication",
        },
        {
          id: "label_manager",
          label: "Label Manager",
          description: "Releases, planning and operations",
        },
        {
          id: "ar_manager",
          label: "A&R Manager",
          description: "Demo intake and artist communication",
        },
        {
          id: "dj",
          label: "DJ",
          description: "Performance, promo and bookings",
        },
        {
          id: "producer",
          label: "Producer",
          description: "Creative work and production",
        },
        {
          id: "dj_producer",
          label: "DJ / Producer",
          description: "Performance, production and music development",
        },
        {
          id: "label_owner",
          label: "Label Owner",
          description: "Strategy and oversight",
        },
      ],
      extra: [
        {
          id: "legal",
          label: "Legal",
          description: "Contracts, approvals and rights review",
        },
        {
          id: "finance",
          label: "Finance",
          description: "Payments, reporting and cash oversight",
        },
        {
          id: "royalty",
          label: "Royalty",
          description: "Statements, tracking and royalty follow-up",
        },
        {
          id: "sync_licensing",
          label: "Sync / Licensing",
          description: "Placements, licensing and opportunity handling",
        },
        {
          id: "social_media_manager",
          label: "Social Media Manager",
          description: "Content planning and platform coordination",
        },
        {
          id: "promo_manager",
          label: "Promo Manager",
          description: "Promo servicing, outreach and campaign follow-up",
        },
        {
          id: "distribution",
          label: "Distribution",
          description: "Delivery, DSP operations and release logistics",
        },
        {
          id: "admin",
          label: "Admin",
          description: "Workspace administration and operational support",
        },
      ],
    },
    inboxCount: {
      title: "How many inboxes do you want to organize?",
      options: [
        { id: "1", label: "1 inbox" },
        { id: "2", label: "2 inboxes" },
        { id: "3", label: "3 inboxes" },
        { id: "4+", label: "4+" },
        { id: "not_sure", label: "Not sure yet" },
      ],
    },
    inboxSetup: {
      title: "Suggested inbox setup",
      description: "Select the inboxes you want to include. At least 1 inbox is required.",
      limitHint: "To add more inboxes, go back and choose a larger setup.",
      minimumHint: (count: number) => `Select at least ${count} inboxes to continue.`,
      specializedTitle: "Specialized inboxes",
      specializedDescription: "Add specialized inboxes",
      show: "Show",
      hide: "Hide",
      main: [
        { id: "main", label: "Main Inbox" },
        { id: "demo", label: "Demo Inbox" },
        { id: "business", label: "Business Inbox" },
        { id: "promo", label: "Promo Inbox" },
      ],
      specialized: [
        { id: "legal", label: "Legal Inbox" },
        { id: "finance", label: "Finance Inbox" },
        { id: "royalty", label: "Royalty Inbox" },
        { id: "sync", label: "Sync / Licensing" },
      ],
      defaultBadge: "Default",
    },
    workflowStyle: {
      title: "Workflow style",
      description:
        "This affects how updates, reminders and important mail appear. You can adjust details later.",
      recommended: "Recommended",
      options: [
        {
          id: "quiet",
          label: "Quiet",
          description: "Only essential items stay visible",
          tooltip: "Low-noise mode with only essential visibility.",
        },
        {
          id: "balanced",
          label: "Balanced",
          description: "Recommended for most users",
          recommended: true,
          tooltip: "Balanced visibility for most workflows.",
        },
        {
          id: "active",
          label: "Active",
          description: "Show more updates and reminders",
          tooltip: "Higher visibility for reminders and updates.",
        },
      ],
    },
    connect: {
      title: "Connect your inboxes",
      description: "Add provider details for each selected inbox.",
      inboxHint: "Choose a provider and enter the email for this inbox.",
      email: "Email",
      connectInbox: "Connect inbox",
      continueWithGoogle: "Continue with Google",
      testingConnection: "Testing connection...",
      connected: "Connected",
      notConnected: "Not connected",
      oauthRequired: "OAuth required",
      waitingForAuthentication: "Waiting for authentication",
      authenticatedPendingActivation: "Authenticated pending activation",
      connectionFailed: "Connection failed",
      invalidEmail: "Enter a valid email address",
      incorrectPassword: "Password is incorrect",
      invalidHost: "Host address is invalid",
      couldNotConnect: "Could not connect to server",
      connectionTimedOut: "Connection timed out",
      googleOAuthTitle: "Google connects with OAuth",
      googleOAuthDescription:
        "Use secure Google authentication for Gmail and Google Workspace. Manual IMAP passwords are no longer used in this path.",
      googleOAuthPending:
        "Authentication will continue in Google once the runtime OAuth endpoint is available.",
      googleOAuthActivationPending:
        "Google authentication completed. Tokens are stored only in the current server runtime. Final mailbox activation requires durable secure mailbox token storage.",
      reusePreviousServerSettings: "Reuse previous server settings",
      reuse: "Reuse",
      host: "Host",
      port: "Port",
      username: "Username",
      password: "Password",
      ssl: "SSL",
      providers: [
        { id: "google", label: "Gmail / Google Workspace (OAuth)" },
        { id: "microsoft", label: "Microsoft 365 / Outlook" },
        { id: "icloud", label: "iCloud" },
        { id: "yahoo", label: "Yahoo" },
        { id: "custom_imap", label: "Custom IMAP" },
      ],
    },
    complete: {
      badge: "Setup Complete",
      title: "Your workspace is ready",
      text: "Your inbox setup is complete. We’ll now organize your workflow.",
      summary: (count: number) => `${count} inboxes connected · workflow configured`,
      sidebarText:
        "Your workspace is ready to use. Everything is configured and can be adjusted later.",
      sidebarLabel: "Setup Complete",
    },
  },
} as const;

export const onboardingText = onboardingCopy.en;
