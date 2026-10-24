import { useEffect, useState } from 'react';
import { api } from '../lib/api';
import { Button } from '../components/ui/button';
import { Card, CardContent, CardHeader } from '../components/ui/card';
import { Input } from '../components/ui/input';
import { Badge } from '../components/ui/badge';
import { staggerStyle } from '../components/ui/motion';

interface GuestUser {
  id: string;
  display_name: string | null;
  email: string | null;
  created_at: string;
}

export default function GuestsPage() {
  const [guests, setGuests] = useState<GuestUser[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [mergeGuestId, setMergeGuestId] = useState('');
  const [mergeNetid, setMergeNetid] = useState('');
  const [merging, setMerging] = useState(false);
  const [mergeSuccess, setMergeSuccess] = useState('');

  async function load() {
    try {
      const data = await api.get<GuestUser[]>('/admin/users/guests');
      setGuests(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load guests');
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { void load(); }, []);

  async function handleMerge(e: React.FormEvent) {
    e.preventDefault();
    // Merging cannot be undone: name the guest (when listed) and the target, as Delete confirms.
    const guest = guests.find((g) => g.id === mergeGuestId.trim());
    const guestLabel = guest ? `${guest.display_name ?? 'Unnamed guest'} (${guest.id})` : mergeGuestId;
    if (!confirm(`Merge guest ${guestLabel} into ${mergeNetid}? Their scores move to that account. This cannot be undone.`)) return;
    setMerging(true);
    setError('');
    setMergeSuccess('');
    try {
      await api.post('/admin/users/merge-guest', {
        guest_user_id: mergeGuestId,
        target_netid: mergeNetid,
      });
      setMergeSuccess(`Guest merged into ${mergeNetid}`);
      setMergeGuestId('');
      setMergeNetid('');
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Merge failed');
    } finally {
      setMerging(false);
    }
  }

  async function deleteGuest(id: string) {
    if (!confirm('Delete this guest user and all their scores?')) return;
    try {
      await api.delete(`/admin/users/${id}`);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Delete failed');
    }
  }

  return (
    <div className="p-4 sm:p-6 max-w-4xl space-y-6">
      <div className="flex items-center gap-3">
        <h2 className="text-heading text-primary">Guest Users</h2>
        {!loading && <Badge tone="neutral" className="tabular-nums">{guests.length}</Badge>}
      </div>

      {error && <p role="alert" className="rounded-lg bg-danger-tint px-3 py-2 text-sm text-danger">{error}</p>}
      {mergeSuccess && <p role="status" className="rounded-lg bg-success-tint px-3 py-2 text-sm text-success">{mergeSuccess}</p>}

      <Card variant="glass">
        <CardHeader><h3 className="text-subheading text-primary">Merge Guest into Real User</h3></CardHeader>
        <CardContent>
          <form onSubmit={handleMerge} className="flex gap-3 flex-wrap">
            <Input
              placeholder="Guest user ID (UUID)"
              value={mergeGuestId}
              onChange={(e) => setMergeGuestId(e.target.value)}
              className="flex-1 min-w-64 font-mono text-sm"
              required
            />
            <Input
              placeholder="Target netid"
              value={mergeNetid}
              onChange={(e) => setMergeNetid(e.target.value)}
              className="flex-1 min-w-40"
              required
            />
            <Button type="submit" loading={merging}>{merging ? 'Merging\u2026' : 'Merge'}</Button>
          </form>
        </CardContent>
      </Card>

      {loading ? (
        <div className="space-y-2" aria-busy="true" aria-label="Loading guests">
          {[0, 1, 2].map((i) => <div key={i} className="skeleton h-16 rounded-xl" />)}
        </div>
      ) : guests.length === 0 ? (
        <Card variant="glass-subtle" className="px-6 py-10 text-center">
          <p className="text-subheading text-primary">No guest users</p>
          <p className="mt-1 text-body text-muted">Guests appear here after someone joins a game without signing in.</p>
        </Card>
      ) : (
        <ul className="space-y-2">
          {guests.map((g, i) => (
            <li
              key={g.id}
              className="animate-slide-up flex flex-wrap items-center justify-between gap-3 rounded-xl border border-line bg-surface px-4 py-3 shadow-soft"
              style={staggerStyle(i)}
            >
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
                  <span className="text-body font-semibold text-primary">{g.display_name}</span>
                  <Badge tone="outline">GUEST</Badge>
                  <span className="text-caption text-muted">{g.email}</span>
                </div>
                <p className="mt-1 break-all font-mono text-xs text-muted">{g.id}</p>
              </div>
              <div className="flex items-center gap-2">
                <Button variant="outline" size="sm" onClick={() => setMergeGuestId(g.id)}>
                  Use ID
                </Button>
                <Button variant="danger" size="sm" onClick={() => void deleteGuest(g.id)}>
                  Delete
                </Button>
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
