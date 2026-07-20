import {
  AUTH0_LOGIN_ENDPOINT,
  hasAuthCallbackError,
} from "../../lib/authApi";

export function Auth0LoginView() {
  const showCallbackError =
    typeof window !== "undefined" && hasAuthCallbackError(window.location.search);

  return (
    <main className="min-h-screen bg-[linear-gradient(180deg,#f6efe7_0%,#efe5da_100%)] px-6 py-10 text-[color:#2f2a24] dark:bg-[linear-gradient(180deg,#171411_0%,#221c17_100%)] dark:text-[color:#f1e9de]">
      <div className="mx-auto flex min-h-[calc(100vh-5rem)] max-w-[560px] items-center justify-center">
        <section className="w-full rounded-[32px] border border-[rgba(120,104,89,0.14)] bg-[rgba(255,252,247,0.82)] p-8 shadow-[0_28px_80px_rgba(61,44,32,0.12)] backdrop-blur dark:border-[rgba(255,255,255,0.08)] dark:bg-[rgba(33,28,24,0.82)]">
          <div className="text-center">
            <div className="mx-auto mb-6 flex h-14 w-14 items-center justify-center rounded-full bg-[#264238] shadow-[inset_0_1px_0_rgba(255,255,255,0.14),0_14px_28px_rgba(38,66,56,0.18)]">
              <span
                aria-hidden="true"
                className="h-4 w-4 rounded-full bg-[rgba(248,247,242,0.98)]"
              />
            </div>
            <div className="text-[0.72rem] font-medium uppercase tracking-[0.22em] text-[rgba(120,104,89,0.7)] dark:text-[rgba(214,201,189,0.64)]">
              Cuevion
            </div>
            <h1 className="mt-3 text-[1.8rem] font-medium tracking-[-0.04em]">
              Sign in to Cuevion
            </h1>
            <p className="mx-auto mt-4 max-w-[25rem] text-[0.96rem] leading-7 text-[rgba(88,80,71,0.84)] dark:text-[rgba(222,211,200,0.76)]">
              We&apos;ll send a secure sign-in code to your email address.
            </p>
          </div>

          {showCallbackError ? (
            <div
              role="alert"
              className="mt-6 rounded-[18px] border border-[rgba(132,77,63,0.16)] bg-[rgba(245,228,220,0.56)] px-4 py-3 text-center text-[0.84rem] leading-6 text-[rgba(132,77,63,0.94)] dark:border-[rgba(244,186,168,0.14)] dark:bg-[rgba(92,54,45,0.28)] dark:text-[rgba(244,186,168,0.84)]"
            >
              Sign-in could not be completed. Please try again.
            </div>
          ) : null}

          <div className="mt-7 flex justify-center">
            <button
              type="button"
              onClick={() => window.location.assign(AUTH0_LOGIN_ENDPOINT)}
              className="inline-flex h-11 items-center justify-center rounded-full border border-[rgba(218,194,142,0.56)] bg-[linear-gradient(180deg,rgba(237,222,184,0.98),rgba(199,166,104,0.96))] px-6 text-[0.74rem] font-semibold uppercase tracking-[0.15em] text-[rgba(29,58,48,0.96)] shadow-[inset_0_1px_0_rgba(255,252,240,0.66),inset_0_-1px_0_rgba(119,82,38,0.14),0_10px_22px_rgba(15,36,30,0.18)] transition-[background-image,border-color,transform,box-shadow] duration-150 hover:-translate-y-px hover:border-[rgba(231,207,156,0.66)] hover:bg-[linear-gradient(180deg,rgba(242,228,192,0.98),rgba(184,149,88,0.98))] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[rgba(38,66,56,0.32)] focus-visible:ring-offset-2 active:translate-y-0 active:scale-[0.99]"
            >
              Sign in with email
            </button>
          </div>
        </section>
      </div>
    </main>
  );
}
