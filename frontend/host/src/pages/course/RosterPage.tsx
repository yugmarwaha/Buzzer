import { useEffect, useRef, useState } from 'react';
import { useParams } from 'react-router-dom';
import { Upload, X, Pencil, Search, Users, FileSpreadsheet, CheckCircle2 } from 'lucide-react';
import { api } from '../../lib/api';
import { Button } from '../../components/ui/button';
import { Card, CardContent, CardHeader } from '../../components/ui/card';
import { Input, fieldClassName } from '../../components/ui/input';
import { Badge } from '../../components/ui/badge';
import { cn } from '../../lib/utils';

interface RosterEntry {
  id: number;
  netid: string;
  full_name: string;
  email: string;
  is_active: boolean;
  imported_at: string;
}

interface UploadResult {
  imported: number;
  updated: number;
  deactivated: number;
  errors: string[];
}

interface MappedRow {
  netid: string;
  full_name: string;
  email: string;
}

interface EditDraft {
  netid: string;
  full_name: string;
  email: string;
  is_active: boolean;
}

// ── Minimal RFC-4180-compliant CSV parser ─────────────────────────────────────
function parseCSV(text: string): string[][] {
  const rows: string[][] = [];
  let row: string[] = [];
  let field = '';
  let inQuotes = false;
  let i = 0;
  while (i < text.length) {
    const ch = text[i];
    if (inQuotes) {
      if (ch === '"') {
        if (text[i + 1] === '"') { field += '"'; i += 2; continue; }
        inQuotes = false;
      } else {
        field += ch;
      }
    } else {
      if (ch === '"') { inQuotes = true; }
      else if (ch === ',') { row.push(field); field = ''; }
      else if (ch === '\n' || ch === '\r') {
        row.push(field); field = '';
        if (row.some((f) => f.trim() !== '')) rows.push(row);
        row = [];
        if (ch === '\r' && text[i + 1] === '\n') i++;
      } else { field += ch; }
    }
    i++;
  }
  if (field !== '' || row.length > 0) {
    row.push(field);
    if (row.some((f) => f.trim() !== '')) rows.push(row);
  }
  return rows;
}

// ── Apply column mapping + transforms to one raw CSV row ─────────────────────
function mapRow(
  rawRow: string[],
  headers: string[],
  netidCol: string,
  nameCol: string,
  emailCol: string,
  skipBlank: boolean,
  stripDomain: boolean,
  reverseName: boolean,
): MappedRow | null {
  const get = (col: string): string => {
    const idx = headers.indexOf(col);
    return idx >= 0 ? (rawRow[idx] ?? '').trim() : '';
  };

  let netid = get(netidCol);
  let full_name = get(nameCol);
  let email = get(emailCol);

  // Strip @domain from netid (Canvas: "NETID@WISC.EDU" → "netid")
  // When netidCol === emailCol, use the full value as email before stripping
  if (stripDomain && netid.includes('@')) {
    if (!email || emailCol === netidCol) email = netid;
    netid = netid.split('@')[0];
  }

  // Extract given name from "Last, First [Middle]" format
  if (reverseName && full_name.includes(',')) {
    full_name = full_name.split(',').slice(1).join(',').trim();
  }

  // Normalize
  netid = netid.toLowerCase();
  email = email.toLowerCase();
  // Title-case each word
  full_name = full_name.replace(/\b\w/g, (c) => c.toUpperCase());

  if (skipBlank && (!netid || !full_name || !email)) return null;
  return { netid, full_name, email };
}

// ── Component ─────────────────────────────────────────────────────────────────
export default function RosterPage() {
  const { courseId } = useParams<{ courseId: string }>();
  const fileRef = useRef<HTMLInputElement>(null);

  // Roster list
  const [entries, setEntries] = useState<RosterEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  // Inline edit
  const [editingId, setEditingId] = useState<number | null>(null);
  const [editDraft, setEditDraft] = useState<EditDraft | null>(null);
  const [saving, setSaving] = useState(false);

  // Wizard state
  const [step, setStep] = useState<'idle' | 'mapping' | 'result'>('idle');
  const [fileName, setFileName] = useState('');
  const [headers, setHeaders] = useState<string[]>([]);
  const [rawRows, setRawRows] = useState<string[][]>([]);
  const [netidCol, setNetidCol] = useState('');
  const [nameCol, setNameCol] = useState('');
  const [emailCol, setEmailCol] = useState('');
  const [skipRow2, setSkipRow2] = useState(false);
  const [stripDomain, setStripDomain] = useState(false);
  const [reverseName, setReverseName] = useState(false);
  const [importing, setImporting] = useState(false);
  const [uploadResult, setUploadResult] = useState<UploadResult | null>(null);
  const [query, setQuery] = useState('');

  // Guards against a slow response for a previous course overwriting the current one.
  const latestCourse = useRef(courseId);

  async function load() {
    const id = courseId;
    try {
      const data = await api.get<RosterEntry[]>(`/host/courses/${courseId}/roster`);
      if (latestCourse.current !== id) return;
      setEntries(data);
    } catch (err) {
      if (latestCourse.current !== id) return;
      setError(err instanceof Error ? err.message : 'Failed to load roster');
    } finally {
      if (latestCourse.current === id) setLoading(false);
    }
  }

  useEffect(() => {
    latestCourse.current = courseId;
    setEntries([]); setError(''); setLoading(true);
    void load();
  }, [courseId]);

  // ── File selected: parse + auto-detect ───────────────────────────────────
  function handleFileSelect(file: File) {
    setError('');
    const reader = new FileReader();
    reader.onload = (e) => {
      const text = (e.target?.result as string) ?? '';
      const all = parseCSV(text);
      if (all.length === 0) { setError('CSV file appears to be empty.'); return; }

      const hdrs = all[0];
      const data = all.slice(1);
      setFileName(file.name);
      setHeaders(hdrs);
      setRawRows(data);

      // Case-insensitive column finder
      const lower = hdrs.map((h) => h.trim().toLowerCase());
      const find = (...candidates: string[]): string => {
        for (const c of candidates) {
          const idx = lower.indexOf(c);
          if (idx >= 0) return hdrs[idx];
        }
        return '';
      };

      const isCanvas = lower.includes('sis login id');
      const detNetid = find('sis login id', 'netid', 'login id');
      const detName  = find('student', 'full_name', 'full name', 'name');
      const detEmail = find('email');

      setNetidCol(detNetid || hdrs[0] || '');
      setNameCol(detName  || hdrs[1] || '');
      // Canvas: email comes from same column as netid (full "netid@domain" value)
      setEmailCol(detEmail || (isCanvas ? detNetid : hdrs[2]) || '');
      setStripDomain(isCanvas && !!detNetid);
      setReverseName(isCanvas && !!detName);

      // Auto-detect skip row 2: Canvas "Points Possible" metadata row
      const firstDataRow = data[0] ?? [];
      setSkipRow2(firstDataRow.some((cell) =>
        cell.trim().toLowerCase().startsWith('points possible'),
      ));

      setStep('mapping');
    };
    reader.readAsText(file);
  }

  // ── Build mapped rows from current wizard config ──────────────────────────
  function getMappedRows(): MappedRow[] {
    const dataRows = skipRow2 ? rawRows.slice(1) : rawRows;
    const result: MappedRow[] = [];
    for (const row of dataRows) {
      const mapped = mapRow(row, headers, netidCol, nameCol, emailCol, true, stripDomain, reverseName);
      if (mapped) result.push(mapped);
    }
    return result;
  }

  // ── Import ────────────────────────────────────────────────────────────────
  async function handleImport() {
    const rows = getMappedRows();
    if (rows.length === 0) { setError('No valid rows to import with the current mapping.'); return; }
    setImporting(true);
    setError('');
    try {
      const result = await api.post<UploadResult>(
        `/host/courses/${courseId}/roster/import`,
        { rows },
      );
      setUploadResult(result);
      setStep('result');
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Import failed');
    } finally {
      setImporting(false);
    }
  }

  function resetWizard() {
    setStep('idle');
    setFileName('');
    setHeaders([]);
    setRawRows([]);
    setUploadResult(null);
    setError('');
    if (fileRef.current) fileRef.current.value = '';
  }

  // ── Inline edit ───────────────────────────────────────────────────────────
  function startEdit(entry: RosterEntry) {
    setEditingId(entry.id);
    setEditDraft({
      netid: entry.netid,
      full_name: entry.full_name,
      email: entry.email,
      is_active: entry.is_active,
    });
    setError('');
  }

  function cancelEdit() {
    setEditingId(null);
    setEditDraft(null);
  }

  async function saveEdit(entryId: number) {
    if (!editDraft) return;
    setSaving(true);
    setError('');
    try {
      const updated = await api.patch<RosterEntry>(
        `/host/courses/${courseId}/roster/${entryId}`,
        editDraft,
      );
      setEntries((prev) => prev.map((e) => e.id === entryId ? updated : e));
      setEditingId(null);
      setEditDraft(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Update failed');
    } finally {
      setSaving(false);
    }
  }

  // Compute once; only meaningful in mapping step
  const mappedRows = step === 'mapping' ? getMappedRows() : [];
  const previewRows = mappedRows.slice(0, 3);
  const totalRows = mappedRows.length;

  const active = entries.filter((e) => e.is_active);
  const inactive = entries.filter((e) => !e.is_active);
  const q = query.trim().toLowerCase();
  const shown = q
    ? entries.filter((e) => `${e.full_name} ${e.netid} ${e.email}`.toLowerCase().includes(q))
    : entries;

  return (
    <div className="max-w-5xl">
      <div className="mb-6 flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-heading text-primary">Roster</h2>
          {!loading && entries.length > 0 && (
            <p className="mt-1 text-body text-muted tabular-nums">
              {active.length} active &middot; {inactive.length} inactive
            </p>
          )}
        </div>
        {step === 'idle' && (
          <div className="flex flex-wrap items-center gap-3">
            {entries.length > 0 && (
              <div className="relative">
                <Search size={16} aria-hidden="true" className="pointer-events-none absolute left-3.5 top-1/2 -translate-y-1/2 text-muted" />
                <Input
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  placeholder="Search roster"
                  aria-label="Search roster"
                  className="w-56 pl-10 sm:w-64"
                />
              </div>
            )}
            <input
              ref={fileRef}
              type="file"
              accept=".csv"
              className="hidden"
              onChange={(e) => { const f = e.target.files?.[0]; if (f) handleFileSelect(f); }}
            />
            <Button onClick={() => fileRef.current?.click()}>
              <Upload size={16} /> Upload CSV
            </Button>
          </div>
        )}
      </div>

      {error && (
        <p role="alert" className="mb-4 animate-fade-in rounded-lg border border-danger bg-danger-tint px-4 py-3 text-body text-danger">
          {error}
        </p>
      )}

      {/* ── Column-mapping wizard ── */}
      {step === 'mapping' && (
        <Card variant="glass-strong" className="mb-6 animate-scale-in">
          <CardHeader>
            <div className="flex items-center justify-between">
              <h3 className="flex items-center gap-2 text-subheading text-primary">
                <FileSpreadsheet size={20} aria-hidden="true" className="text-accent-text" />
                Map Columns — {fileName}
              </h3>
              <button
                onClick={resetWizard}
                aria-label="Close"
                className="rounded-lg p-2 text-muted transition-colors duration-fast hover:bg-raised hover:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                <X size={18} />
              </button>
            </div>
          </CardHeader>
          <CardContent className="space-y-5">

            {/* Raw CSV preview */}
            <div>
              <p className="mb-2 text-body text-muted">
                Raw preview — {rawRows.length} data row{rawRows.length !== 1 ? 's' : ''} detected:
              </p>
              <div className="overflow-x-auto rounded-xl border border-line">
                <table className="w-full text-sm text-secondary">
                  <thead>
                    <tr className="bg-raised">
                      {headers.map((h, i) => (
                        <th key={i} className="whitespace-nowrap px-3 py-2.5 text-left text-label uppercase text-secondary">{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {rawRows.slice(0, 4).map((row, ri) => (
                      <tr
                        key={ri}
                        className={cn('border-t border-line', ri === 0 && skipRow2 && 'opacity-30 line-through')}
                      >
                        {headers.map((_, ci) => (
                          <td key={ci} className="px-3 py-1.5 max-w-[180px] truncate">{row[ci] ?? ''}</td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>

            {/* Column selectors */}
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
              {([
                { label: 'NetID column', value: netidCol, set: setNetidCol },
                { label: 'Full name column', value: nameCol, set: setNameCol },
                { label: 'Email column', value: emailCol, set: setEmailCol },
              ] as const).map(({ label, value, set }) => (
                <div key={label}>
                  <label className="mb-1.5 block text-caption text-secondary">{label}</label>
                  <select
                    value={value}
                    onChange={(e) => set(e.target.value)}
                    className={cn(fieldClassName, 'text-sm')}
                  >
                    {headers.map((h) => <option key={h} value={h}>{h}</option>)}
                  </select>
                </div>
              ))}
            </div>

            {/* Options */}
            <div className="space-y-2">
              {([
                {
                  id: 'skip2',
                  checked: skipRow2,
                  set: setSkipRow2,
                  label: 'Skip row 2 — e.g. Canvas "Points Possible" metadata row',
                },
                {
                  id: 'strip',
                  checked: stripDomain,
                  set: setStripDomain,
                  label: 'Strip @domain from NetID column (netid@wisc.edu → netid; full address used as email)',
                },
                {
                  id: 'rev',
                  checked: reverseName,
                  set: setReverseName,
                  label: 'Name is in "Last, First" format — extract given name only',
                },
              ] as const).map(({ id, checked, set, label }) => (
                <label key={id} className="flex cursor-pointer select-none items-center gap-2.5 text-body text-secondary">
                  <input
                    type="checkbox"
                    checked={checked}
                    onChange={(e) => set(e.target.checked)}
                    className="size-4 rounded-sm border-line-strong bg-surface accent-accent"
                  />
                  {label}
                </label>
              ))}
            </div>
            <p className="text-body text-muted">Rows missing any required field after mapping are automatically skipped.</p>

            {/* Mapped preview */}
            {previewRows.length > 0 && (
              <div>
                <p className="mb-2 text-body text-muted tabular-nums">
                  Mapped preview — {totalRows} row{totalRows !== 1 ? 's' : ''} will be imported:
                </p>
                <div className="overflow-hidden rounded-xl border border-line">
                  <table className="w-full text-sm text-secondary">
                    <thead>
                      <tr className="bg-raised text-label uppercase text-secondary">
                        <th className="px-3 py-2.5 text-left">netid</th>
                        <th className="px-3 py-2.5 text-left">full_name</th>
                        <th className="px-3 py-2.5 text-left">email</th>
                      </tr>
                    </thead>
                    <tbody>
                      {previewRows.map((r, i) => (
                        <tr key={i} className="border-t border-line">
                          <td className="px-3 py-1.5">{r.netid}</td>
                          <td className="px-3 py-1.5">{r.full_name}</td>
                          <td className="px-3 py-1.5">{r.email}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}

            <div className="flex flex-wrap gap-3">
              <Button onClick={() => void handleImport()} disabled={importing || totalRows === 0} loading={importing}>
                {importing ? 'Importing…' : `Import ${totalRows} row${totalRows !== 1 ? 's' : ''}`}
              </Button>
              <Button variant="ghost" onClick={resetWizard}>Cancel</Button>
            </div>

          </CardContent>
        </Card>
      )}

      {/* ── Result banner ── */}
      {step === 'result' && uploadResult && (
        <Card variant="glass" className="mb-6 animate-slide-up p-5" role="status">
          <div className="flex items-start justify-between gap-4">
            <div className="flex items-start gap-3">
              <CheckCircle2 size={22} aria-hidden="true" className="mt-0.5 shrink-0 text-success" />
              <div>
                <p className="text-subheading text-success tabular-nums">
                  Import complete &mdash; {uploadResult.imported} imported, {uploadResult.updated} updated,{' '}
                  {uploadResult.deactivated} deactivated
                </p>
                {uploadResult.errors.length > 0 && (
                  <ul className="mt-2 space-y-1 text-body text-danger">
                    {uploadResult.errors.map((e, i) => <li key={i}>{e}</li>)}
                  </ul>
                )}
              </div>
            </div>
            <button
              onClick={resetWizard}
              aria-label="Dismiss"
              className="rounded-lg p-2 text-muted transition-colors duration-fast hover:bg-raised hover:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              <X size={18} />
            </button>
          </div>
        </Card>
      )}

      {/* ── Roster table ── */}
      {loading ? (
        <div className="glass rounded-2xl p-4" role="status" aria-label="Loading roster">
          <div className="space-y-3">
            {[0, 1, 2, 3, 4].map((i) => (
              <div key={i} className="skeleton h-12 rounded-lg" />
            ))}
          </div>
        </div>
      ) : entries.length === 0 ? (
        <div className="glass animate-fade-in rounded-2xl px-6 py-14 text-center">
          <div className="mx-auto mb-4 flex size-14 items-center justify-center rounded-2xl bg-accent-hint text-accent-text">
            <Users size={28} aria-hidden="true" />
          </div>
          <h3 className="text-heading text-primary">Your roster is empty</h3>
          <p className="mx-auto mt-2 max-w-md text-body text-muted">
            No roster entries yet. Upload a CSV to get started.
          </p>
          {step === 'idle' && (
            <Button className="mt-6" onClick={() => fileRef.current?.click()}>
              <Upload size={16} /> Upload CSV
            </Button>
          )}
        </div>
      ) : (
        <div className="glass animate-fade-in overflow-hidden rounded-2xl">
          <div className="max-h-[65vh] overflow-auto">
            <table className="w-full min-w-[760px] text-body">
              <thead className="sticky top-0 z-10">
                <tr className="border-b border-line bg-surface text-label uppercase text-secondary">
                  <th scope="col" className="px-4 py-3 text-left">Full Name</th>
                  <th scope="col" className="px-4 py-3 text-left">NetID</th>
                  <th scope="col" className="px-4 py-3 text-left">Email</th>
                  <th scope="col" className="px-4 py-3 text-left">Status</th>
                  <th scope="col" className="px-4 py-3 text-left">Imported</th>
                  <th scope="col" className="px-4 py-3"><span className="sr-only">Actions</span></th>
                </tr>
              </thead>
              <tbody>
                {shown.length === 0 && (
                  <tr>
                    <td colSpan={6} className="px-4 py-10 text-center text-body text-muted">
                      No roster entries match &ldquo;{query.trim()}&rdquo;.
                    </td>
                  </tr>
                )}
                {shown.map((entry) =>
                  editingId === entry.id && editDraft ? (
                    // ── Edit row ──
                    <tr key={entry.id} className="border-t border-line bg-accent-hint">
                      <td className="px-3 py-2">
                        <Input
                          value={editDraft.full_name}
                          onChange={(e) => setEditDraft({ ...editDraft, full_name: e.target.value })}
                          aria-label="Full name"
                          className="h-10 text-sm"
                        />
                      </td>
                      <td className="px-3 py-2">
                        <Input
                          value={editDraft.netid}
                          onChange={(e) => setEditDraft({ ...editDraft, netid: e.target.value })}
                          aria-label="NetID"
                          className="h-10 font-mono text-sm"
                        />
                      </td>
                      <td className="px-3 py-2">
                        <Input
                          value={editDraft.email}
                          onChange={(e) => setEditDraft({ ...editDraft, email: e.target.value })}
                          aria-label="Email"
                          className="h-10 text-sm"
                        />
                      </td>
                      <td className="px-3 py-2">
                        <label className="flex cursor-pointer select-none items-center gap-2">
                          <input
                            type="checkbox"
                            checked={editDraft.is_active}
                            onChange={(e) => setEditDraft({ ...editDraft, is_active: e.target.checked })}
                            className="size-4 rounded-sm border-line-strong bg-surface accent-accent"
                          />
                          <span className="text-sm text-secondary">Active</span>
                        </label>
                      </td>
                      <td className="whitespace-nowrap px-4 py-2 text-sm text-muted tabular-nums">
                        {new Date(entry.imported_at).toLocaleDateString()}
                      </td>
                      <td className="px-3 py-2">
                        <div className="flex justify-end gap-2">
                          <Button size="sm" onClick={() => void saveEdit(entry.id)} disabled={saving} loading={saving}>
                            {saving ? 'Saving…' : 'Save'}
                          </Button>
                          <Button variant="ghost" size="sm" onClick={cancelEdit} disabled={saving}>
                            Cancel
                          </Button>
                        </div>
                      </td>
                    </tr>
                  ) : (
                    // ── View row ──
                    <tr
                      key={entry.id}
                      className="border-t border-line transition-colors duration-fast even:bg-raised hover:bg-accent-hint"
                    >
                      <td className={cn('px-4 py-3.5 font-semibold', entry.is_active ? 'text-primary' : 'text-secondary')}>
                        {entry.full_name}
                      </td>
                      <td className="px-4 py-3.5 font-mono text-sm text-secondary">{entry.netid}</td>
                      <td className="px-4 py-3.5 text-secondary">{entry.email}</td>
                      <td className="px-4 py-3.5">
                        <Badge tone={entry.is_active ? 'success' : 'neutral'}>
                          {entry.is_active ? 'Active' : 'Inactive'}
                        </Badge>
                      </td>
                      <td className="whitespace-nowrap px-4 py-3.5 text-sm text-muted tabular-nums">
                        {new Date(entry.imported_at).toLocaleDateString()}
                      </td>
                      <td className="px-4 py-2.5 text-right">
                        <Button variant="ghost" size="sm" onClick={() => startEdit(entry)}>
                          <Pencil size={14} /> Edit
                        </Button>
                      </td>
                    </tr>
                  )
                )}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
