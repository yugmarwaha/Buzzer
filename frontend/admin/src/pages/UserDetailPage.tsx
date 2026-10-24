import { useEffect, useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { ArrowLeft, Trash2, Pencil, ShieldAlert } from 'lucide-react';
import { api } from '../lib/api';
import { Button } from '../components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '../components/ui/card';
import { Input, fieldClassName } from '../components/ui/input';
import { Badge } from '../components/ui/badge';
import { Spinner } from '../components/ui/spinner';
import { staggerStyle } from '../components/ui/motion';

interface CourseAccess { course_id: number; role: string }
interface UserDetail {
  id: string;
  username: string | null;
  netid: string | null;
  display_name: string | null;
  email: string | null;
  role: string;
  created_at: string;
  last_login: string | null;
  course_access: CourseAccess[];
  /** Read-only: what each course grant implies. Quiz access comes from course access. */
  course_summary: CourseAccessSummary[];
}
interface CourseAccessSummary {
  course_id: number;
  name: string;
  semester: string;
  role: 'HOST' | 'PLAYER';
  active_quiz_count: number;
  archived_quiz_count: number;
}
interface Course { id: number; name: string; semester: string }

interface CourseSelection { id: number; role: 'HOST' | 'PLAYER'; checked: boolean }

export default function UserDetailPage() {
  const { userId } = useParams<{ userId: string }>();
  const navigate = useNavigate();
  const [user, setUser] = useState<UserDetail | null>(null);
  const [courses, setCourses] = useState<Course[]>([]);
  const [error, setError] = useState('');
  const [showCoursePanel, setShowCoursePanel] = useState(false);
  const [courseSelections, setCourseSelections] = useState<CourseSelection[]>([]);
  const [grantingCourses, setGrantingCourses] = useState(false);
  const [editing, setEditing] = useState(false);
  const [editUsername, setEditUsername] = useState('');
  const [editDisplayName, setEditDisplayName] = useState('');
  const [editEmail, setEditEmail] = useState('');
  const [editPassword, setEditPassword] = useState('');
  const [editRole, setEditRole] = useState('');
  const [editSaving, setEditSaving] = useState(false);

  async function load() {
    try {
      const [u, cs] = await Promise.all([
        api.get<UserDetail>(`/admin/users/${userId}`),
        api.get<Course[]>('/admin/courses'),
      ]);
      setUser(u);
      setCourses(cs);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load user');
    }
  }

  useEffect(() => { void load(); }, [userId]);

  function openCoursePanel(availableCourses: Course[]) {
    setCourseSelections(availableCourses.map((c) => ({ id: c.id, role: 'HOST', checked: false })));
    setShowCoursePanel(true);
  }

  async function grantSelectedCourses() {
    const selected = courseSelections.filter((s) => s.checked);
    if (selected.length === 0) return;
    setGrantingCourses(true);
    setError('');
    try {
      await Promise.all(
        selected.map((s) => api.post(`/admin/users/${userId}/course-access`, { course_id: s.id, role: s.role }))
      );
      setShowCoursePanel(false);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to grant access');
    } finally {
      setGrantingCourses(false);
    }
  }

  async function revokeCourseAccess(courseId: number) {
    try {
      await api.delete(`/admin/users/${userId}/course-access/${courseId}`);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to revoke access');
    }
  }

  function startEdit(u: UserDetail) {
    setEditUsername(u.username ?? '');
    setEditDisplayName(u.display_name ?? '');
    setEditEmail(u.email ?? '');
    setEditPassword('');
    setEditRole(u.role);
    setEditing(true);
  }

  async function saveEdit() {
    if (!user) return;
    setEditSaving(true);
    setError('');
    try {
      const body: Record<string, string> = {};
      if (user.username !== null && editUsername !== user.username) body.username = editUsername;
      if (editDisplayName !== (user.display_name ?? '')) body.display_name = editDisplayName;
      if (editEmail !== (user.email ?? '')) body.email = editEmail;
      if (editPassword) body.password = editPassword;
      if (editRole !== user.role) body.role = editRole;
      await api.put(`/admin/users/${userId}`, body);
      setEditing(false);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to update user');
    } finally {
      setEditSaving(false);
    }
  }

  async function deleteUser() {
    if (!confirm('Delete this user? This cannot be undone.')) return;
    try {
      await api.delete(`/admin/users/${userId}`);
      navigate('/users');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to delete user');
    }
  }

  if (!user) {
    return (
      <div className="flex items-center gap-3 p-6 text-body text-muted">
        {error ? <p role="alert" className="text-danger">{error}</p> : <><Spinner label="Loading user" /> Loading{'…'}</>}
      </div>
    );
  }

  const courseMap = Object.fromEntries(courses.map((c) => [c.id, c]));

  const grantedCourseIds = new Set(user.course_access.map((ca) => ca.course_id));
  const availableCourses = courses.filter((c) => !grantedCourseIds.has(c.id));

  const selectedCourseCount = courseSelections.filter((s) => s.checked).length;

  const title = user.display_name ?? user.username ?? user.netid ?? 'User';
  const initial = title.trim().charAt(0).toUpperCase() || '?';
  const labelClass = 'mb-1.5 block text-caption text-secondary';

  return (
    <div className="mx-auto max-w-3xl space-y-5 p-4 md:p-6">
      <button
        onClick={() => navigate('/users')}
        className="flex items-center gap-2 rounded-lg px-2 py-1 text-caption text-secondary transition-colors duration-fast hover:bg-raised hover:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        <ArrowLeft size={14} aria-hidden="true" /> Back to Users
      </button>

      {error && (
        <p role="alert" className="rounded-lg border border-danger bg-danger-tint px-4 py-2.5 text-body text-danger animate-fade-in">
          {error}
        </p>
      )}

      <div className="space-y-5 stagger">
        {/* Profile */}
        <Card variant="glass" className="animate-slide-up">
          <CardHeader className="pt-6">
            <div className="flex flex-wrap items-start gap-4">
              <span
                aria-hidden="true"
                className="flex h-14 w-14 shrink-0 items-center justify-center rounded-2xl bg-accent-hint text-heading text-accent-text"
              >
                {initial}
              </span>
              <div className="min-w-0 flex-1">
                <h2 className="break-words text-heading text-primary">{title}</h2>
                <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1.5 text-caption text-secondary">
                  {user.username && <span>@{user.username}</span>}
                  {user.netid && <span>netid: {user.netid}</span>}
                  <Badge tone={user.role === 'ADMIN' ? 'accent' : 'neutral'}>{user.role}</Badge>
                </div>
              </div>
              {!editing && (
                <Button variant="outline" size="sm" onClick={() => startEdit(user)}>
                  <Pencil size={14} aria-hidden="true" /> Edit
                </Button>
              )}
            </div>
          </CardHeader>
          <CardContent className="pt-3">
            {editing ? (
              <div className="space-y-4 border-t border-line pt-4 animate-fade-in">
                {user.username !== null && (
                  <div>
                    <label className={labelClass}>Username</label>
                    <Input value={editUsername} onChange={(e) => setEditUsername(e.target.value)} placeholder="username" />
                  </div>
                )}
                <div>
                  <label className={labelClass}>Display name</label>
                  <Input value={editDisplayName} onChange={(e) => setEditDisplayName(e.target.value)} placeholder="Display name" />
                </div>
                <div>
                  <label className={labelClass}>Email</label>
                  <Input type="email" value={editEmail} onChange={(e) => setEditEmail(e.target.value)} placeholder="email@example.com" />
                </div>
                {user.username !== null && (
                  <div>
                    <label className={labelClass}>New password <span className="text-muted">(leave blank to keep current)</span></label>
                    <Input type="password" value={editPassword} onChange={(e) => setEditPassword(e.target.value)} placeholder="New password" />
                  </div>
                )}
                <div>
                  <label className={labelClass}>Role</label>
                  <select className={fieldClassName} value={editRole} onChange={(e) => setEditRole(e.target.value)}>
                    <option value="USER">USER</option>
                    <option value="ADMIN">ADMIN</option>
                  </select>
                </div>
                <div className="flex gap-2 pt-1">
                  <Button size="sm" onClick={() => void saveEdit()} loading={editSaving}>
                    {editSaving ? 'Saving…' : 'Save'}
                  </Button>
                  <Button size="sm" variant="ghost" onClick={() => setEditing(false)}>Cancel</Button>
                </div>
              </div>
            ) : (
              <div className="space-y-1 border-t border-line pt-3">
                {user.email && <p className="text-body text-secondary">{user.email}</p>}
                {user.netid && <p className="text-body text-secondary">netid: {user.netid} (OAuth2 {'—'} not editable)</p>}
                <p className="text-caption text-muted tabular-nums">
                  Created {new Date(user.created_at).toLocaleDateString()}
                  {user.last_login && ` · Last login ${new Date(user.last_login).toLocaleDateString()}`}
                </p>
              </div>
            )}
          </CardContent>
        </Card>

        {/* Course access */}
        <Card variant="glass" className="animate-slide-up">
          <CardHeader>
            <div className="flex items-center justify-between gap-3">
              <CardTitle className="flex items-center gap-2">
                Course Access
                <Badge tone="neutral" className="tabular-nums">{user.course_access.length}</Badge>
              </CardTitle>
              {availableCourses.length > 0 && !showCoursePanel && (
                <Button size="sm" variant="outline" onClick={() => openCoursePanel(availableCourses)}>
                  Add Courses
                </Button>
              )}
            </div>
          </CardHeader>
          <CardContent className="space-y-2">
            {user.course_access.length === 0 && !showCoursePanel && (
              <p className="text-body text-muted">No course access granted.</p>
            )}
            {user.course_access.map((ca, i) => {
              const course = courseMap[ca.course_id];
              const name = course ? `${course.name} — ${course.semester}` : `Course ${ca.course_id}`;
              return (
                <div
                  key={ca.course_id}
                  style={staggerStyle(i)}
                  className="glass-subtle flex items-center justify-between gap-3 rounded-xl px-4 py-2.5 animate-fade-in"
                >
                  <span className="flex min-w-0 flex-wrap items-center gap-2 text-body text-primary">
                    <span className="min-w-0 break-words">{name}</span>
                    <Badge tone={ca.role === 'HOST' ? 'accent' : 'neutral'}>{ca.role}</Badge>
                  </span>
                  <Button
                    variant="ghost"
                    size="sm"
                    aria-label={`Revoke access to ${name}`}
                    title="Revoke access"
                    className="hover:text-danger"
                    onClick={() => void revokeCourseAccess(ca.course_id)}
                  >
                    <Trash2 size={14} aria-hidden="true" />
                  </Button>
                </div>
              );
            })}

            {showCoursePanel && (
              <div className="glass-subtle space-y-1 rounded-xl p-4 animate-scale-in">
                <div className="mb-2 flex items-center gap-3 border-b border-line pb-2">
                  <input
                    type="checkbox"
                    className="h-4 w-4 rounded-sm"
                    aria-label="Select all courses"
                    checked={courseSelections.every((s) => s.checked)}
                    onChange={(e) =>
                      setCourseSelections((prev) => prev.map((s) => ({ ...s, checked: e.target.checked })))
                    }
                  />
                  <span className="flex-1 text-caption text-secondary">Select all</span>
                  <span className="w-24 text-center text-caption text-secondary">Role</span>
                </div>
                {courseSelections.map((sel, i) => {
                  const course = courseMap[sel.id];
                  return (
                    <div key={sel.id} className="flex items-center gap-3 py-1">
                      <input
                        type="checkbox"
                        className="h-4 w-4 rounded-sm"
                        aria-label={`Select ${course ? course.name : `Course ${sel.id}`}`}
                        checked={sel.checked}
                        onChange={(e) =>
                          setCourseSelections((prev) =>
                            prev.map((s, j) => j === i ? { ...s, checked: e.target.checked } : s)
                          )
                        }
                      />
                      <span className="min-w-0 flex-1 break-words text-body text-primary">
                        {course ? `${course.name} — ${course.semester}` : `Course ${sel.id}`}
                      </span>
                      <div className="flex w-24 overflow-hidden rounded-lg border border-line-strong text-caption">
                        <button
                          aria-pressed={sel.role === 'HOST'}
                          className={`flex-1 px-2 py-1.5 transition-colors duration-fast ${sel.role === 'HOST' ? 'bg-accent text-accent-fg' : 'text-secondary hover:bg-raised hover:text-primary'}`}
                          onClick={() => setCourseSelections((prev) => prev.map((s, j) => j === i ? { ...s, role: 'HOST' } : s))}
                        >HOST</button>
                        <button
                          aria-pressed={sel.role === 'PLAYER'}
                          className={`flex-1 px-2 py-1.5 transition-colors duration-fast ${sel.role === 'PLAYER' ? 'bg-accent text-accent-fg' : 'text-secondary hover:bg-raised hover:text-primary'}`}
                          onClick={() => setCourseSelections((prev) => prev.map((s, j) => j === i ? { ...s, role: 'PLAYER' } : s))}
                        >PLAYER</button>
                      </div>
                    </div>
                  );
                })}
                <div className="flex gap-2 pt-3">
                  <Button
                    size="sm"
                    onClick={() => void grantSelectedCourses()}
                    disabled={selectedCourseCount === 0}
                    loading={grantingCourses}
                  >
                    {grantingCourses ? 'Granting…' : `Grant Selected (${selectedCourseCount})`}
                  </Button>
                  <Button size="sm" variant="ghost" onClick={() => setShowCoursePanel(false)}>Cancel</Button>
                </div>
              </div>
            )}
          </CardContent>
        </Card>

        {/* What the course grants imply (read-only; there are no per-quiz grants) */}
        <Card variant="glass" className="animate-slide-up">
          <CardHeader>
            <CardTitle>Quiz access</CardTitle>
            <CardDescription className="text-caption">
              Quiz access comes from course access. Change it under Course Access above.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-2 pt-2">
            {user.course_summary.length === 0 && (
              <p className="text-body text-muted">No course access, so no quiz access.</p>
            )}
            {user.course_summary.map((c) => (
              <p key={c.course_id} className="text-body text-primary">
                {c.role === 'HOST'
                  ? `Hosts all quizzes in ${c.name} (${c.semester}): ${c.active_quiz_count} active, ${c.archived_quiz_count} archived.`
                  : `Joins live ${c.name} quizzes by room code.`}
              </p>
            ))}
          </CardContent>
        </Card>

        {/* Danger zone: Admin accounts cannot be deleted from the UI */}
        {user.role !== 'ADMIN' && (
          <Card variant="glass" className="animate-slide-up">
            <CardContent className="flex flex-wrap items-center justify-between gap-4 pt-6">
              <div className="flex min-w-0 items-start gap-3">
                <ShieldAlert size={20} className="mt-0.5 shrink-0 text-danger" aria-hidden="true" />
                <div>
                  <h3 className="text-subheading text-danger">Delete user</h3>
                  <p className="text-body text-secondary">Removes this account and all its course access. This cannot be undone.</p>
                </div>
              </div>
              <Button variant="danger" size="sm" onClick={() => void deleteUser()}>
                <Trash2 size={14} aria-hidden="true" /> Delete
              </Button>
            </CardContent>
          </Card>
        )}
      </div>
    </div>
  );
}
