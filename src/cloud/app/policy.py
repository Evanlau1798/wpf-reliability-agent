from app.models import DiagnosticTool, RiskLevel


READ_ONLY_DIAGNOSTIC_TOOLS = frozenset(
    {
        DiagnosticTool.HEALTH_GET_SNAPSHOT,
        DiagnosticTool.BINDING_GET_ERRORS,
        DiagnosticTool.BINDING_GET_LIVE_CANDIDATES,
        DiagnosticTool.EXCEPTION_GET_RECENT,
        DiagnosticTool.UI_GET_SUBTREE,
        DiagnosticTool.UI_GET_ELEMENT_DETAILS,
        DiagnosticTool.PERFORMANCE_SAMPLE,
        DiagnosticTool.STATE_COMPARE_SNAPSHOTS,
    }
)


def risk_for_tool(tool: DiagnosticTool | str) -> RiskLevel:
    try:
        normalized = tool if isinstance(tool, DiagnosticTool) else DiagnosticTool(tool)
    except ValueError:
        return RiskLevel.BLOCKED
    if normalized in READ_ONLY_DIAGNOSTIC_TOOLS:
        return RiskLevel.LOW
    if normalized is DiagnosticTool.RECOVERY_SET_FEATURE_FLAG:
        return RiskLevel.HIGH
    return RiskLevel.BLOCKED
