export type DesktopComposeBodyDraft = {
  getBodyHtml: () => string;
  recordInput: (bodyHtml: string) => void;
  replaceBodyHtml: (bodyHtml: string) => void;
};

export function createDesktopComposeBodyDraft(
  initialBodyHtml: string,
): DesktopComposeBodyDraft {
  let bodyHtml = initialBodyHtml;

  return {
    getBodyHtml: () => bodyHtml,
    recordInput: (nextBodyHtml) => {
      bodyHtml = nextBodyHtml;
    },
    replaceBodyHtml: (nextBodyHtml) => {
      bodyHtml = nextBodyHtml;
    },
  };
}
