"""Zentrale, typsichere Konfiguration über Pydantic Settings.

Alle Werte sind über Umgebungsvariablen (Präfix ``NETPROBE_``) bzw. die Datei
``/etc/netprobe/netprobe.env`` konfigurierbar.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Standard-Pfad der Env-Datei; kann via NETPROBE_ENV_FILE überschrieben werden.
_ENV_FILE = Path("/etc/netprobe/netprobe.env")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="NETPROBE_",
        env_file=str(_ENV_FILE) if _ENV_FILE.exists() else None,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Allgemein ---
    app_name: str = "netprobe"
    environment: str = "production"
    timezone: str = "Europe/Berlin"

    # Geheimschlüssel für Cookie-Signierung. MUSS in Produktion gesetzt werden.
    secret_key: str = Field(default="change-me-in-production")

    # --- Scheduler (globale Automatisierung) ---
    # Startwert; zur Laufzeit per /api/scheduler/enable|disable im KV-Store überschrieben
    # (siehe app.workers.scheduler.SchedulerService, analog zu speedtest_enabled).
    scheduler_enabled: bool = True

    # --- Netzwerk / HTTP ---
    host: str = "127.0.0.1"
    port: int = 8000
    # true, wenn hinter HTTPS-Reverse-Proxy -> Secure-Cookie
    https_enabled: bool = False
    # Erlaubte Origins für CORS (leer = keine Cross-Origin-Requests)
    cors_origins: str = ""

    # --- Pfade ---
    data_dir: Path = Path("/var/lib/netprobe")
    capture_dir: Path = Path("/var/lib/netprobe/captures")
    log_dir: Path = Path("/var/log/netprobe")
    frontend_dist: Path = Path("/opt/netprobe/frontend/dist")

    # --- Datenbank ---
    database_url: str = "sqlite:////var/lib/netprobe/db.sqlite"

    # --- Externe Tools ---
    speedtest_bin: str = "speedtest"
    fping_bin: str = "fping"
    ping_bin: str = "ping"
    dumpcap_bin: str = "dumpcap"
    tshark_bin: str = "tshark"
    ip_bin: str = "ip"

    # --- Speedtest ---
    speedtest_interval_min: int = 60
    speedtest_server_id: int | None = None
    speedtest_enabled: bool = True

    # --- Capture-Limits ---
    capture_default_duration_s: int = 60
    capture_max_duration_s: int = 900  # 15 Minuten
    capture_default_size_mb: int = 100
    capture_max_size_mb: int = 500
    capture_retention_days: int = 14

    # --- Langzeit-Captures (Ringpuffer) ---
    capture_job_max_filesize_mb: int = 1000
    capture_job_max_files: int = 200
    # Obergrenze für den Plattenverbrauch eines Jobs (max_filesize_mb * max_files).
    capture_job_max_total_mb: int = 51_200  # 50 GiB

    # --- iperf3 ---
    iperf3_bin: str = "iperf3"
    iperf3_server_port: int = 5201

    # --- MTU-Test ---
    mtu_min: int = 68
    mtu_max: int = 9216

    # --- VoIP / 3CX ---
    voip_retention_days: int = 365
    # Optionaler paralleler PCAP-Mitschnitt während eines VoIP-Tests.
    voip_capture_bpf_default: str = "port 5060 or port 5061 or port 5090"
    voip_capture_default_duration_s: int = 10

    # --- System-Health-Checks ---
    health_check_retention_days: int = 90

    # --- TCP-Retransmission-Monitoring ---
    retrans_enabled: bool = True
    # "auto" = Interface der Default-Route; ein gesetztes, aber fehlendes/inaktives
    # Interface fällt ebenfalls darauf zurück (capture_service.resolve_default_interface).
    retrans_default_interface: str = "auto"
    retrans_default_duration_s: int = 60
    retrans_max_duration_s: int = 600  # 10 Minuten
    retrans_retention_days: int = 90
    # Schwellwerte für Warnungen/Alarme (Retransmission-Rate in %).
    retrans_warn_threshold_pct: float = 0.5
    retrans_alert_threshold_pct: float = 1.0
    # Geplantes periodisches Monitoring auf retrans_default_interface (Minuten, 0 = aus).
    retrans_scheduled_interval_min: int = 0

    # --- Traceroute / Geolocation ---
    mtr_bin: str = "mtr"
    traceroute_bin: str = "traceroute"
    traceroute_retention_days: int = 90
    traceroute_max_hops_limit: int = 60
    traceroute_timeout_s: int = 120
    # Geo/ASN-Auflösung der Hops. Hybrid: erst lokale MaxMind-DB, dann – falls
    # aktiviert – ip-api.com (HTTP). Bei Deaktivierung bleiben die Hops ohne Geo.
    geoip_enabled: bool = True
    geoip_online_enabled: bool = True
    geoip_db_city: Path = Path("/var/lib/netprobe/geoip/GeoLite2-City.mmdb")
    geoip_db_asn: Path = Path("/var/lib/netprobe/geoip/GeoLite2-ASN.mmdb")
    geoip_endpoint: str = "http://ip-api.com/batch"
    geoip_cache_days: int = 30
    # Obergrenze ip-api.com Free: 45 Anfragen/Minute.
    geoip_online_rate_per_min: int = 40

    # --- Retention ---
    ping_raw_retention_days: int = 14
    ping_agg_retention_days: int = 365
    result_retention_days: int = 365

    # --- Analyse-Limits ---
    tshark_timeout_s: int = 120
    display_filter_max_packets: int = 1000
    capinfos_bin: str = "capinfos"
    # Obergrenze für die Grundanalyse: es werden höchstens so viele Pakete vom
    # Dateianfang ausgewertet (Kopf-Sample). Für größere Captures wird die Analyse
    # als "sampled" markiert.
    analysis_max_packets: int = 200_000
    # Anzahl Einträge in Top-Listen (Protokolle, Conversations).
    analysis_top_n: int = 20

    # --- Analyse-Schwellwerte für Warnungen ---
    analysis_broadcast_warn_pct: float = 10.0
    analysis_multicast_warn_pct: float = 30.0
    analysis_tcp_retransmission_warn_pct: float = 5.0
    analysis_tcp_reset_warn: int = 50

    # --- Reports ---
    reports_dir: Path = Path("/var/lib/netprobe/reports")
    # Zeitfenster, aus dem aktive Testergebnisse in einen Report übernommen werden.
    report_test_lookback_hours: int = 168  # 7 Tage
    # Fenster für die Dauerping-Auswertung im Report.
    report_ping_window_hours: int = 24

    # --- Schwellwerte für Report-Warnungen (aktive Tests) ---
    report_ping_loss_warn_pct: float = 1.0
    report_udp_loss_warn_pct: float = 1.0
    # Retransmissions je iperf3-TCP-Test (absolut), ab dem gewarnt wird.
    report_tcp_retransmit_warn: int = 100
    report_jitter_warn_ms: float = 30.0
    # Pfad-MTU, unter der auf mögliche PMTUD-/Fragmentierungsprobleme hingewiesen wird.
    report_mtu_info_below: int = 1500

    # --- Network Diagnostics (Sammel-Test mehrerer Ziele) ---
    # Alle übrigen Schwellwerte (Paketverlust, Jitter, Retransmission-Rate, MTU,
    # Broadcast-Anteil) werden bewusst von den bereits existierenden Einstellungen
    # oben wiederverwendet (report_ping_loss_warn_pct, report_jitter_warn_ms,
    # retrans_warn_threshold_pct/retrans_alert_threshold_pct, report_mtu_info_below,
    # analysis_broadcast_warn_pct) statt einer zweiten, potenziell abweichenden
    # Schwellwert-Quelle. Nur die Ping-Latenz hat bislang noch keinen Schwellwert.
    diagnostics_ping_latency_warn_ms: float = 100.0
    diagnostics_max_targets: int = 10

    # --- Wachtl-Clients (netprobe als Zentrale, Probe-API v1) ---
    # Startwert; zur Laufzeit per /api/clients/settings im KV-Store überschrieben (wachtl_enabled).
    # Aus: /api/probe/v1/* antwortet mit 404, die Probes puffern bis zu 7 Tage.
    wachtl_enabled: bool = False
    # Zertifikat der nginx-HTTPS-Site; daraus wird der SPKI-Pin für das Setup berechnet.
    wachtl_tls_cert: Path = Path("/etc/netprobe/tls/fullchain.pem")
    # Unverändertes Wachtl-Setup aus dem Release-Paket (install.sh legt es hier ab, Download unter
    # /api/clients/setup). Inhalt: WachtlSetup-<Version>.exe, SHA256SUMS.txt, VERSION, LIESMICH.md, LICENSE.
    wachtl_client_dir: Path = Path("/opt/netprobe/clients/wachtl")
    # Adressen, unter denen Probes netprobe erreichen (kommagetrennt, https://…); leer = keine Vorgabe.
    wachtl_server_urls: str = ""
    # Untergrenze für Check-Intervalle (Sekunden): 1-s-Pings vieler Clients sprengen SQLite.
    wachtl_min_interval_s: int = 5
    wachtl_upload_interval_s: int = 10
    wachtl_heartbeat_interval_s: int = 30
    wachtl_config_poll_s: int = 300
    # 1-min-Buckets so lange, danach zu 1-h-Buckets verdichtet; diese bis wachtl_retention_days.
    wachtl_minute_retention_days: int = 14
    wachtl_retention_days: int = 365
    # Obergrenzen für einen Upload (komprimiert / entpackt).
    wachtl_max_body_bytes: int = 2 * 1024 * 1024
    wachtl_max_ingest_bytes: int = 8 * 1024 * 1024
    # Client-Hinweise (Kabel, Link, Treiber …) erkennen/senden und als Sprechblase am PC zeigen.
    wachtl_client_findings_enabled: bool = True
    wachtl_client_findings_notify: bool = True
    # Ab diesem Verlust (in %) einer Minute gilt ein Check in der Ausfallanalyse als gestört.
    wachtl_outage_loss_pct: float = 25.0
    # Längster Zeitraum der Ausfallanalyse (nur 1-min-Buckets sind genau genug).
    wachtl_incident_max_days: int = 14
    # Störungsmeldung startet automatisch einen netprobe-Diagnose-Lauf (Ping/MTU/Traceroute
    # zu den Zielen des Standorts und zum PC); höchstens einer je Standort und Sperrzeit.
    wachtl_report_diagnostics: bool = True
    wachtl_diagnostics_cooldown_min: int = 10
    # Aufbewahrung von Meldungen, Ereignissen und behobenen Hinweisen
    wachtl_event_retention_days: int = 365
    # RTP-Reflektor (Gegenstelle für Sprachtests, UDP); Startwert, zur Laufzeit im KV-Store.
    wachtl_rtp_enabled: bool = True
    wachtl_rtp_port: int = 5004
    # Adresse von netprobe für iperf3-/Sprachtests der Probes; leer = Host aus der Anfrage der Probe.
    wachtl_peer_host: str = ""
    # Ergebnisse geplanter Messungen (Pfad, Bandbreite, Sprache)
    wachtl_result_retention_days: int = 365

    # --- Login / Rate Limiting ---
    login_rate_max_attempts: int = 5
    login_rate_window_s: int = 300
    session_max_age_s: int = 60 * 60 * 12  # 12 Stunden

    # --- Erst-Admin (nur beim allerersten Start ausgewertet) ---
    admin_user: str | None = None
    admin_password: str | None = None

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def wachtl_server_url_list(self) -> list[str]:
        return [u.strip().rstrip("/") for u in self.wachtl_server_urls.split(",")
                if u.strip().startswith("https://")]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
