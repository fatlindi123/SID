import { useEffect, useState, type FormEvent } from "react";
import { apiRequest, ensureCsrf } from "../apiClient";

type AuthConfiguration = {
  password_recovery_available: boolean;
};

const DEFAULT_CONFIGURATION: AuthConfiguration = {
  password_recovery_available: false,
};

function safeNextPath(): string | null {
  const value = new URLSearchParams(window.location.search).get("next");
  if (!value) return null;
  if (!value.startsWith("/") || value.startsWith("//") || value.includes("\\")) return "/";
  try {
    const parsed = new URL(value, window.location.origin);
    if (parsed.origin !== window.location.origin) return "/";
    if (["/login", "/register", "/forgot-password", "/reset-password", "/welcome"].includes(parsed.pathname)) return "/";
    return parsed.pathname + parsed.search + parsed.hash;
  } catch {
    return "/";
  }
}

function passwordMeetsMinimum(value: string): boolean {
  return value.length >= 7 && value.length <= 128;
}

export function AuthExperience({ route }: { route: string }) {
  const [configuration, setConfiguration] = useState(DEFAULT_CONFIGURATION);
  const [configurationReady, setConfigurationReady] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [showPassword, setShowPassword] = useState(false);
  const [showConfirmation, setShowConfirmation] = useState(false);

  useEffect(() => {
    let active = true;
    void (async () => {
      try {
        await ensureCsrf();
        const config = await apiRequest<AuthConfiguration>("/auth/configuration");
        if (active) setConfiguration(config);
      } catch (reason) {
        if (active) setError(reason instanceof Error ? reason.message : "Authentication is unavailable.");
      } finally {
        if (active) setConfigurationReady(true);
      }
    })();
    return () => { active = false; };
  }, []);

  const handleLogin = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const values = new FormData(event.currentTarget);
    const password = String(values.get("password") || "");
    setBusy(true); setError(null); setMessage(null);
    try {
      const result = await apiRequest<{ next: string }>("/auth/login", {
        method: "POST",
        body: JSON.stringify({ email: values.get("email"), password, next: safeNextPath() }),
      });
      window.location.replace(result.next || "/");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Sign in could not be completed.");
    } finally {
      setBusy(false);
    }
  };

  const handleRegister = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const values = new FormData(event.currentTarget);
    const password = String(values.get("password") || "");
    const confirmPassword = String(values.get("confirm_password") || "");
    if (!passwordMeetsMinimum(password)) { setError("Use a password with at least 7 characters."); return; }
    if (password !== confirmPassword) { setError("Passwords do not match."); return; }
    setBusy(true); setError(null); setMessage(null);
    try {
      const result = await apiRequest<{ next: string }>("/auth/register", {
        method: "POST",
        body: JSON.stringify({
          full_name: values.get("full_name"), email: values.get("email"), password,
          confirm_password: confirmPassword, next: safeNextPath(),
        }),
      });
      window.location.replace(result.next || "/welcome");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Account registration could not be completed.");
    } finally { setBusy(false); }
  };

  const handleForgot = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const values = new FormData(event.currentTarget);
    setBusy(true); setError(null); setMessage(null);
    try {
      const result = await apiRequest<{ message: string }>("/auth/forgot-password", {
        method: "POST", body: JSON.stringify({ email: values.get("email") }),
      });
      setMessage(result.message);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "The request could not be completed.");
    } finally { setBusy(false); }
  };

  const handleReset = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const token = new URLSearchParams(window.location.search).get("token");
    const values = new FormData(event.currentTarget);
    const password = String(values.get("password") || "");
    const confirmPassword = String(values.get("confirm_password") || "");
    if (!token) { setError("This password reset link is invalid or incomplete."); return; }
    if (!passwordMeetsMinimum(password)) { setError("Use a password with at least 7 characters."); return; }
    if (password !== confirmPassword) { setError("Passwords do not match."); return; }
    setBusy(true); setError(null); setMessage(null);
    try {
      const result = await apiRequest<{ message: string }>("/auth/reset-password", {
        method: "POST", body: JSON.stringify({ token, password, confirm_password: confirmPassword }),
      });
      setMessage(result.message);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "The password could not be updated."); }
    finally { setBusy(false); }
  };

  const search = typeof window === "undefined" ? "" : window.location.search;
  const loginRoute = route === "/login";
  const registerRoute = route === "/register";
  const forgotRoute = route === "/forgot-password";
  const resetRoute = route === "/reset-password";
  const title = registerRoute ? "Create your account" : forgotRoute ? "Reset your password" : resetRoute ? "Choose a new password" : "Welcome back";
  const subtitle = registerRoute ? "Start a secure SID workspace." : loginRoute ? "Sign in to your SID workspace." : "Use the secure link sent to your email address.";

  return (
    <main className="auth-shell">
      <section className="auth-story" aria-label="About SID">
        <a className="auth-brand" href="/login" aria-label="SID home"><span className="auth-brand-mark"><i /><i /><i /></span><strong>SID</strong></a>
        <div className="auth-story-copy">
          <p className="auth-eyebrow">SOFTWARE INTELLIGENCE</p>
          <h1>Understand your software.<br /><span>Test with confidence.</span></h1>
          <p>Explore applications, uncover their structure and turn software intelligence into actionable testing workflows.</p>
        </div>
        <div className="auth-visual" aria-hidden="true">
          <div className="auth-visual-toolbar"><span /><span /><span /><i>Application map</i></div>
          <div className="auth-visual-canvas">
            <div className="auth-page-node auth-page-root"><small>PAGE</small><strong>Overview</strong><span className="auth-node-line" /></div>
            <div className="auth-page-node auth-page-child"><small>PAGE</small><strong>Test cases</strong></div>
            <div className="auth-page-node auth-page-third"><small>WORKFLOW</small><strong>Sign in</strong></div>
            <div className="auth-graph-node auth-graph-a" /><div className="auth-graph-node auth-graph-b" /><div className="auth-graph-node auth-graph-c" />
            <svg viewBox="0 0 480 245" preserveAspectRatio="none"><path d="M145 97 C220 97 205 64 278 64"/><path d="M145 113 C218 113 208 166 282 166"/><path d="M332 70 C360 70 360 115 383 115"/><path d="M332 166 C356 166 360 129 383 129"/></svg>
            <div className="auth-visual-caption"><span className="auth-caption-dot" /> Connected pages, behaviors and evidence</div>
          </div>
        </div>
        <p className="auth-story-foot">Evidence first. Clear paths from exploration to verification.</p>
      </section>

      <section className="auth-panel">
        <div className="auth-form-wrap">
          <a className="auth-mobile-brand" href="/login"><span className="auth-brand-mark"><i /><i /><i /></span><strong>SID</strong></a>
          <p className="auth-eyebrow">YOUR WORKSPACE</p>
          <h2>{title}</h2>
          <p className="auth-subtitle">{subtitle}</p>

          {loginRoute && <>
            {!configurationReady && <p className="auth-muted">Checking sign-in availability…</p>}
            <form className="auth-form" onSubmit={handleLogin}>
              <label>Email address<input name="email" type="email" autoComplete="email" placeholder="you@example.com" required maxLength={320} /></label>
              <label>Password<span className="auth-password-wrap"><input name="password" type={showPassword ? "text" : "password"} autoComplete="current-password" required maxLength={128} /><button type="button" aria-label={showPassword ? "Hide password" : "Show password"} onClick={() => setShowPassword((value) => !value)}>{showPassword ? "Hide" : "Show"}</button></span></label>
              <div className="auth-forgot-row"><a href="/forgot-password">Forgot password?</a></div>
              {error && <p className="auth-alert error" role="alert">{error}</p>}
              <button className="auth-submit" type="submit" disabled={busy}>{busy ? "Signing in…" : "Sign in"}</button>
            </form>
            <p className="auth-bottom-link">New to SID? <a href="/register">Create account</a></p>
          </>}

          {registerRoute && <form className="auth-form" onSubmit={handleRegister}>
            <label>Full name<input name="full_name" autoComplete="name" placeholder="Your name" required minLength={1} maxLength={160} /></label>
            <label>Email address<input name="email" type="email" autoComplete="email" placeholder="you@example.com" required maxLength={320} /></label>
            <label>Password<span className="auth-password-wrap"><input name="password" type={showPassword ? "text" : "password"} autoComplete="new-password" required minLength={7} maxLength={128} /><button type="button" aria-label={showPassword ? "Hide password" : "Show password"} onClick={() => setShowPassword((value) => !value)}>{showPassword ? "Hide" : "Show"}</button></span></label>
            <small className="auth-requirement">Use at least 7 characters.</small>
            <label>Confirm password<span className="auth-password-wrap"><input name="confirm_password" type={showConfirmation ? "text" : "password"} autoComplete="new-password" required minLength={7} maxLength={128} /><button type="button" aria-label={showConfirmation ? "Hide confirmation" : "Show confirmation"} onClick={() => setShowConfirmation((value) => !value)}>{showConfirmation ? "Hide" : "Show"}</button></span></label>
            {error && <p className="auth-alert error" role="alert">{error}</p>}
            <button className="auth-submit" type="submit" disabled={busy}>{busy ? "Creating account..." : "Create account"}</button>
            <p className="auth-bottom-link">Already have an account? <a href="/login">Sign in</a></p>
          </form>}

          {forgotRoute && <form className="auth-form" onSubmit={handleForgot}>
            <label>Email address<input name="email" type="email" autoComplete="email" placeholder="you@example.com" required maxLength={320} /></label>
            {configurationReady && !configuration.password_recovery_available && <p className="auth-alert info" role="status">Email delivery is unavailable. Password recovery cannot send a reset link right now.</p>}
            {error && <p className="auth-alert error" role="alert">{error}</p>}{message && <p className="auth-alert success" role="status">{message}</p>}
            <button className="auth-submit" type="submit" disabled={busy || !configuration.password_recovery_available}>{busy ? "Sending…" : "Send reset link"}</button>
            <p className="auth-bottom-link"><a href="/login">Back to sign in</a></p>
          </form>}

          {resetRoute && <form className="auth-form" onSubmit={handleReset}>
            <label>New password<span className="auth-password-wrap"><input name="password" type={showPassword ? "text" : "password"} autoComplete="new-password" required minLength={7} maxLength={128} /><button type="button" aria-label={showPassword ? "Hide password" : "Show password"} onClick={() => setShowPassword((value) => !value)}>{showPassword ? "Hide" : "Show"}</button></span></label>
            <small className="auth-requirement">Use at least 7 characters.</small>
            <label>Confirm new password<span className="auth-password-wrap"><input name="confirm_password" type={showConfirmation ? "text" : "password"} autoComplete="new-password" required minLength={7} maxLength={128} /><button type="button" aria-label={showConfirmation ? "Hide confirmation" : "Show confirmation"} onClick={() => setShowConfirmation((value) => !value)}>{showConfirmation ? "Hide" : "Show"}</button></span></label>
            {error && <p className="auth-alert error" role="alert">{error}</p>}{message && <p className="auth-alert success" role="status">{message}</p>}
            <button className="auth-submit" type="submit" disabled={busy || !new URLSearchParams(search).get("token")}>{busy ? "Updating…" : "Update password"}</button>
            {message && <p className="auth-bottom-link"><a href="/login">Back to sign in</a></p>}
          </form>}

          <div className="auth-secure-note"><span aria-hidden="true">●</span> Your workspace is protected by a secure server-side session.</div>
        </div>
        <footer className="auth-footer"><span>© {new Date().getFullYear()} SID</span><span>Secure access to your software intelligence</span></footer>
      </section>
    </main>
  );
}
