import { useCallback, useEffect, useState } from "react";
import { ArrowRightLeft, RefreshCw, Users } from "lucide-react";
import { Button } from "@nous-research/ui/ui/components/button";
import { Typography } from "@/components/NouiTypography";
import { cn } from "@/lib/utils";
import { api, type GroupOwnerRecord } from "@/lib/api";

export default function GroupOwnersPage() {
  const [groups, setGroups] = useState<GroupOwnerRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const data = await api.cpListGroupOwners();
      setGroups(data);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load group owners");
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
          <Users className="h-5 w-5 text-midground" />
          <Typography className="font-mondwest text-display text-lg tracking-[0.1em] text-text-primary uppercase">
            Group Owners
          </Typography>
        </div>
        <div className="flex items-center gap-2">
          <Button ghost size="sm" onClick={load} disabled={loading}>
            <RefreshCw className={cn("h-4 w-4", loading && "animate-spin")} />
          </Button>
        </div>
      </div>

      <Typography className="font-mono-ui text-xs text-text-tertiary">
        Group chat owner = first person who @Bot'd in that chat. Their
        external_id drives sessions.user_id for every message in the group.
      </Typography>

      {error && (
        <div className="rounded border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-400">
          {error}
        </div>
      )}

      <div className="overflow-x-auto rounded border border-current/10">
        <table className="w-full text-left text-sm">
          <thead>
            <tr className="border-b border-current/10 bg-[var(--component-header-background)]">
              <th className="px-4 py-2 font-mono-ui text-xs text-text-tertiary">Platform</th>
              <th className="px-4 py-2 font-mono-ui text-xs text-text-tertiary">Chat ID</th>
              <th className="px-4 py-2 font-mono-ui text-xs text-text-tertiary">Owner External ID</th>
              <th className="px-4 py-2 font-mono-ui text-xs text-text-tertiary">Alt ID</th>
              <th className="px-4 py-2 font-mono-ui text-xs text-text-tertiary">Established</th>
              <th className="px-4 py-2 font-mono-ui text-xs text-text-tertiary">Notes</th>
              <th className="px-4 py-2 font-mono-ui text-xs text-text-tertiary w-10" />
            </tr>
          </thead>
          <tbody>
            {groups.map((g) => (
              <GroupRow key={g.id} group={g} onUpdate={load} />
            ))}
            {groups.length === 0 && !loading && (
              <tr>
                <td colSpan={7} className="px-4 py-8 text-center text-text-tertiary">
                  No group owners found
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function GroupRow({
  group,
  onUpdate,
}: {
  group: GroupOwnerRecord;
  onUpdate: () => void;
}) {
  const [reassigning, setReassigning] = useState(false);

  return (
    <>
      <tr className="border-b border-current/5 hover:bg-white/5 transition-colors">
        <td className="px-4 py-2.5">
          <span className="font-mono-ui text-xs px-2 py-0.5 rounded bg-midground/10 text-midground uppercase">
            {group.platform}
          </span>
        </td>
        <td className="px-4 py-2.5 font-mono-ui text-xs text-text-primary max-w-[180px] truncate">
          {group.chat_id}
        </td>
        <td className="px-4 py-2.5 font-mono-ui text-xs text-text-primary max-w-[200px] truncate">
          {group.owner_external_id}
        </td>
        <td className="px-4 py-2.5 font-mono-ui text-xs text-text-tertiary">
          {group.owner_user_id_alt || "—"}
        </td>
        <td className="px-4 py-2.5 text-text-tertiary font-mono-ui text-xs">
          {new Date(group.established_at).toLocaleDateString()}
        </td>
        <td className="px-4 py-2.5 text-text-tertiary text-xs max-w-[150px] truncate">
          {group.notes || "—"}
        </td>
        <td className="px-4 py-2.5">
          <Button
            ghost
            size="icon"
            className="h-7 w-7"
            onClick={() => setReassigning(true)}
            title="Reassign owner"
          >
            <ArrowRightLeft className="h-3.5 w-3.5 text-midground" />
          </Button>
        </td>
      </tr>
      {reassigning && (
        <ReassignDialog
          group={group}
          onClose={() => setReassigning(false)}
          onDone={() => {
            setReassigning(false);
            onUpdate();
          }}
        />
      )}
    </>
  );
}

function ReassignDialog({
  group,
  onClose,
  onDone,
}: {
  group: GroupOwnerRecord;
  onClose: () => void;
  onDone: () => void;
}) {
  const [newExternalId, setNewExternalId] = useState("");
  const [newAltId, setNewAltId] = useState("");
  const [notes, setNotes] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newExternalId.trim()) return;
    setError("");
    setBusy(true);
    try {
      await api.cpReassignGroupOwner(group.id, {
        new_external_id: newExternalId.trim(),
        new_external_id_alt: newAltId.trim() || undefined,
        notes: notes.trim() || undefined,
      });
      onDone();
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "Failed to reassign",
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <tr className="bg-midground/5">
      <td colSpan={7} className="px-4 py-3">
        <form onSubmit={handleSubmit} className="flex items-center gap-3 flex-wrap">
          <span className="font-mono-ui text-sm text-text-primary">
            Reassign{" "}
            <strong>
              {group.platform}/{group.chat_id.slice(0, 16)}…
            </strong>
          </span>
          <label className="flex items-center gap-1">
            <span className="font-mono-ui text-xs text-text-tertiary">
              New External ID *
            </span>
            <input
              type="text"
              value={newExternalId}
              onChange={(e) => setNewExternalId(e.target.value)}
              className="rounded border border-current/20 bg-black/40 px-2 py-1 font-mono-ui text-xs text-text-primary w-48"
              required
              placeholder="ou_xxx"
            />
          </label>
          <label className="flex items-center gap-1">
            <span className="font-mono-ui text-xs text-text-tertiary">
              Alt ID
            </span>
            <input
              type="text"
              value={newAltId}
              onChange={(e) => setNewAltId(e.target.value)}
              className="rounded border border-current/20 bg-black/40 px-2 py-1 font-mono-ui text-xs text-text-primary w-36"
              placeholder="union_id"
            />
          </label>
          <label className="flex items-center gap-1">
            <span className="font-mono-ui text-xs text-text-tertiary">
              Notes
            </span>
            <input
              type="text"
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              className="rounded border border-current/20 bg-black/40 px-2 py-1 font-mono-ui text-xs text-text-primary w-32"
            />
          </label>
          {error && <span className="text-xs text-red-400">{error}</span>}
          <div className="flex gap-1">
            <Button ghost size="sm" onClick={onClose} type="button">
              Cancel
            </Button>
            <Button size="sm" type="submit" disabled={busy}>
              Reassign
            </Button>
          </div>
        </form>
      </td>
    </tr>
  );
}
