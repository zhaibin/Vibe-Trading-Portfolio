import json
import os
from pathlib import Path

import pytest
from packaging.version import Version

from vibe_portfolio.compatibility import (
    REQUIRED_ENDPOINTS,
    SUPPORTED_VIBE_VERSIONS,
    CompatibilityDiscovery,
    McpStatus,
)
from vibe_portfolio.config import Settings
from vibe_portfolio.vibe.gateway import VibeGateway


def test_pinned_baseline_matches_the_supported_public_contract() -> None:
    baseline_path = Path(__file__).parents[2] / "compatibility" / "baseline.json"
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))

    assert baseline["schema_version"] == "vibe-compatibility-baseline.v1"
    assert baseline["minimum"] == {
        "ref": "67a393e4574865e8ab9b1b3f9a9fd1d7ab337343",
        "version": "0.1.11",
    }
    assert baseline["stable"] == baseline["minimum"]
    assert baseline["latest"]["allowed_to_advance_support_range"] is False
    assert Version(baseline["minimum"]["version"]) in SUPPORTED_VIBE_VERSIONS
    assert Version("0.2.0") not in SUPPORTED_VIBE_VERSIONS
    assert baseline["required_endpoints"] == [
        [method, path] for _, method, path in REQUIRED_ENDPOINTS
    ]


def test_upstream_matrix_pins_minimum_and_stable_without_widening_latest() -> None:
    workflow_path = Path(__file__).parents[2] / ".github" / "workflows" / "upstream-compatibility.yml"
    workflow = workflow_path.read_text(encoding="utf-8")
    baseline_ref = "67a393e4574865e8ab9b1b3f9a9fd1d7ab337343"

    assert workflow.count(f"ref: {baseline_ref}") == 2
    assert "- name: latest\n            ref: main" in workflow
    assert "portfolio-compat-check --contract-only" in workflow
    assert "ALLOW_SESSION_MCP_SERVERS" not in workflow
    assert "mcpServers" not in workflow


@pytest.mark.contract
async def test_running_vibe_matches_supported_public_contract() -> None:
    base_url = os.environ.get("PORTFOLIO_VIBE_BASE_URL")
    if not base_url:
        pytest.skip("PORTFOLIO_VIBE_BASE_URL is not set")
    gateway = VibeGateway(Settings())
    try:
        report = await CompatibilityDiscovery(gateway).discover(McpStatus.NOT_CHECKED)
    finally:
        await gateway.close()

    assert report.contract_compatible, report.model_dump_json(indent=2)
    assert report.vibe_version is not None
    assert report.missing_capabilities == []
