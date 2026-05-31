import { useCallback, useEffect, useState } from "react";
import {
  Ban,
  CheckCircle,
  Plus,
  RefreshCw,
  Shield,
  User,
  UserCog,
  XCircle,
} from "lucide-react";
import { Button } from "@nous-research/ui/ui/components/button";
import { Typography } from "@/components/NouiTypography";
import { cn } from "@/lib/utils";
import { api, type ControlUser } from "@/lib/api";

export default function UsersPage() {
  const [users, setUsers] = useState<ControlUser[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [showCreate, setShowCreate] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const data = await api.cpListUsers({ limit: 200 });
      setUsers(data);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load users");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-center justify-between gap-4">
        <div className="flex items-center gap-3">
          <UserCog className="h-5 w-5 text-midground" />
          <Typography className="font-mondwest text-display text-lg tracking-[0.1em] text-text-primary uppercase">
            Users
          </Typography>
        </div>
        <div className="flex items-center gap-2">
          <Button ghost size="sm" onClick={load} disabled={loading}>
            <RefreshCw className={cn("h-4 w-4", loading && "animate-spin")} />
          </Button>
          <Button size="sm" onClick={() => setShowCreate(true)}>
            <Plus className="h-4 w-4" />
            <span className="ml-1">New User</span>
          </Button>
        </div>
      </div>

      {error && (
        <div className="rounded border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-400">
          {error}
        </div>
      )}

      {showCreate && (
        <CreateUserDialog
          onClose={() => setShowCreate(false)}
          onCreated={() => {
            setShowCreate(false);
            load();
          }}
        />
      )}

      <div className="overflow-x-auto rounded border border-current/10">
        <table className="w-full text-left text-sm">
          <thead>
            <tr className="border-b border-current/10 bg-[var(--component-header-background)]">
              <th className="px-4 py-2 font-mono-ui text-xs text-text-tertiary">Username</th>
              <th className="px-4 py-2 font-mono-ui text-xs text-text-tertiary">Display Name</th>
              <th className="px-4 py-2 font-mono-ui text-xs text-text-tertiary">Role</th>
              <th className="px-4 py-2 font-mono-ui text-xs text-text-tertiary">Status</th>
              <th className="px-4 py-2 font-mono-ui text-xs text-text-tertiary">Last Login</th>
              <th className="px-4 py-2 font-mono-ui text-xs text-text-tertiary w-10" />
            </tr>
          </thead>
          <tbody>
            {users.map((u) => (
              <UserRow key={u.id} user={u} onUpdate={load} />
            ))}
            {users.length === 0 && !loading && (
              <tr>
                <td colSpan={6} className="px-4 py-8 text-center text-text-tertiary">
                  No users found
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function UserRow({ user: u, onUpdate }: { user: ControlUser; onUpdate: () => void }) {
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState(false);

  const toggleStatus = async () => {
    setBusy(true);
    try {
      const newStatus = u.status === "active" ? "disabled" : "active";
      await api.cpUpdateUser(u.id, { status: newStatus });
      onUpdate();
    } catch {
      // silently fail — onUpdate won't be called
    } finally {
      setBusy(false);
    }
  };

  const isAdmin = u.role === "admin";
  const isSystem = u.role === "system";

  return (
    <>
      <tr className="border-b border-current/5 hover:bg-white/5 transition-colors">
        <td className="px-4 py-2.5">
          <div className="flex items-center gap-2">
            {isAdmin ? (
              <Shield className="h-3.5 w-3.5 text-midground" />
            ) : isSystem ? (
              <Ban className="h-3.5 w-3.5 text-text-tertiary" />
            ) : (
              <User className="h-3.5 w-3.5 text-text-secondary" />
            )}
            <span className="font-mono-ui text-text-primary">{u.username}</span>
          </div>
        </td>
        <td className="px-4 py-2.5 text-text-secondary">{u.display_name || "—"}</td>
        <td className="px-4 py-2.5">
          <span
            className={cn(
              "font-mono-ui text-xs px-2 py-0.5 rounded uppercase",
              isAdmin
                ? "bg-midground/10 text-midground"
                : isSystem
                  ? "bg-text-tertiary/10 text-text-tertiary"
                  : "bg-text-secondary/10 text-text-secondary",
            )}
          >
            {u.role}
          </span>
        </td>
        <td className="px-4 py-2.5">
          <span
            className={cn(
              "font-mono-ui text-xs px-2 py-0.5 rounded flex items-center gap-1 w-fit",
              u.status === "active"
                ? "bg-green-500/10 text-green-400"
                : "bg-red-500/10 text-red-400",
            )}
          >
            {u.status === "active" ? (
              <CheckCircle className="h-3 w-3" />
            ) : (
              <XCircle className="h-3 w-3" />
            )}
            {u.status}
          </span>
        </td>
        <td className="px-4 py-2.5 text-text-tertiary font-mono-ui text-xs">
          {u.last_login_at
            ? new Date(u.last_login_at).toLocaleDateString()
            : "—"}
        </td>
        <td className="px-4 py-2.5">
          <div className="flex items-center gap-1">
            {!isSystem && (
              <>
                <Button
                  ghost
                  size="icon"
                  className="h-7 w-7"
                  onClick={toggleStatus}
                  disabled={busy}
                  title={u.status === "active" ? "Disable" : "Enable"}
                >
                  {u.status === "active" ? (
                    <Ban className="h-3.5 w-3.5 text-red-400" />
                  ) : (
                    <CheckCircle className="h-3.5 w-3.5 text-green-400" />
                  )}
                </Button>
                <Button
                  ghost
                  size="icon"
                  className="h-7 w-7"
                  onClick={() => setEditing(true)}
                  title="Edit"
                >
                  <UserCog className="h-3.5 w-3.5 text-text-secondary" />
                </Button>
              </>
            )}
          </div>
        </td>
      </tr>
      {editing && (
        <EditUserDialog
          user={u}
          onClose={() => setEditing(false)}
          onUpdated={() => {
            setEditing(false);
            onUpdate();
          }}
        />
      )}
    </>
  );
}

function CreateUserDialog({
  onClose,
  onCreated,
}: {
  onClose: () => void;
  onCreated: () => void;
}) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [role, setRole] = useState<"user" | "admin">("user");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!username.trim() || password.length < 8) return;
    setError("");
    setBusy(true);
    try {
      await api.cpCreateUser({
        username: username.trim(),
        password,
        role,
        display_name: displayName.trim() || undefined,
      });
      onCreated();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create user");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="rounded border border-current/20 bg-black p-6">
      <Typography className="font-mondwest text-display text-sm tracking-[0.1em] text-text-primary uppercase mb-4">
        Create User
      </Typography>
      <form onSubmit={handleSubmit} className="flex flex-col gap-3">
        <div className="grid grid-cols-2 gap-3">
          <label className="flex flex-col gap-1">
            <span className="font-mono-ui text-xs text-text-tertiary">Username</span>
            <input
              type="text"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              className="rounded border border-current/20 bg-black/40 px-3 py-1.5 font-mono-ui text-sm text-text-primary"
              required
              minLength={2}
            />
          </label>
          <label className="flex flex-col gap-1">
            <span className="font-mono-ui text-xs text-text-tertiary">Password</span>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="rounded border border-current/20 bg-black/40 px-3 py-1.5 font-mono-ui text-sm text-text-primary"
              required
              minLength={8}
            />
          </label>
          <label className="flex flex-col gap-1">
            <span className="font-mono-ui text-xs text-text-tertiary">Display Name</span>
            <input
              type="text"
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
              className="rounded border border-current/20 bg-black/40 px-3 py-1.5 font-mono-ui text-sm text-text-primary"
            />
          </label>
          <label className="flex flex-col gap-1">
            <span className="font-mono-ui text-xs text-text-tertiary">Role</span>
            <select
              value={role}
              onChange={(e) => setRole(e.target.value as "user" | "admin")}
              className="rounded border border-current/20 bg-black/40 px-3 py-1.5 font-mono-ui text-sm text-text-primary"
            >
              <option value="user">user</option>
              <option value="admin">admin</option>
            </select>
          </label>
        </div>
        {error && (
          <div className="text-sm text-red-400">{error}</div>
        )}
        <div className="flex gap-2 justify-end">
          <Button ghost size="sm" onClick={onClose} type="button">
            Cancel
          </Button>
          <Button size="sm" type="submit" disabled={busy}>
            Create
          </Button>
        </div>
      </form>
    </div>
  );
}

function EditUserDialog({
  user: u,
  onClose,
  onUpdated,
}: {
  user: ControlUser;
  onClose: () => void;
  onUpdated: () => void;
}) {
  const [role, setRole] = useState(u.role);
  const [displayName, setDisplayName] = useState(u.display_name || "");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    setBusy(true);
    try {
      const updates: Record<string, string> = {};
      if (role !== u.role) updates.role = role;
      if (displayName.trim() !== (u.display_name || ""))
        updates.display_name = displayName.trim();
      if (password) updates.password = password;
      if (Object.keys(updates).length > 0) {
        await api.cpUpdateUser(u.id, updates);
      }
      onUpdated();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to update user");
    } finally {
      setBusy(false);
    }
  };

  return (
    <tr className="bg-midground/5">
      <td colSpan={6} className="px-4 py-3">
        <form onSubmit={handleSubmit} className="flex items-center gap-3 flex-wrap">
          <span className="font-mono-ui text-sm text-text-primary">
            Edit: <strong>{u.username}</strong>
          </span>
          <label className="flex items-center gap-1">
            <span className="font-mono-ui text-xs text-text-tertiary">Role</span>
            <select
              value={role}
              onChange={(e) => setRole(e.target.value)}
              className="rounded border border-current/20 bg-black/40 px-2 py-1 font-mono-ui text-xs text-text-primary"
            >
              <option value="user">user</option>
              <option value="admin">admin</option>
            </select>
          </label>
          <label className="flex items-center gap-1">
            <span className="font-mono-ui text-xs text-text-tertiary">Name</span>
            <input
              type="text"
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
              className="rounded border border-current/20 bg-black/40 px-2 py-1 font-mono-ui text-xs text-text-primary w-32"
            />
          </label>
          <label className="flex items-center gap-1">
            <span className="font-mono-ui text-xs text-text-tertiary">
              New Password
            </span>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="rounded border border-current/20 bg-black/40 px-2 py-1 font-mono-ui text-xs text-text-primary w-28"
              placeholder="leave blank"
              minLength={8}
            />
          </label>
          {error && <span className="text-xs text-red-400">{error}</span>}
          <div className="flex gap-1 ml-auto">
            <Button ghost size="sm" onClick={onClose} type="button">
              Cancel
            </Button>
            <Button size="sm" type="submit" disabled={busy}>
              Save
            </Button>
          </div>
        </form>
      </td>
    </tr>
  );
}
