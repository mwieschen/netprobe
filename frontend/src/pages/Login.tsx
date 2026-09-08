import { useState } from "react";
import { useAuth } from "../auth";
import { ApiError } from "../api";
import { ErrorBox } from "../components/common";

export default function Login() {
  const { login } = useAuth();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      await login(username, password);
    } catch (err) {
      const e = err as ApiError;
      setError(e.detail || "Anmeldung fehlgeschlagen");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="login-wrap">
      <form className="card login-card" onSubmit={submit}>
        <div className="brand" style={{ padding: "0 0 16px" }}>
          netprobe
        </div>
        <h2>Anmeldung</h2>
        <ErrorBox error={error} />
        <div className="field">
          <label>Benutzername</label>
          <input value={username} onChange={(e) => setUsername(e.target.value)} autoFocus />
        </div>
        <div className="field">
          <label>Passwort</label>
          <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} />
        </div>
        <button className="primary" type="submit" disabled={busy} style={{ width: "100%" }}>
          {busy ? "Anmelden…" : "Anmelden"}
        </button>
      </form>
    </div>
  );
}
