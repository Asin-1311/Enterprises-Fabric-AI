// Sign-in screen with the usual companions: create account, forgot password, and the
// "set a new password" step reached from a reset link (/?reset=<token>).
//
// The browser only collects the details; every check (password, rules, roles) is made
// by the API. New accounts are always staff with no client engagements, and only when the
// firm has switched self-registration on (it is off by default).

import { ArrowLeft, BookOpen, Eye, EyeOff, Loader2 } from "lucide-react";
import { useEffect, useId, useState, type FormEvent, type ReactNode } from "react";
import { API_DOWN_MESSAGE, ApiError, forgotPassword, getAuthConfig, login, register, resetPassword } from "../api";
import { startSignIn, type OidcConfig } from "../lib/oidc";
import type { SessionInfo } from "../types";

type Mode = "signin" | "register" | "forgot" | "reset";

const MIN_PASSWORD = 8;
export const REGISTRATION_CLOSED = "Accounts are created by the firm administrator. Ask them to add you.";

const TEXT: Record<Mode, { title: string; subtitle: string; submit: string; busy: string }> = {
  signin: { title: "Sign in", subtitle: "Use your firm email address and password.", submit: "Sign in", busy: "Signing in…" },
  register: { title: "Create account", subtitle: "Set up access to the firm's knowledge base.", submit: "Create account", busy: "Creating account…" },
  forgot: { title: "Reset your password", subtitle: "Enter your email and we'll send you a link to set a new password.", submit: "Send reset link", busy: "Sending…" },
  reset: { title: "Choose a new password", subtitle: `Use at least ${MIN_PASSWORD} characters.`, submit: "Set new password", busy: "Saving…" },
};

interface Props {
  onSignedIn: (session: SessionInfo) => void;
  resetToken?: string | null;
  initialError?: string | null; // e.g. a refused single sign-on (M16c)
}

export function LoginScreen({ onSignedIn, resetToken = null, initialError = null }: Props) {
  const [mode, setMode] = useState<Mode>(resetToken ? "reset" : "signin");
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(initialError);
  const [oidc, setOidc] = useState<OidcConfig | null>(null); // full profile: Keycloak signs users in
  const [notice, setNotice] = useState<string | null>(null);
  const [selfRegistration, setSelfRegistration] = useState(false); // off until the API says on

  useEffect(() => {
    getAuthConfig()
      .then((config) => {
        setSelfRegistration(config.self_registration);
        if (config.mode === "oidc" && config.oidc_issuer && config.oidc_client_id) {
          setOidc({ issuer: config.oidc_issuer, clientId: config.oidc_client_id });
        }
      })
      .catch(() => setSelfRegistration(false));
  }, []);

  function createAccount() {
    if (selfRegistration) go("register");
    else {
      setError(null);
      setNotice(REGISTRATION_CLOSED);
    }
  }

  function go(next: Mode, message: string | null = null) {
    setMode(next);
    setError(null);
    setNotice(message);
    setPassword("");
    setConfirm("");
  }

  /** Checks the browser can make before asking the API (the API checks again). */
  function localProblem(): string | null {
    if ((mode === "register" || mode === "reset") && password.length < MIN_PASSWORD) {
      return `Use at least ${MIN_PASSWORD} characters for the password.`;
    }
    if ((mode === "register" || mode === "reset") && password !== confirm) {
      return "The two passwords do not match.";
    }
    return null;
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    const problem = localProblem();
    if (problem) {
      setError(problem);
      return;
    }
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      if (mode === "signin") onSignedIn(await login(email, password));
      else if (mode === "register") onSignedIn(await register(name, email, password));
      else if (mode === "forgot") {
        const { message } = await forgotPassword(email);
        setNotice(`${message} (Demo: there is no email server, so the link appears in the API window.)`);
      } else if (resetToken) {
        await resetPassword(resetToken, password);
        window.history.replaceState(null, "", window.location.pathname); // drop ?reset=
        go("signin", "Your password has been changed. Sign in with your new password.");
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.message : API_DOWN_MESSAGE);
    } finally {
      setBusy(false);
    }
  }

  async function singleSignOn(config: OidcConfig) {
    setBusy(true);
    try {
      window.location.assign(await startSignIn(config));
    } catch {
      setBusy(false);
      setError("The sign-in page could not be opened.");
    }
  }

  if (oidc) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-gradient-to-br from-slate-50 via-white to-indigo-50 px-4 py-10 text-slate-900">
        <main className="w-full max-w-md rounded-2xl border border-slate-200 bg-white p-6 shadow-sm sm:p-8">
          <div className="mb-4 flex items-center gap-2 text-lg font-semibold">
            <BookOpen size={20} className="text-indigo-600" aria-hidden /> Enterprise Fabric AI
          </div>
          <h1 className="text-xl font-semibold">Sign in</h1>
          <p className="mt-1 text-sm text-slate-500">
            The firm signs you in with its identity provider (single sign-on). Your role and engagements come from there.
          </p>
          {error && <p role="alert" className="mt-4 rounded-lg bg-rose-50 px-3 py-2 text-sm text-rose-700">{error}</p>}
          <button type="button" disabled={busy} onClick={() => void singleSignOn(oidc)}
            className="mt-6 flex w-full items-center justify-center gap-2 rounded-lg bg-indigo-600 px-4 py-2.5 text-sm font-semibold text-white hover:bg-indigo-700 disabled:bg-indigo-400">
            {busy && <Loader2 size={16} className="animate-spin" aria-hidden />} Continue to firm sign-in
          </button>
        </main>
      </div>
    );
  }

  const text = TEXT[mode];
  return (
    <div className="flex min-h-screen items-center justify-center bg-gradient-to-br from-slate-50 via-white to-indigo-50 px-4 py-10 text-slate-900">
      <div className="w-full max-w-md">
        <div className="mb-8 flex flex-col items-center text-center">
          <div className="grid h-12 w-12 place-items-center rounded-2xl bg-indigo-600 text-white shadow-sm">
            <BookOpen size={24} aria-hidden />
          </div>
          <div className="mt-3 text-lg font-semibold">Enterprise Fabric AI</div>
          <div className="text-sm text-slate-500">Cited answers for audit teams</div>
        </div>

        <main className="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm sm:p-8">
          <h1 className="text-xl font-semibold">{text.title}</h1>
          <p className="mt-1 text-sm text-slate-500">{text.subtitle}</p>

          {notice && <p role="status" className="mt-4 rounded-lg bg-emerald-50 px-3 py-2 text-sm text-emerald-800">{notice}</p>}
          {error && <p role="alert" className="mt-4 rounded-lg bg-rose-50 px-3 py-2 text-sm text-rose-700">{error}</p>}

          <form className="mt-6 space-y-4" onSubmit={(e) => void submit(e)}>
            {mode === "register" && (
              <Field label="Full name">
                {(id) => <input id={id} type="text" required autoComplete="name" value={name}
                  onChange={(e) => setName(e.target.value)} className={INPUT} />}
              </Field>
            )}

            {mode !== "reset" && (
              <Field label="Email">
                {(id) => <input id={id} type="email" required autoComplete="email" value={email}
                  onChange={(e) => setEmail(e.target.value)} placeholder="name@firm.com" className={INPUT} />}
              </Field>
            )}

            {mode !== "forgot" && (
              <PasswordField
                label={mode === "signin" ? "Password" : "New password"}
                value={password}
                onChange={setPassword}
                autoComplete={mode === "signin" ? "current-password" : "new-password"}
              />
            )}

            {(mode === "register" || mode === "reset") && (
              <PasswordField label="Confirm password" value={confirm} onChange={setConfirm} autoComplete="new-password" />
            )}

            {mode === "signin" && (
              <div className="flex items-center justify-between text-sm">
                <button type="button" onClick={() => go("forgot")} className={LINK}>Forgot password?</button>
                <button type="button" onClick={createAccount} className={LINK}>Create account</button>
              </div>
            )}

            {mode === "register" && (
              <p className="text-xs text-slate-500">
                New accounts can see firm-wide staff material. Access to client engagement files is
                assigned by the firm.
              </p>
            )}

            <button type="submit" disabled={busy}
              className="flex w-full items-center justify-center gap-2 rounded-lg bg-indigo-600 px-4 py-2.5 text-sm font-semibold text-white shadow-sm hover:bg-indigo-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-300 disabled:bg-indigo-400">
              {busy && <Loader2 size={16} className="animate-spin" aria-hidden />}
              {busy ? text.busy : text.submit}
            </button>
          </form>

          {mode !== "signin" && (
            <p className="mt-6 text-center text-sm text-slate-500">
              {mode === "register" && <>Already have an account? </>}
              <button type="button" onClick={() => go("signin")} className={`${LINK} inline-flex items-center gap-1`}>
                {mode !== "register" && <ArrowLeft size={14} aria-hidden />}
                {mode === "register" ? "Sign in" : "Back to sign in"}
              </button>
            </p>
          )}
        </main>

        <p className="mt-6 text-center text-[11px] leading-snug text-slate-400">
          SYNTHETIC demonstration for an internship project: fictional firm, clients and people.
        </p>
      </div>
    </div>
  );
}

const INPUT =
  "w-full rounded-lg border border-slate-300 bg-white px-3 py-2.5 text-sm shadow-sm placeholder:text-slate-400 focus:border-indigo-400 focus:outline-none focus:ring-2 focus:ring-indigo-100";
const LINK = "font-medium text-indigo-600 hover:text-indigo-700 hover:underline focus:outline-none focus-visible:underline";

/** A label above a single input; `children` receives the id that links them. */
function Field({ label, children }: { label: string; children: (id: string) => ReactNode }) {
  const id = useId();
  return (
    <div>
      <label htmlFor={id} className="mb-1.5 block text-sm font-medium text-slate-700">{label}</label>
      {children(id)}
    </div>
  );
}

interface PasswordFieldProps {
  label: string;
  value: string;
  onChange: (value: string) => void;
  autoComplete: string;
}

function PasswordField({ label, value, onChange, autoComplete }: PasswordFieldProps) {
  const [visible, setVisible] = useState(false);
  return (
    <Field label={label}>
      {(id) => (
        <div className="relative">
          <input id={id} type={visible ? "text" : "password"} required autoComplete={autoComplete}
            value={value} onChange={(e) => onChange(e.target.value)} className={`${INPUT} pr-10`} />
          <button type="button" onClick={() => setVisible((v) => !v)}
            aria-label={visible ? `Hide ${label.toLowerCase()}` : `Show ${label.toLowerCase()}`}
            className="absolute inset-y-0 right-0 grid w-10 place-items-center text-slate-400 hover:text-slate-600">
            {visible ? <EyeOff size={16} aria-hidden /> : <Eye size={16} aria-hidden />}
          </button>
        </div>
      )}
    </Field>
  );
}
