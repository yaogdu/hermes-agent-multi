import { useCallback, useEffect, useState } from "react";
import { ArrowRightLeft, Link, Plus, RefreshCw, Trash2 } from "lucide-react";
import { Button } from "@nous-research/ui/ui/components/button";
import { Typography } from "@/components/NouiTypography";
import { cn } from "@/lib/utils";
import { api, type IdentityRecord, type ControlUser } from "@/lib/api";

export default function IdentitiesPage() {
  const [identities, setIdentities] = useState<IdentityRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [showAdd, setShowAdd] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const data = await api.cpListIdentities();
      setIdentities(data);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load identities");
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
          <Link className="h-5 w-5 text-midground" />
          <Typography className="font-mondwest text-display text-lg tracking-[0.1em] text-text-primary uppercase">
            Identity Bindings
          </Typography>
        </div>
        <div className="flex items-center gap-2">
          <Button ghost size="sm" onClick={load} disabled={loading}>
            <RefreshCw className={cn("h-4 w-4", loading && "animate-spin")} />
          </Button>
          <Button size="sm" onClick={() => setShowAdd(true)}>
            <Plus className="h-4 w-4" />
            <span className="ml-1">Bind Identity</span>
          </Button>
        </div>
      </div>

      {error && (
        <div className="rounded border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-400">
          {error}
        </div>
      )}

      {showAdd && (
        <AddIdentityDialog
          onClose={() => setShowAdd(false)}
          onAdded={() => {
            setShowAdd(false);
            load();
          }}
        />
      )}

      <div className="overflow-x-auto rounded border border-current/10">
        <table className="w-full text-left text-sm">
          <thead>
            <tr className="border-b border-current/10 bg-[var(--component-header-background)]">
              <th className="px-4 py-2 font-mono-ui text-xs text-text-tertiary">Platform</th>
              <th className="px-4 py-2 font-mono-ui text-xs text-text-tertiary">External ID</th>
              <th className="px-4 py-2 font-mono-ui text-xs text-text-tertiary">Alt ID</th>
              <th className="px-4 py-2 font-mono-ui text-xs text-text-tertiary">Bound To</th>
              <th className="px-4 py-2 font-mono-ui text-xs text-text-tertiary">Display Name</th>
              <th className="px-4 py-2 font-mono-ui text-xs text-text-tertiary">Bound At</th>
              <th className="px-4 py-2 font-mono-ui text-xs text-text-tertiary w-10" />
            </tr>
          </thead>
          <tbody>
            {identities.map((idn) => (
              <IdentityRow key={idn.id} identity={idn} onUpdate={load} />
            ))}
            {identities.length === 0 && !loading && (
              <tr>
                <td colSpan={7} className="px-4 py-8 text-center text-text-tertiary">
                  No identity bindings found
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function IdentityRow({
  identity: idn,
  onUpdate,
}: {
  identity: IdentityRecord;
  onUpdate: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [transferring, setTransferring] = useState(false);

  const handleRemove = async () => {
    if (!confirm(`Remove binding for ${idn.platform}/${idn.external_id}?`)) return;
    setBusy(true);
    try {
      await api.cpRemoveIdentity(idn.id);
      onUpdate();
    } catch {
      // ignore
    } finally {
      setBusy(false);
    }
  };

  const isSystemUser = idn.username === "system" || idn.user_id === "usr_system";

  return (
    <>
      <tr className="border-b border-current/5 hover:bg-white/5 transition-colors">
        <td className="px-4 py-2.5">
          <span className="font-mono-ui text-xs px-2 py-0.5 rounded bg-midground/10 text-midground uppercase">
            {idn.platform}
          </span>
        </td>
        <td className="px-4 py-2.5 font-mono-ui text-xs text-text-primary max-w-[200px] truncate">
          {idn.external_id}
        </td>
        <td className="px-4 py-2.5 font-mono-ui text-xs text-text-tertiary">
          {idn.external_id_alt || "—"}
        </td>
        <td className="px-4 py-2.5">
          <span
            className={cn(
              "font-mono-ui text-xs",
              isSystemUser ? "text-text-tertiary" : "text-text-primary",
            )}
          >
            {idn.user_display_name || idn.username || "system"}
          </span>
        </td>
        <td className="px-4 py-2.5 text-text-secondary text-xs">
          {idn.display_name || "—"}
        </td>
        <td className="px-4 py-2.5 text-text-tertiary font-mono-ui text-xs">
          {new Date(idn.bound_at).toLocaleDateString()}
        </td>
        <td className="px-4 py-2.5">
          <div className="flex items-center gap-1">
            {isSystemUser && (
              <Button
                ghost
                size="icon"
                className="h-7 w-7"
                onClick={() => setTransferring(true)}
                title="Transfer to user"
              >
                <ArrowRightLeft className="h-3.5 w-3.5 text-midground" />
              </Button>
            )}
            <Button
              ghost
              size="icon"
              className="h-7 w-7"
              onClick={handleRemove}
              disabled={busy}
              title="Remove"
            >
              <Trash2 className="h-3.5 w-3.5 text-red-400" />
            </Button>
          </div>
        </td>
      </tr>
      {transferring && (
        <TransferIdentityDialog
          identity={idn}
          onClose={() => setTransferring(false)}
          onTransferred={() => {
            setTransferring(false);
            onUpdate();
          }}
        />
      )}
    </>
  );
}

function AddIdentityDialog({
  onClose,
  onAdded,
}: {
  onClose: () => void;
  onAdded: () => void;
}) {
  const [users, setUsers] = useState<ControlUser[]>([]);
  const [userId, setUserId] = useState("");
  const [platform, setPlatform] = useState("feishu");
  const [externalId, setExternalId] = useState("");
  const [externalIdAlt, setExternalIdAlt] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api.cpListUsers({ limit: 100 }).then(setUsers).catch(() => {});
  }, []);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!userId || !platform || !externalId.trim()) return;
    setError("");
    setBusy(true);
    try {
      await api.cpAddIdentity({
        user_id: userId,
        platform,
        external_id: externalId.trim(),
        external_id_alt: externalIdAlt.trim() || undefined,
        display_name: displayName.trim() || undefined,
      });
      onAdded();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to add identity");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="rounded border border-current/20 bg-black p-6">
      <Typography className="font-mondwest text-display text-sm tracking-[0.1em] text-text-primary uppercase mb-4">
        Bind Identity
      </Typography>
      <form onSubmit={handleSubmit} className="flex flex-col gap-3">
        <div className="grid grid-cols-2 gap-3">
          <label className="flex flex-col gap-1">
            <span className="font-mono-ui text-xs text-text-tertiary">User</span>
            <select
              value={userId}
              onChange={(e) => setUserId(e.target.value)}
              className="rounded border border-current/20 bg-black/40 px-3 py-1.5 font-mono-ui text-sm text-text-primary"
              required
            >
              <option value="">Select user…</option>
              {users.map((u) => (
                <option key={u.id} value={u.id}>
                  {u.username} ({u.role})
                </option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-1">
            <span className="font-mono-ui text-xs text-text-tertiary">Platform</span>
            <select
              value={platform}
              onChange={(e) => setPlatform(e.target.value)}
              className="rounded border border-current/20 bg-black/40 px-3 py-1.5 font-mono-ui text-sm text-text-primary"
            >
              <option value="feishu">Feishu</option>
              <option value="telegram">Telegram</option>
              <option value="slack">Slack</option>
              <option value="discord">Discord</option>
              <option value="wecom">WeCom</option>
            </select>
          </label>
          <label className="flex flex-col gap-1">
            <span className="font-mono-ui text-xs text-text-tertiary">External ID *</span>
            <input
              type="text"
              value={externalId}
              onChange={(e) => setExternalId(e.target.value)}
              className="rounded border border-current/20 bg-black/40 px-3 py-1.5 font-mono-ui text-sm text-text-primary"
              required
              placeholder="ou_xxx / user_xxx"
            />
          </label>
          <label className="flex flex-col gap-1">
            <span className="font-mono-ui text-xs text-text-tertiary">Alt ID</span>
            <input
              type="text"
              value={externalIdAlt}
              onChange={(e) => setExternalIdAlt(e.target.value)}
              className="rounded border border-current/20 bg-black/40 px-3 py-1.5 font-mono-ui text-sm text-text-primary"
              placeholder="union_id"
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
        </div>
        {error && <div className="text-sm text-red-400">{error}</div>}
        <div className="flex gap-2 justify-end">
          <Button ghost size="sm" onClick={onClose} type="button">
            Cancel
          </Button>
          <Button size="sm" type="submit" disabled={busy}>
            Bind
          </Button>
        </div>
      </form>
    </div>
  );
}

function TransferIdentityDialog({
  identity,
  onClose,
  onTransferred,
}: {
  identity: IdentityRecord;
  onClose: () => void;
  onTransferred: () => void;
}) {
  const [users, setUsers] = useState<ControlUser[]>([]);
  const [targetUserId, setTargetUserId] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api.cpListUsers({ limit: 100 }).then(setUsers).catch(() => {});
  }, []);

  const handleTransfer = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!targetUserId) return;
    setError("");
    setBusy(true);
    try {
      await api.cpTransferIdentity(identity.id, targetUserId);
      onTransferred();
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Failed to transfer identity",
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <tr className="bg-midground/5">
      <td colSpan={7} className="px-4 py-3">
        <form onSubmit={handleTransfer} className="flex items-center gap-3 flex-wrap">
          <span className="font-mono-ui text-sm text-text-primary">
            Transfer{" "}
            <strong>
              {identity.platform}/{identity.external_id}
            </strong>{" "}
            to:
          </span>
          <select
            value={targetUserId}
            onChange={(e) => setTargetUserId(e.target.value)}
            className="rounded border border-current/20 bg-black/40 px-2 py-1 font-mono-ui text-xs text-text-primary"
            required
          >
            <option value="">Select user…</option>
            {users
              .filter((u) => u.role !== "system" && u.status === "active")
              .map((u) => (
                <option key={u.id} value={u.id}>
                  {u.username}
                </option>
              ))}
          </select>
          {error && <span className="text-xs text-red-400">{error}</span>}
          <div className="flex gap-1">
            <Button ghost size="sm" onClick={onClose} type="button">
              Cancel
            </Button>
            <Button size="sm" type="submit" disabled={busy}>
              Transfer
            </Button>
          </div>
        </form>
      </td>
    </tr>
  );
}
