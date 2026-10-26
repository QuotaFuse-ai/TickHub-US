"""SEC-06：SEC 适配器的入参白名单，避免入参拼进 URL 造成路径越权或注入。"""

from __future__ import annotations

import pytest

from tickhub_us.core.errors import BadRequest
from tickhub_us.providers.sec_edgar import PROVIDER, parse_cik


@pytest.mark.parametrize("value", [0, -1, "-320193", "0", "cik:0"])
def test_parse_cik_rejects_non_positive(value: str | int) -> None:
    with pytest.raises(BadRequest):
        parse_cik(value)


@pytest.mark.parametrize(
    ("value", "expected"),
    [(320193, 320193), ("0000320193", 320193), ("cik:320193", 320193), ("CIK=320193", 320193)],
)
def test_parse_cik_accepts_positive(value: str | int, expected: int) -> None:
    assert parse_cik(value) == expected


@pytest.mark.parametrize("tag", ["Revenues/../../x", "Revenues?x=1", "", "A" * 65, "Revenues 1"])
def test_frames_rejects_unsafe_tag(tag: str) -> None:
    with pytest.raises(BadRequest):
        PROVIDER.frames(tag=tag)


@pytest.mark.parametrize("unit", ["USD/../x", "USD?x=1", "共享", "U" * 65])
def test_frames_rejects_unsafe_unit(unit: str) -> None:
    with pytest.raises(BadRequest):
        PROVIDER.frames(tag="Revenues", unit=unit)


def test_concept_rejects_unsafe_tag() -> None:
    with pytest.raises(BadRequest):
        PROVIDER.concept(symbol="AAPL", tag="../ticker")


def test_filing_index_encodes_form(monkeypatch: pytest.MonkeyPatch) -> None:
    recent = {
        "form": ["SC 13D/A", "10-K"],
        "filingDate": ["2026-01-02", "2026-01-03"],
        "accessionNumber": ["0000320193-26-000001", "0000320193-26-000002"],
        "reportDate": ["2026-01-01", "2025-12-31"],
        "acceptanceDateTime": ["2026-01-02T10:00:00.000Z", "2026-01-03T10:00:00.000Z"],
        "primaryDocument": ["d.htm", "e.htm"],
        "primaryDocDescription": ["x", "y"],
    }
    payload = {
        "filings": {"recent": recent},
        "tickers": ["AAPL"],
        "name": "Apple Inc.",
        "sicDescription": "Tech",
    }
    monkeypatch.setattr(type(PROVIDER), "_submissions", lambda self, cik: payload)

    out = PROVIDER.filings(cik="0000320193", form_type=" sc 13d/a ", limit=5)

    assert len(out["filings"]) == 1
    filing = out["filings"][0]
    assert "type=SC+13D%2FA" in filing["filing_index"]
    assert filing["url"] == "https://www.sec.gov/Archives/edgar/data/320193/000032019326000001/d.htm"
