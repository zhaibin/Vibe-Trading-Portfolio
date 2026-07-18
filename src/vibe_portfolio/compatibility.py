from enum import StrEnum
from typing import Protocol

from packaging.specifiers import SpecifierSet
from packaging.version import InvalidVersion, Version
from pydantic import BaseModel, Field

from vibe_portfolio.vibe.errors import GatewayError
from vibe_portfolio.vibe.models import ApiInfo, ProbeResult

SUPPORTED_VIBE_VERSIONS = SpecifierSet(">=0.1.11,<0.2.0")


class CompatibilityState(StrEnum):
    COMPATIBLE = "compatible"
    DEGRADED = "degraded"
    UNSUPPORTED = "unsupported"


class AnalysisMode(StrEnum):
    FULL_MCP = "full_mcp"
    BOUNDED_CONTEXT = "bounded_context"
    DISABLED = "disabled"


class McpStatus(StrEnum):
    NOT_CHECKED = "not_checked"
    AVAILABLE = "available"
    MISSING = "missing"
    FAILED = "failed"


REQUIRED_ENDPOINTS: tuple[tuple[str, str, str], ...] = (
    ("sessions.create", "POST", "/sessions"),
    ("goals.create_research", "POST", "/sessions/{session_id}/goal"),
    ("messages.send", "POST", "/sessions/{session_id}/messages"),
    ("messages.poll", "GET", "/sessions/{session_id}/messages"),
    ("events.stream", "GET", "/sessions/{session_id}/events"),
    ("events.ticket", "POST", "/auth/sse-ticket"),
    ("sessions.cancel", "POST", "/sessions/{session_id}/cancel"),
)


class DiscoveryGateway(Protocol):
    async def api_info(self) -> ApiInfo:
        ...

    async def openapi(self) -> dict[str, object]:
        ...

    async def live(self) -> ProbeResult:
        ...

    async def ready(self) -> ProbeResult:
        ...


class CompatibilityReport(BaseModel):
    state: CompatibilityState
    analysis_mode: AnalysisMode
    contract_compatible: bool
    deep_analysis_enabled: bool
    vibe_version: str | None = None
    supported_versions: str = str(SUPPORTED_VIBE_VERSIONS)
    mcp_status: McpStatus
    missing_capabilities: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)


class CompatibilityDiscovery:
    def __init__(self, gateway: DiscoveryGateway) -> None:
        self.gateway = gateway

    async def discover(self, mcp_status: McpStatus = McpStatus.NOT_CHECKED) -> CompatibilityReport:
        try:
            info = await self.gateway.api_info()
            spec = await self.gateway.openapi()
            await self.gateway.live()
            readiness = await self.gateway.ready()
        except GatewayError as exc:
            return CompatibilityReport(
                state=CompatibilityState.DEGRADED,
                analysis_mode=AnalysisMode.DISABLED,
                contract_compatible=False,
                deep_analysis_enabled=False,
                mcp_status=mcp_status,
                reasons=[exc.code.value.lower()],
            )

        reasons: list[str] = []
        try:
            version_supported = Version(info.version) in SUPPORTED_VIBE_VERSIONS
        except InvalidVersion:
            version_supported = False
        if not version_supported:
            reasons.append("version_out_of_range")

        raw_paths = spec.get("paths")
        paths = raw_paths if isinstance(raw_paths, dict) else {}
        missing = [
            capability
            for capability, method, path in REQUIRED_ENDPOINTS
            if not isinstance(paths.get(path), dict) or method.lower() not in paths[path]
        ]

        if not version_supported or missing:
            return CompatibilityReport(
                state=CompatibilityState.UNSUPPORTED,
                analysis_mode=AnalysisMode.DISABLED,
                contract_compatible=False,
                deep_analysis_enabled=False,
                vibe_version=info.version,
                mcp_status=mcp_status,
                missing_capabilities=missing,
                reasons=reasons + (["required_capability_missing"] if missing else []),
            )

        if not readiness.ok:
            return CompatibilityReport(
                state=CompatibilityState.DEGRADED,
                analysis_mode=AnalysisMode.DISABLED,
                contract_compatible=True,
                deep_analysis_enabled=False,
                vibe_version=info.version,
                mcp_status=mcp_status,
                reasons=["vibe_not_ready"],
            )

        if mcp_status is McpStatus.AVAILABLE:
            return CompatibilityReport(
                state=CompatibilityState.COMPATIBLE,
                analysis_mode=AnalysisMode.FULL_MCP,
                contract_compatible=True,
                deep_analysis_enabled=True,
                vibe_version=info.version,
                mcp_status=mcp_status,
            )

        mcp_reason = {
            McpStatus.NOT_CHECKED: "mcp_not_verified",
            McpStatus.MISSING: "mcp_not_configured",
            McpStatus.FAILED: "mcp_probe_failed",
        }[mcp_status]
        return CompatibilityReport(
            state=CompatibilityState.DEGRADED,
            analysis_mode=AnalysisMode.BOUNDED_CONTEXT,
            contract_compatible=True,
            deep_analysis_enabled=True,
            vibe_version=info.version,
            mcp_status=mcp_status,
            reasons=[mcp_reason],
        )
