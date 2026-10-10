import { useState, type FormEvent } from "react";
import { Navigate, useNavigate } from "react-router-dom";
import { Button, Card, Field } from "../components/ui";
import { useAuth } from "../hooks/useAuth";

/**
 * Operator sign-in.
 *
 * Reached only when the API has refused a request, so it is deliberately
 * quiet: no marketing copy, no feature list, one thing to do.
 *
 * When the deployment has no operator configured (APP_ADMIN_PASSWORD unset)
 * it says so instead of showing a password box that can never succeed.
 */
export function LoginPage() {
  const { user, signIn, enabled, loading } = useAuth();
  const navigate = useNavigate();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    if (submitting) return;
    setError(null);
    setSubmitting(true);
    try {
      const result = await signIn(username.trim(), password);
      if (result.ok) {
        // The session cookie is set and the provider now holds a user. Without
        // this the URL stays on /login and the screen never changes, which
        // looks exactly like a failed login.
        navigate("/", { replace: true });
      } else {
        setError(result.message ?? "We couldn't sign you in. Please try again.");
        // Clear only the password: the username is almost certainly right and
        // retyping it is pure friction.
        setPassword("");
      }
    } finally {
      setSubmitting(false);
    }
  }

  // Already signed in (or the session came back from a cookie): go to the app.
  if (loading) {
    return (
      <div className="auth-page">
        <Card className="auth-card">
          <p className="auth-card__status">Checking your session…</p>
        </Card>
      </div>
    );
  }

  if (user) {
    return <Navigate to="/" replace />;
  }

  return (
    <div className="auth-page">
      <Card className="auth-card">
        <div className="auth-card__brand">
          <span className="auth-card__mark" aria-hidden="true" />
          <div>
            <h1 className="auth-card__title">Social Media Automation</h1>
            <p className="auth-card__subtitle">Sign in to continue</p>
          </div>
        </div>

        {!enabled ? (
          <div className="auth-card__notice" role="status">
            <p className="auth-card__notice-title">Login is not configured</p>
            <p>
              This deployment has no operator account. Set{" "}
              <code>APP_ADMIN_PASSWORD</code> in the backend environment and
              restart the service, then reload this page.
            </p>
          </div>
        ) : (
          <form className="auth-form" onSubmit={onSubmit} noValidate>
            <Field label="Username" htmlFor="auth-username">
              <input
                id="auth-username"
                className="input"
                type="text"
                name="username"
                autoComplete="username"
                autoCapitalize="none"
                autoCorrect="off"
                spellCheck={false}
                required
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                disabled={submitting}
              />
            </Field>

            <Field label="Password" htmlFor="auth-password" error={error}>
              <input
                id="auth-password"
                className="input"
                type="password"
                name="password"
                autoComplete="current-password"
                required
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                disabled={submitting}
              />
            </Field>

            <Button
              type="submit"
              variant="primary"
              size="lg"
              fullWidth
              loading={submitting}
            >
              {submitting ? "Signing in…" : "Sign in"}
            </Button>
          </form>
        )}
      </Card>
    </div>
  );
}