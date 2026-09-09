"""Tests für den VoIP-PCAP-Analyse-Service (ohne echtes tshark)."""

from __future__ import annotations

import pytest

from app.core.config import settings
from app.services import voip_analysis


def test_validate_capture_path_outside_dir(tmp_path):
    outside = tmp_path / "evil.pcap"
    outside.write_bytes(b"x")
    with pytest.raises(voip_analysis.VoipAnalysisError, match="außerhalb"):
        voip_analysis.validate_capture_path(outside)


def test_validate_capture_path_missing():
    inside = settings.capture_dir / "gibtsnicht.pcapng"
    with pytest.raises(voip_analysis.VoipAnalysisError, match="nicht gefunden"):
        voip_analysis.validate_capture_path(inside)


def test_analyze_capture_without_tshark_is_safe():
    settings.capture_dir.mkdir(parents=True, exist_ok=True)
    f = settings.capture_dir / "leer.pcapng"
    f.write_bytes(b"\xd4\xc3\xb2\xa1")  # pcap magic, sonst leer
    res = voip_analysis.analyze_capture(f)
    assert set(res) == {"sip", "codecs", "rtp", "error"}
    # tshark fehlt in der Testumgebung -> error gesetzt, keine Exception
    assert res["error"] is not None
    assert res["sip"] == {}


def test_resolve_capture_requires_arg():
    from app.db.session import session_scope

    with session_scope() as db, pytest.raises(voip_analysis.VoipAnalysisError):
        voip_analysis.resolve_capture(db)


def test_sip_fields_match_parser_field_count():
    # Die tshark-Feldliste muss zur festen Spaltenzahl im Parser passen.
    from app.services import sip_parsers

    assert len(voip_analysis.SIP_FIELDS) == sip_parsers._N_FIELDS
