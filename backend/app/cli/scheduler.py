"""CLI-Unterbefehle für den globalen Scheduler.

Schreiben/lesen direkt die Konfiguration (KV-Store bzw. Zielobjekte) in der
Datenbank. Läuft der Dienst bereits in einem anderen Prozess, übernimmt dieser
die Änderung an ``scheduler-enable``/``scheduler-disable`` erst nach einem
Neustart (keine Live-IPC zum laufenden Prozess) – für sofortige Wirkung die
Web-UI/API desselben laufenden Prozesses verwenden.
"""

from __future__ import annotations

import argparse

from app.core.config import settings
from app.db.session import session_scope
from app.models.health_check import HealthProbe
from app.models.traceroute import TracerouteTarget
from app.models.voip import VoipServer
from app.models.wan import WanTarget
from app.services import kvstore


def scheduler_status(args: argparse.Namespace) -> None:
    with session_scope() as db:
        enabled = kvstore.get_value(db, "scheduler_enabled", settings.scheduler_enabled)
        sp_enabled = kvstore.get_value(db, "speedtest_enabled", settings.speedtest_enabled)
        sp_interval = kvstore.get_value(db, "speedtest_interval_min", settings.speedtest_interval_min)
        rt_enabled = kvstore.get_value(db, "retrans_enabled", settings.retrans_enabled)
        rt_interval = kvstore.get_value(
            db, "retrans_scheduled_interval_min", settings.retrans_scheduled_interval_min
        )
        wan_n = db.query(WanTarget).filter(WanTarget.enabled.is_(True)).count()
        voip_n = db.query(VoipServer).filter(VoipServer.enabled.is_(True)).count()
        tr_n = db.query(TracerouteTarget).filter(TracerouteTarget.enabled.is_(True)).count()
        hc_n = db.query(HealthProbe).filter(HealthProbe.enabled.is_(True)).count()

    print(f"Automatisierung: {'aktiv' if enabled else 'pausiert'}")
    print(f"  Speedtest:               {'an' if sp_enabled else 'aus'}, alle {sp_interval} min")
    rt_on = rt_enabled and rt_interval > 0
    print(f"  Retransmission-Monitor:  {'an' if rt_on else 'aus'}, alle {rt_interval} min")
    print(f"  WAN-Ziele aktiv:         {wan_n}")
    print(f"  VoIP-Server aktiv:       {voip_n}")
    print(f"  Traceroute-Ziele aktiv:  {tr_n}")
    print(f"  Health-Probes aktiv:     {hc_n}")


def scheduler_enable(args: argparse.Namespace) -> None:
    with session_scope() as db:
        kvstore.set_value(db, "scheduler_enabled", True)
    print(
        "Automatisierung aktiviert. Wirkt sofort, falls über die API/UI desselben laufenden "
        "Prozesses umgeschaltet; ansonsten nach dem nächsten Neustart."
    )


def scheduler_disable(args: argparse.Namespace) -> None:
    with session_scope() as db:
        kvstore.set_value(db, "scheduler_enabled", False)
    print(
        "Automatisierung pausiert. Wirkt sofort, falls über die API/UI desselben laufenden "
        "Prozesses umgeschaltet; ansonsten nach dem nächsten Neustart."
    )
