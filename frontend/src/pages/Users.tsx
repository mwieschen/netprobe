import { useEffect, useState } from "react";
import { api, ApiError, User } from "../api";
import { useAuth } from "../auth";
import { Modal, ErrorBox } from "../components/common";
import { fmtDateTime } from "../util";

export default function UsersPage() {
  const { isAdmin, user: me } = useAuth();
  const [users, setUsers] = useState<User[]>([]);
  const [showModal, setShowModal] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function load() {
    api.get<User[]>("/api/users").then(setUsers).catch((e: ApiError) => setError(e.detail));
  }
  useEffect(() => { if (isAdmin) load(); }, [isAdmin]);

  if (!isAdmin) return <div className="warn-box">Nur Administratoren können Benutzer verwalten.</div>;

  async function remove(id: number) {
    if (!confirm("Benutzer löschen?")) return;
    try {
      await api.del(`/api/users/${id}`);
      load();
    } catch (err) {
      setError((err as ApiError).detail);
    }
  }

  async function toggleRole(u: User) {
    await api.patch(`/api/users/${u.id}`, { role: u.role === "admin" ? "viewer" : "admin" });
    load();
  }

  return (
    <div>
      <div className="topbar">
        <h1>Benutzer</h1>
        <button className="primary" onClick={() => setShowModal(true)}>Benutzer anlegen</button>
      </div>
      <ErrorBox error={error} />

      <div className="card">
        <div className="table-scroll">
          <table>
            <thead><tr><th>Benutzername</th><th>Rolle</th><th>Aktiv</th><th>Erstellt</th><th></th></tr></thead>
            <tbody>
              {users.map((u) => (
                <tr key={u.id}>
                  <td>{u.username}{u.id === me?.id && <span className="muted"> (Sie)</span>}</td>
                  <td><span className={`badge ${u.role === "admin" ? "ok" : "dim"}`}>{u.role}</span></td>
                  <td>{u.is_active ? "ja" : "nein"}</td>
                  <td>{fmtDateTime(u.created_at)}</td>
                  <td className="num">
                    <div className="row" style={{ justifyContent: "flex-end" }}>
                      <button onClick={() => toggleRole(u)}>Rolle wechseln</button>
                      {u.id !== me?.id && <button className="danger" onClick={() => remove(u.id)}>Löschen</button>}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {showModal && <UserModal onClose={() => { setShowModal(false); load(); }} setError={setError} />}
    </div>
  );
}

function UserModal({ onClose, setError }: { onClose: () => void; setError: (s: string) => void }) {
  const [form, setForm] = useState({ username: "", password: "", role: "viewer" });
  async function save() {
    try {
      await api.post("/api/users", form);
      onClose();
    } catch (err) {
      setError((err as ApiError).detail);
    }
  }
  return (
    <Modal title="Benutzer anlegen" onClose={onClose}>
      <div className="field"><label>Benutzername</label>
        <input value={form.username} onChange={(e) => setForm({ ...form, username: e.target.value })} /></div>
      <div className="field"><label>Passwort (min. 8 Zeichen)</label>
        <input type="password" value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} /></div>
      <div className="field"><label>Rolle</label>
        <select value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value })}>
          <option value="viewer">viewer</option>
          <option value="admin">admin</option>
        </select></div>
      <button className="primary" onClick={save}>Anlegen</button>
    </Modal>
  );
}
