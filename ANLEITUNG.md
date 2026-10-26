# Anleitung: netprobe mit Wachtl-Clients einrichten

Diese Anleitung führt Schritt für Schritt vom Release-Paket bis zum ersten Windows-PC, der Messwerte an
netprobe liefert. Alles, was dafür nötig ist, steckt in **einem** Paket: `netprobe.tar.gz`.

| Wo | Was |
| --- | --- |
| **Ubuntu-Server** | netprobe (Weboberfläche, Datenbank, Zentrale für die Wachtl-Clients) |
| **Browser** (Admin) | Einrichtung in netprobe: Standorte, Checks, Token, Setup-Download |
| **Windows-PCs** | Wachtl-Probe (unverändertes Setup aus dem Paket), läuft als Dienst „Wachtl“ |

## Inhalt des Pakets

```
netprobe-<Version>-<Commit>/
├── ANLEITUNG.md            diese Anleitung
├── install.sh              Installation und Update (ein Skript für alles)
├── RELEASE                 Version von netprobe und mitgeliefertem Wachtl
├── backend/ frontend/dist/ netprobe selbst (Frontend vorgebaut, kein Node.js nötig)
├── clients/wachtl/
│   ├── WachtlSetup-<Version>.exe   Setup für die Windows-PCs (unverändert, github.com/fkroczak/Wachtl)
│   ├── SHA256SUMS.txt              Prüfsumme aus dem Wachtl-Release
│   ├── LIESMICH.md                 Release-Notes von Wachtl
│   ├── LICENSE                     MIT-Lizenz von Wachtl
│   └── VERSION
├── deploy/ scripts/        nginx, systemd, logrotate, Backup/Restore
└── docs/                   ausführliche Dokumentation (u. a. wachtl-zentrale.md)
```

---

## Teil 1 – Server (Ubuntu 22.04 / 24.04 / 26.04)

### 1.1 Paket auf den Server bringen und installieren

Auf dem **eigenen Rechner** (Paket aus dem GitHub-Release herunterladen):

```bash
scp netprobe.tar.gz <benutzer>@<server>:
```

Auf dem **Server**:

```bash
tar xzf netprobe.tar.gz
cd netprobe-*/
sudo bash install.sh
```

- **Neuinstallation**: Am Ende stehen Adresse und das **einmalige Admin-Passwort**. Notieren, sofort ändern.
- **Update** einer bestehenden Installation: dasselbe Kommando. `install.sh` erkennt, was sich geändert hat,
  legt vorher einen Snapshot an (`/var/lib/netprobe/backups/`), spielt Code und Datenbank-Migrationen ein und
  rollt bei einem Fehler automatisch zurück. Manuell zurück: `sudo bash /opt/netprobe/install.sh --rollback`.
- Das Wachtl-Setup landet unter `/opt/netprobe/clients/wachtl/` (Download später in der Weboberfläche unter
  **Downloads**).
- HTTPS: `install.sh` erzeugt einmalig einen Schlüssel und ein selbstsigniertes Zertifikat unter
  `/etc/netprobe/tls/`. Der **Schlüssel bleibt bei jedem Update gleich**, damit bleibt der Pin der PCs gültig.

> Ist das GitHub-Repository öffentlich, geht Installation und Update auch direkt:
> `curl -fsSL https://raw.githubusercontent.com/mwieschen/netprobe/main/install.sh | sudo bash`.
> Bei einem privaten Repository nur über das Paket wie oben.

### 1.2 Firewall

Von den **Client-Netzen zum netprobe-Server** freigeben:

| Port | Wofür | Nötig |
| --- | --- | --- |
| TCP 443 | Probe-API (Anmeldung, Messwerte, Heartbeat) | **immer** |
| TCP 80 / 443 | Weboberfläche für Admins | für die Verwaltung |
| TCP 5201 | iperf3-Gegenstelle („Bandbreite intern“ zu netprobe) | nur bei Bandbreiten-Checks |
| UDP 5004 | RTP-Reflektor („Sprache“) | nur bei Sprach-Checks |

`install.sh` öffnet bei aktiver UFW nur 80 und 443. 5201/5004 bei Bedarf selbst freigeben
(`sudo ufw allow 5201/tcp`, `sudo ufw allow 5004/udp`) und auf Firewalls zwischen den Standorten.

### 1.3 Server-Adressen festlegen (empfohlen)

Die PCs brauchen eine Adresse, unter der sie netprobe erreichen. Name **und** IP angeben, falls an einem
Standort die Namensauflösung fehlt. In `/etc/netprobe/netprobe.env`:

```bash
NETPROBE_WACHTL_SERVER_URLS=https://netprobe.firma.local,https://10.0.0.5
```

Danach `sudo systemctl restart netprobe`. Ohne diesen Eintrag nimmt netprobe die Adresse, unter der die
Weboberfläche gerade aufgerufen wird.

---

## Teil 2 – netprobe einrichten (Browser, als Admin)

Weboberfläche öffnen (`https://<server>` oder `http://<server>`), anmelden, Menü **Clients (Wachtl)**.

1. **Anbindung aktiv** einschalten. Solange sie aus ist, antwortet die Probe-API mit 404 (die PCs puffern
   dann bis zu 7 Tage).
2. Abschnitt **Einrichtung** prüfen:
   - **Wachtl-Setup**: Download-Link `WachtlSetup-<Version>.exe` mit Version und SHA256.
   - **SPKI-Pin**: muss angezeigt werden. Steht dort ein Fehler, ist HTTPS nicht eingerichtet → `install.sh`
     erneut ausführen.
   - **Server-Adressen**: so, wie die PCs netprobe erreichen sollen (siehe 1.3).
3. **Standort hinzufügen** (z. B. „Zentrale“, „Filiale Nord“).
4. **Check hinzufügen**, z. B.:
   - `gateway`, Rolle *Gateway* – das Standard-Gateway jedes PCs
   - `8.8.8.8` und `1.1.1.1`, Rolle *Internet-Referenz*
   - ein internes Ziel (Fileserver, TK-Anlage), Rolle *Ziel*
   - optional *Pfad*, *Bandbreite intern/WAN*, *Sprache*

   Ohne Standort gilt ein Check für alle Standorte.
5. **Token erzeugen**: Standort, Anzahl Installationen, Gültigkeit in Tagen. Angezeigt werden **einmalig**
   das Token und der fertige Installationsbefehl, z. B.:

   ```powershell
   WachtlSetup-0.9.6.exe /VERYSILENT /SUPPRESSMSGBOXES /Server=https://netprobe.firma.local,https://10.0.0.5 /Pin="kpbQ…=" /Token=wachtl-…
   ```

   Befehl kopieren und aufbewahren, bis alle PCs des Standorts installiert sind. Pro Standort ein eigenes Token.
6. Menü **Downloads** öffnen und **WachtlSetup-<Version>.exe herunterladen** klicken. Dort stehen auch
   Prüfsumme, Installationsbefehl (mit Server und Pin, Token einsetzen) und Deinstallationsbefehl zum Kopieren.
   Jeder angemeldete Benutzer kann das Setup laden; Token erzeugen nur Admins.

Prüfsumme der heruntergeladenen Datei kontrollieren (PowerShell), muss zum SHA256 in netprobe passen:

```powershell
Get-FileHash .\WachtlSetup-0.9.6.exe -Algorithm SHA256
```

---

## Teil 3 – Windows-PCs (Windows 10/11, Server 2016+, x64)

Voraussetzungen: lokaler Administrator für die Installation; ausgehend TCP 443 zu netprobe; ICMP zu den Zielen.
Kein .NET nötig.

### 3.1 Einzelner PC (zum Testen)

`WachtlSetup-<Version>.exe` auf den PC kopieren, **PowerShell als Administrator**, den Befehl aus Schritt 2.5
ausführen. Exit-Code 0 = Probe läuft, 10 = nicht betriebsbereit (z. B. Token abgelaufen, Grund mit
zusätzlichem `/LOG="C:\Temp\wachtl-setup.log"` sehen).

Ohne Parameter gestartet, fragt das Setup interaktiv nach Rolle (**Probe**), Server, Pin und Token.

### 3.2 Viele PCs per GPO (Computer-Startskript)

1. Setup auf eine Freigabe legen, die **Domänencomputer** lesen dürfen, z. B. `\\fs01\software$\wachtl\`.
2. Startskript `wachtl-install.ps1` daneben (Pin/Token/Server aus Schritt 2.5 einsetzen):

   ```powershell
   $setup = "\\fs01\software$\wachtl\WachtlSetup-0.9.6.exe"
   $probe = "C:\Program Files\Wachtl\wachtl-probe.exe"
   # Nur installieren, wenn die Probe fehlt oder älter ist
   $ist = if (Test-Path $probe) { (Get-Item $probe).VersionInfo.ProductVersion } else { "" }
   if ($ist -notlike "0.9.6*") {
       Start-Process $setup -Wait -ArgumentList @(
           "/VERYSILENT", "/SUPPRESSMSGBOXES", "/LOG=C:\Windows\Temp\wachtl-setup.log",
           "/Server=https://netprobe.firma.local,https://10.0.0.5",
           '/Pin="kpbQ…="', "/Token=wachtl-…")
   }
   ```

3. GPO: *Computerkonfiguration › Richtlinien › Windows-Einstellungen › Skripts › Start* → PowerShell-Skript.
   Läuft als SYSTEM beim nächsten Neustart. Auf bereits registrierten PCs aktualisiert ein Lauf nur das Programm.

### 3.3 Viele PCs per Intune (Win32-App)

1. `WachtlSetup-<Version>.exe` mit dem *Microsoft Win32 Content Prep Tool* zu `.intunewin` verpacken.
2. Intune › Apps › Windows › **Win32-App** hinzufügen:
   - **Installationsbefehl**: der Befehl aus Schritt 2.5
   - **Deinstallationsbefehl**: `"C:\Program Files\Wachtl\unins000.exe" /VERYSILENT /SUPPRESSMSGBOXES`
   - **Installationsverhalten**: System
   - **Rückgabecodes**: 0 = Erfolg, 10 = Fehler
   - **Erkennungsregel**: Datei `C:\Program Files\Wachtl\wachtl-probe.exe` vorhanden
3. Gerätegruppe des Standorts zuweisen. Pro Standort eine App (oder Zuweisung) mit dem Token dieses Standorts.

### 3.4 Kontrolle

- Nach etwa **30 Sekunden** erscheint der PC in netprobe unter **Clients** in der Standort-Matrix
  (Name = Computername).
- Auf dem PC: Dienst **Wachtl** läuft; Status mit `"C:\Program Files\Wachtl\wachtl-probe.exe" status`
  (Admin); Logs unter `C:\ProgramData\Wachtl\logs\`.
- Neben der Uhr erscheint das Wachtl-Symbol (bei Installation als SYSTEM ab der nächsten Anmeldung). Darüber
  melden Benutzer Störungen („Problem melden …“) – sie erscheinen in netprobe unter *Störungsmeldungen*.

### 3.5 Clients aktualisieren / entfernen

- **Update**: neues netprobe-Release auf dem Server einspielen (Teil 1.1) → neues Setup unter *Clients ›
  Einrichtung* herunterladen → wie bei der Installation verteilen. Auf registrierten PCs aktualisiert es nur das
  Programm; Token und Registrierung bleiben.
- **Entfernen**: Apps › Wachtl Probe, oder
  `"C:\Program Files\Wachtl\unins000.exe" /VERYSILENT /SUPPRESSMSGBOXES`. Die Probe wird dabei an netprobe
  abgemeldet (Token sofort ungültig); Messwerte bleiben in netprobe.

---

## Teil 4 – optional: E-Mail-Benachrichtigung

*Clients (Wachtl)* › Link **Benachrichtigung (E-Mail)** oben rechts: SMTP-Server eintragen, **Testmail** senden, Empfänger anlegen (Standort,
Mindest-Schweregrad, Kategorien). netprobe meldet Ausfälle, offline gegangene PCs, Störungsmeldungen und
Messereignisse – je eine Mail beim Beginn und bei der Erholung. Details: `docs/wachtl-zentrale.md`.

---

## Fehlersuche

| Symptom | Ursache und Lösung |
| --- | --- |
| Setup: „Das Server-Zertifikat passt nicht zum angegebenen Pin“ | Falscher Pin, oder ein Proxy/Firewall bricht TLS auf. Pin in netprobe prüfen, auf dem PC: `wachtl-probe.exe show-pin --server https://<server>`; TLS-Inspection für netprobe ausnehmen |
| Setup: „Unter dieser Adresse antwortet kein Wachtl-Server“ | netprobe älter als 1.1.2 (ohne `/healthz`) → aktualisieren. Prüfen im Browser: `https://<server>/healthz` muss `{"status":"ok",…}` zeigen |
| Setup: „Keine Verbindung zum Wachtl-Server“ / Zeitüberschreitung | TCP 443 vom PC zum Server gesperrt oder Name nicht auflösbar → Firewall prüfen, IP-Adresse als zweite Server-Adresse angeben |
| Setup: „Server lehnt die Registrierung ab“ | Token abgelaufen, aufgebraucht oder widerrufen → neues Token in netprobe erzeugen |
| PC erscheint nicht | *Anbindung aktiv* aus? TCP 443 vom PC zum Server gesperrt? Server-Adresse am Standort nicht auflösbar → IP zusätzlich in `NETPROBE_WACHTL_SERVER_URLS` |
| PC „offline“, Puffer steigt | Server nicht erreichbar. Werte bleiben bis 7 Tage auf dem PC und werden nachgesendet |
| Seite *Downloads* meldet „kein Wachtl-Setup installiert“ | netprobe wurde aus einem Repository-Checkout statt aus dem Release-Paket installiert → Paket installieren |
| Bandbreite/Sprache zu netprobe schlägt fehl | TCP 5201 bzw. UDP 5004 freigeben; iperf3-Server (Seite *iperf3*) bzw. RTP-Reflektor (*Clients › Einrichtung*) einschalten |
| Alle PCs melden nach Zertifikatstausch Pin-Fehler | Neuer Schlüssel unter `/etc/netprobe/tls/` = neuer Pin → alle PCs mit neuem Pin neu registrieren. Den Schlüssel daher nie neu erzeugen |

## Was netprobe von Wachtl nicht übernimmt

netprobe ist Zentrale für **unveränderte** Wachtl-Clients und implementiert deren Probe-API. Funktionen, die es nur
im eigenen Wachtl-Server gibt, fehlen hier: vorbelegtes Setup je Standort (stattdessen Befehl mit Parametern),
Netzwerkscan und Switch-Topologie (SNMP), eigene Wachtl-CA und die Seite `/zertifikat` (die Probe legt stattdessen
das selbstsignierte netprobe-Zertifikat als vertrauenswürdig ab, sofern es zum Pin passt).
