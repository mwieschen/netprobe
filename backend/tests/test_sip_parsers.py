"""Unit-Tests für die SIP-/SDP-/RTP-Parser."""
# ruff: noqa: E501

from __future__ import annotations

from app.services import sip_parsers


# Feldreihenfolge = voip_analysis.SIP_FIELDS:
# time_epoch, ip.src, ip.dst, Method, Status-Code, Status-Line, Call-ID, CSeq,
# Server, User-Agent, Allow, resend, sdp.media, sdp.media_attr
def _line(**kw) -> str:
    order = ["ts", "src", "dst", "method", "status", "status_line", "call_id", "cseq",
             "server", "ua", "allow", "resend", "sdp_media", "sdp_attr"]
    return "\t".join(str(kw.get(k, "")) for k in order)


def test_parse_sip_messages_options_exchange():
    lines = [
        _line(ts="1.0", src="10.0.0.5", dst="1.2.3.4", method="OPTIONS", call_id="abc", cseq="1 OPTIONS"),
        _line(ts="1.05", src="1.2.3.4", dst="10.0.0.5", status="200", status_line="Status-Line: SIP/2.0 200 OK",
              call_id="abc", cseq="1 OPTIONS", server="3CXPhoneSystem/18.0.0",
              allow="INVITE, ACK, CANCEL, BYE, OPTIONS, REGISTER"),
    ]
    out = sip_parsers.parse_sip_messages(lines)
    assert out["messages_captured"] == 2
    assert out["by_method"]["OPTIONS"] == 1
    assert out["by_status_class"]["2xx"] == 1
    assert out["error_count"] == 0
    assert "3CXPhoneSystem/18.0.0" in out["server_headers"]
    assert "REGISTER" in out["allow_methods"]
    assert out["dialogs"][0]["cseq_ok"] is True


def test_parse_sip_messages_errors_and_retransmission():
    lines = [
        _line(method="INVITE", status="", call_id="c1", cseq="1 INVITE"),
        _line(method="INVITE", resend="1", call_id="c1", cseq="1 INVITE"),
        _line(status="403", status_line="SIP/2.0 403 Forbidden", call_id="c1", cseq="1 INVITE"),
        _line(status="503", status_line="SIP/2.0 503 Service Unavailable", call_id="c2", cseq="1 OPTIONS"),
    ]
    out = sip_parsers.parse_sip_messages(lines)
    assert out["retransmissions"] == 1
    assert out["error_count"] == 2
    codes = {e["code"] for e in out["errors"]}
    assert codes == {403, 503}
    assert out["by_status_class"] == {"4xx": 1, "5xx": 1}


def test_parse_sip_messages_sdp_codecs():
    attr = ("rtpmap:111 opus/48000/2,rtpmap:8 PCMA/8000,rtpmap:0 PCMU/8000,"
            "fmtp:111 minptime=10;useinbandfec=1,ptime:20,sendrecv")
    lines = [_line(method="INVITE", call_id="c1", cseq="1 INVITE",
                   sdp_media="audio 5004 RTP/SAVP 111 8 0", sdp_attr=attr)]
    out = sip_parsers.parse_sip_messages(lines)
    assert "opus/48000/2" in out["rtpmaps"]
    assert out["secure_rtp"] is True
    assert 20 in out["ptimes"]
    assert "sendrecv" in out["directions"]

    cs = sip_parsers.codec_summary(out)
    assert cs["codecs_offered"][0] == "opus/48000/2"
    assert cs["primary_codec"] == "opus/48000/2"
    assert cs["estimated_bandwidth_kbit"] == sip_parsers.CODEC_BANDWIDTH_KBIT["OPUS"]
    assert cs["ptime_ms"] == 20
    assert cs["secure"] is True


_RTP_TABLE = """\
=========================================================================
                             RTP Streams
      Src IP addr  Port     Dest IP addr  Port        SSRC          Payload  Pkts       Lost   Max Delta(ms)  Max Jitter(ms)  Mean Jitter(ms)  Problems?
        10.0.0.5  40000         1.2.3.4  5004  0x1A2B3C4D  g711U            1500    3 (0.2%)          25.10            4.20             1.10  X
         1.2.3.4   5004        10.0.0.5  40000  0x5E6F7A8B  Opus             1502    0 (0.0%)          21.00            2.10             0.90
=========================================================================
"""


def test_parse_rtp_streams_and_summary():
    streams = sip_parsers.parse_rtp_streams(_RTP_TABLE)
    assert len(streams) == 2
    assert streams[0]["ssrc"] == "0x1A2B3C4D"
    assert streams[0]["codec"] == "PCMU"
    assert streams[0]["lost"] == 3
    assert streams[0]["lost_pct"] == 0.2
    assert streams[1]["codec"] == "OPUS"

    summ = sip_parsers.rtp_summary(streams)
    assert summ["streams_detected"] == 2
    assert summ["packet_loss_percent"] == 0.2
    assert summ["jitter_estimate_ms"] == 1.1
    assert set(summ["payload_types"]) == {"PCMU", "OPUS"}


def test_rtp_summary_empty():
    s = sip_parsers.rtp_summary([])
    assert s["streams_detected"] == 0
    assert s["jitter_estimate_ms"] is None


def test_codec_summary_static_payload_fallback():
    out = sip_parsers.parse_sip_messages([_line(sdp_media="audio 8000 RTP/AVP 0 8")])
    cs = sip_parsers.codec_summary(out)
    assert "PCMU" in cs["codecs_offered"]
    assert "PCMA" in cs["codecs_offered"]
