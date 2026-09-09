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


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
