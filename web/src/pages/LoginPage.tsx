import { useState, type FormEvent } from "react";
import { KeyRound, LogIn, Shield, User } from "lucide-react";
import { Button } from "@nous-research/ui/ui/components/button";
import { Typography } from "@/components/NouiTypography";
import { useAuth } from "@/contexts/AuthContext";
import { cn } from "@/lib/utils";

export default function LoginPage() {
  const { login } = useAuth();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (!username.trim() || !password) return;
    setError("");
    setBusy(true);
    try {
      await login(username.trim(), password);
    } catch (err) {
      const msg =
        err instanceof Error ? err.message : "Login failed";
      // Try to extract a readable message from the error
      try {
        const parsed = JSON.parse(msg);
        setError(parsed.detail || msg);
      } catch {
        setError(msg.includes("401") ? "Invalid username or password" : msg);
      }
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex min-h-dvh items-center justify-center bg-black px-4">
      <div
        className={cn(
          "w-full max-w-sm rounded-lg border border-current/20 p-8",
        )}
        style={{
          background: "var(--component-sidebar-background)",
          borderImage: "var(--component-sidebar-border-image)",
        }}
      >
        <div className="mb-6 flex items-center justify-center gap-2">
          <Shield className="h-6 w-6 text-midground" />
          <Typography
            className="font-bold text-[1.125rem] leading-[0.95] tracking-[0.0525rem] text-midground uppercase"
            style={{ mixBlendMode: "plus-lighter" }}
          >
            AgentOps
            <br />
            Control
          </Typography>
        </div>

        <form onSubmit={handleSubmit} className="flex flex-col gap-4">
          <div className="flex flex-col gap-1.5">
            <label
              htmlFor="login-username"
              className="font-mondwest text-display text-xs tracking-[0.1em] text-text-secondary uppercase"
            >
              Username
            </label>
            <div className="relative">
              <User className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-text-tertiary" />
              <input
                id="login-username"
                type="text"
                autoComplete="username"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                className={cn(
                  "w-full rounded border border-current/20 bg-black/40 py-2 pl-10 pr-3",
                  "font-mono-ui text-sm text-text-primary",
                  "placeholder:text-text-tertiary",
                  "focus:border-midground/50 focus:outline-none focus:ring-1 focus:ring-midground/30",
                )}
                placeholder="admin"
              />
            </div>
          </div>

          <div className="flex flex-col gap-1.5">
            <label
              htmlFor="login-password"
              className="font-mondwest text-display text-xs tracking-[0.1em] text-text-secondary uppercase"
            >
              Password
            </label>
            <div className="relative">
              <KeyRound className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-text-tertiary" />
              <input
                id="login-password"
                type="password"
                autoComplete="current-password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className={cn(
                  "w-full rounded border border-current/20 bg-black/40 py-2 pl-10 pr-3",
                  "font-mono-ui text-sm text-text-primary",
                  "placeholder:text-text-tertiary",
                  "focus:border-midground/50 focus:outline-none focus:ring-1 focus:ring-midground/30",
                )}
                placeholder="••••••••"
              />
            </div>
          </div>

          {error && (
            <div
              className="rounded border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-400"
              role="alert"
            >
              {error}
            </div>
          )}

          <Button
            type="submit"
            disabled={busy || !username.trim() || !password}
            className="mt-2 w-full"
          >
            {busy ? (
              <span className="flex items-center gap-2">
                <span className="h-4 w-4 animate-spin rounded-full border-2 border-current border-t-transparent" />
                Signing in…
              </span>
            ) : (
              <span className="flex items-center gap-2">
                <LogIn className="h-4 w-4" />
                Sign In
              </span>
            )}
          </Button>
        </form>
      </div>
    </div>
  );
}
