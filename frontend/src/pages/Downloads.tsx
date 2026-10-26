import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, WachtlSettings } from "../api";
import { useAuth } from "../auth";
import { ErrorBox } from "../components/common";
import { fmtNum } from "../util";
import { setupCommand } from "./Clients";

const UNINSTALL = `"C:\\Program Files\\Wachtl\\unins000.exe" /VERYSILENT /SUPPRESSMSGBOXES`;

// Kopieren geht nur im sicheren Kontext (HTTPS/localhost) – sonst bleibt der Text zum Markieren stehen.
function CopyBlock({ text }: { text: string }) {
  const [done, setDone] = useState(false);
  const canCopy = typeof navigator !== "undefined" && !!navigator.clipboard && window.isSecureContext;
  return (
    <div className="row" style={{ alignItems: "flex-start" }}>
      <div className="codeblock" style={{ flex: 1, wordBreak: "break-all" }}>{text}</div>
      {canCopy && (
        <button onClick={() => navigator.clipboard.writeText(text).then(() => {
          setDone(true);
          setTimeout(() => setDone(false), 1500);
        }).catch(() => undefined)}>
          {done ? "Kopiert" : "Kopieren"}
        </button>
      )}
    </div>
  );
}

export default function DownloadsPage() {
  const { isAdmin } = useAuth();
  const [cfg, setCfg] = useState<WachtlSettings | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.get<WachtlSettings>("/api/clients/settings").then(setCfg).catch((e) => setError(String(e)));
  }, []);

  const setup = cfg?.setup ?? null;

  return (
    <div>
      <div className="topbar">
        <h1>Downloads</h1>
      </div>
      <ErrorBox error={error} />

      <div className="card" style={{ marginBottom: 16 }}>
        <h2>Wachtl Windows-Client</h2>
        <p className="muted" style={{ marginTop: 0 }}>
          Messdienst für die Windows-PCs (Windows 10/11, Windows Server 2016+, x64). Misst Ping, Pfad, Bandbreite und
          Sprachqualität zu den unter <Link to="/clients">Clients (Wachtl)</Link> festgelegten Zielen und liefert die
          Werte an netprobe. Benutzer melden Störungen über das Symbol neben der Uhr. Unverändertes Setup von{" "}
          <a href="https://github.com/fkroczak/Wachtl" target="_blank" rel="noreferrer">fkroczak/Wachtl</a> (MIT-Lizenz).
        </p>
        {!cfg ? (
          <p className="muted">Lädt…</p>
        ) : setup ? (
          <>
            <div className="row" style={{ marginBottom: 12 }}>
              <a className="button-link" href="/api/clients/setup" download={setup.filename}>
                {setup.filename} herunterladen
              </a>
              <span className="muted">Version {setup.version} · {fmtNum(setup.size / 1048576, 1)} MB</span>
            </div>
            {setup.sha256 && (
              <div className="field">
                <label>Prüfsumme (SHA256) – nach dem Download in PowerShell vergleichen</label>
                <div className="codeblock" style={{ wordBreak: "break-all" }}>{setup.sha256}</div>
                <CopyBlock text={`(Get-FileHash .\\${setup.filename} -Algorithm SHA256).Hash`} />
              </div>
            )}
          </>
        ) : (
          <div className="error-box">
            Auf diesem Server ist kein Wachtl-Setup installiert. netprobe aus dem Release-Paket
            (<code>netprobe.tar.gz</code>) installieren bzw. aktualisieren: <code>sudo bash install.sh</code> – das
            Paket enthält das Setup und legt es unter <code>/opt/netprobe/clients/wachtl/</code> ab.
          </div>
        )}
      </div>

      {cfg && (
        <div className="card" style={{ marginBottom: 16 }}>
          <h2>Installation auf den PCs</h2>
          {!cfg.enabled && (
            <div className="error-box">
              Die Anbindung ist ausgeschaltet – PCs können sich erst anmelden, wenn unter{" "}
              <Link to="/clients">Clients (Wachtl)</Link> „Anbindung aktiv“ eingeschaltet ist.
            </div>
          )}
          {!cfg.pin && <div className="error-box">{cfg.pin_error}</div>}
          <ol style={{ paddingLeft: 20, lineHeight: 1.6 }}>
            <li>
              Unter <Link to="/clients">Clients (Wachtl)</Link> einen <b>Standort</b> und <b>Checks</b> anlegen
              {isAdmin ? "" : " (Admin)"}.
            </li>
            <li>
              <b>Token erzeugen</b> (ebenfalls unter Clients (Wachtl)). netprobe zeigt danach den fertigen Befehl mit
              Token – einmalig, bitte aufbewahren.
            </li>
            <li>
              Setup auf den PC kopieren und den Befehl in einer <b>PowerShell als Administrator</b> ausführen – oder
              per GPO-Startskript bzw. Intune (Win32-App, Installationsverhalten „System“) verteilen:
              <CopyBlock text={setupCommand(cfg.server_urls, cfg.pin, "<TOKEN>", setup?.filename)} />
              <span className="muted" style={{ fontSize: 12 }}>
                Exit-Code 0 = Probe läuft, 10 = nicht betriebsbereit (z. B. Token abgelaufen; Details mit
                zusätzlichem <code>/LOG="C:\Temp\wachtl.log"</code>).
              </span>
            </li>
            <li>
              Nach etwa 30 Sekunden erscheint der PC unter <Link to="/clients">Clients (Wachtl)</Link> in der
              Standort-Matrix.
            </li>
          </ol>
          <div className="field">
            <label>Firewall: von den Client-Netzen zu netprobe</label>
            <span>
              TCP 443 (immer) · TCP {cfg.iperf_port} für Bandbreite zu netprobe · UDP {cfg.rtp_port} für Sprachtests
            </span>
          </div>
          <div className="field">
            <label>Update</label>
            <span>Neues Setup mit demselben Befehl über die alte Installation laufen lassen – Registrierung bleibt.</span>
          </div>
          <div className="field">
            <label>Deinstallation (meldet den PC an netprobe ab)</label>
            <CopyBlock text={UNINSTALL} />
          </div>
          <p className="muted" style={{ fontSize: 13, marginBottom: 0 }}>
            Ausführlich (GPO-Skript, Intune, Fehlersuche): <code>ANLEITUNG.md</code> im netprobe-Release-Paket.
          </p>
        </div>
      )}
    </div>
  );
}
