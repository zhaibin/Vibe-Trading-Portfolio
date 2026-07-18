from __future__ import annotations

import json
from typing import Any

from pytest import CaptureFixture, MonkeyPatch

from vibe_portfolio.cli import compatibility as compatibility_cli
from vibe_portfolio.compatibility import AnalysisMode, CompatibilityReport, CompatibilityState, McpStatus


class FakeGateway:
    closed = False

    def __init__(self, settings: Any) -> None:
        self.settings = settings

    async def close(self) -> None:
        type(self).closed = True


class FakeDiscovery:
    report: CompatibilityReport

    def __init__(self, gateway: FakeGateway) -> None:
        self.gateway = gateway

    async def discover(self, mcp_status: McpStatus) -> CompatibilityReport:
        assert mcp_status is McpStatus.NOT_CHECKED
        return self.report


def install_report(monkeypatch: MonkeyPatch, report: CompatibilityReport) -> None:
    FakeGateway.closed = False
    FakeDiscovery.report = report
    monkeypatch.setattr(compatibility_cli, "VibeGateway", FakeGateway)
    monkeypatch.setattr(compatibility_cli, "CompatibilityDiscovery", FakeDiscovery)


async def test_contract_only_emits_json_and_succeeds_for_degraded_provider(
    monkeypatch: MonkeyPatch,
    capsys: CaptureFixture[str],
) -> None:
    install_report(
        monkeypatch,
        CompatibilityReport(
            state=CompatibilityState.DEGRADED,
            analysis_mode=AnalysisMode.DISABLED,
            contract_compatible=True,
            deep_analysis_enabled=False,
            vibe_version="0.1.11",
            mcp_status=McpStatus.NOT_CHECKED,
            reasons=["vibe_not_ready"],
        ),
    )

    exit_code = await compatibility_cli._check(contract_only=True)

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["state"] == "degraded"
    assert payload["analysis_mode"] == "disabled"
    assert payload["contract_compatible"] is True
    assert "api_key" not in payload
    assert FakeGateway.closed is True


async def test_default_check_fails_when_deep_analysis_is_disabled(
    monkeypatch: MonkeyPatch,
    capsys: CaptureFixture[str],
) -> None:
    install_report(
        monkeypatch,
        CompatibilityReport(
            state=CompatibilityState.DEGRADED,
            analysis_mode=AnalysisMode.DISABLED,
            contract_compatible=True,
            deep_analysis_enabled=False,
            vibe_version="0.1.11",
            mcp_status=McpStatus.NOT_CHECKED,
            reasons=["vibe_not_ready"],
        ),
    )

    exit_code = await compatibility_cli._check(contract_only=False)

    assert exit_code == 2
    assert json.loads(capsys.readouterr().out)["deep_analysis_enabled"] is False
    assert FakeGateway.closed is True
