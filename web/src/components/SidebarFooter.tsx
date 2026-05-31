import { useState } from "react";
import { LogOut, User } from "lucide-react";
import { Typography } from "@/components/NouiTypography";
import { useSidebarStatus } from "@/hooks/useSidebarStatus";
import { cn } from "@/lib/utils";
import { useI18n } from "@/i18n";
import { useAuth } from "@/contexts/AuthContext";

export function SidebarFooter() {
  const status = useSidebarStatus();
  const { t } = useI18n();
  const { user, logout } = useAuth();
  const [loggingOut, setLoggingOut] = useState(false);

  const handleLogout = async () => {
    setLoggingOut(true);
    try {
      await logout();
    } finally {
      setLoggingOut(false);
    }
  };

  return (
    <div
      className={cn(
        "flex shrink-0 flex-col gap-1",
        "border-t border-current/10",
      )}
    >
      {/* User info row */}
      {user && (
        <div
          className={cn(
            "flex items-center justify-between gap-2",
            "px-5 pt-2.5 pb-1",
          )}
        >
          <div className="flex min-w-0 items-center gap-2">
            <User className="h-3.5 w-3.5 shrink-0 text-text-secondary" />
            <div className="flex min-w-0 flex-col">
              <Typography className="font-mono-ui text-xs truncate text-text-secondary">
                {user.display_name || user.username}
              </Typography>
              <Typography className="font-mondwest text-display text-[0.625rem] tracking-[0.1em] text-text-tertiary uppercase">
                {user.role}
              </Typography>
            </div>
          </div>
          <button
            onClick={handleLogout}
            disabled={loggingOut}
            className={cn(
              "shrink-0 rounded p-1",
              "text-text-tertiary hover:text-midground",
              "transition-colors",
              "focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-midground/40",
            )}
            title="Sign out"
          >
            <LogOut className="h-3.5 w-3.5" />
          </button>
        </div>
      )}

      {/* Version + org row */}
      <div
        className={cn(
          "flex shrink-0 items-center justify-between gap-2",
          "px-5 py-2.5",
        )}
      >
        <Typography className="font-mono-ui text-xs tabular-nums tracking-[0.08em] text-text-tertiary lowercase">
          {status?.version != null ? `v${status.version}` : "—"}
        </Typography>

        <a
          href="https://nousresearch.com"
          target="_blank"
          rel="noopener noreferrer"
          className={cn(
            "font-mondwest text-display text-xs tracking-[0.12em] text-midground",
            "transition-opacity hover:opacity-90",
            "focus-visible:rounded-sm focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-midground/40",
          )}
          style={{ mixBlendMode: "plus-lighter" }}
        >
          {t.app.footer.org}
        </a>
      </div>
    </div>
  );
}
