import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Plus } from 'lucide-react';
import { api } from '../lib/api';
import { Button } from '../components/ui/button';
import { Card, CardContent, CardHeader } from '../components/ui/card';
import { Input } from '../components/ui/input';
import { Badge, type BadgeTone } from '../components/ui/badge';
import { staggerStyle } from '../components/ui/motion';

const ROLE_TONE: Record<string, BadgeTone> = { ADMIN: 'accent', USER: 'neutral', GUEST: 'outline' };

interface UserItem {
  id: string;
  username: string | null;
  netid: string | null;
  display_name: string | null;
  email: string | null;
  role: string;
  created_at: string;
  last_login: string | null;
}

export default function UsersPage() {
  const [users, setUsers] = useState<UserItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [showForm, setShowForm] = useState(false);
  const [username, setUsername] = useState('');
  const [displayName, setDisplayName] = useState('');
  const [password, setPassword] = useState('');
  const [email, setEmail] = useState('');
  const [saving, setSaving] = useState(false);
  const navigate = useNavigate();

  async function load() {
    try {
      const data = await api.get<UserItem[]>('/admin/users');
      setUsers(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load users');
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { void load(); }, []);

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    setSaving(true);
    setError('');
    try {
      await api.post('/admin/users', {
        username,
        display_name: displayName,
        password,
        email: email || undefined,
      });
      setUsername(''); setDisplayName(''); setPassword(''); setEmail('');
      setShowForm(false);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to create user');
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="p-4 sm:p-6 max-w-4xl">
      <div className="flex items-center justify-between gap-3 mb-6">
        <div className="flex items-center gap-3">
          <h2 className="text-heading text-primary">Users</h2>
          {!loading && <Badge tone="neutral" className="tabular-nums">{users.length}</Badge>}
        </div>
        <Button onClick={() => setShowForm(!showForm)} size="sm">
          <Plus size={16} /> New User
        </Button>
      </div>

      {error && <p role="alert" className="mb-4 rounded-lg bg-danger-tint px-3 py-2 text-sm text-danger">{error}</p>}

      {showForm && (
        <Card variant="glass" className="mb-6 animate-slide-up">
          <CardHeader><h3 className="text-subheading text-primary">Create Local Account</h3></CardHeader>
          <CardContent>
            <form onSubmit={handleCreate} className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <Input placeholder="Username" value={username} onChange={(e) => setUsername(e.target.value)} required />
              <Input placeholder="Display name" value={displayName} onChange={(e) => setDisplayName(e.target.value)} required />
              <Input type="password" placeholder="Password (min 8 chars)" value={password} onChange={(e) => setPassword(e.target.value)} required />
              <Input type="email" placeholder="Email (optional)" value={email} onChange={(e) => setEmail(e.target.value)} />
              <div className="sm:col-span-2 flex gap-3">
                <Button type="submit" loading={saving}>{saving ? 'Creating\u2026' : 'Create User'}</Button>
                <Button type="button" variant="ghost" onClick={() => setShowForm(false)}>Cancel</Button>
              </div>
            </form>
          </CardContent>
        </Card>
      )}

      {loading ? (
        <div className="space-y-2" aria-busy="true" aria-label="Loading users">
          {[0, 1, 2, 3, 4].map((i) => <div key={i} className="skeleton h-16 rounded-xl" />)}
        </div>
      ) : users.length === 0 ? (
        <Card variant="glass-subtle" className="px-6 py-10 text-center">
          <p className="text-subheading text-primary">No users yet</p>
          <p className="mt-1 text-body text-muted">Select New User to create a local account.</p>
        </Card>
      ) : (
        <ul className="space-y-2">
          {users.map((u, i) => (
            <li key={u.id} className="animate-slide-up" style={staggerStyle(i)}>
              <div
                role="link"
                tabIndex={0}
                onClick={() => navigate(`/users/${u.id}`)}
                onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); navigate(`/users/${u.id}`); } }}
                className="lift flex flex-wrap items-center justify-between gap-x-4 gap-y-1 rounded-xl border border-line bg-surface px-4 py-3 shadow-soft cursor-pointer hover:border-line-strong focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-page"
              >
                <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1">
                  <span className="text-body font-semibold text-primary">{u.display_name ?? u.username ?? u.netid}</span>
                  <span className="text-caption text-muted">{u.username ?? u.netid}</span>
                  <Badge tone={ROLE_TONE[u.role] ?? 'neutral'}>{u.role}</Badge>
                </div>
                <span className="truncate text-caption text-muted">{u.email ?? ''}</span>
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
