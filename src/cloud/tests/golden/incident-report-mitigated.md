# Incident incident-1

## Incident Metadata
- Incident ID: incident-1
- Status: MITIGATED
- Severity: ERROR
- Confidence: HIGH

## Executive Summary
- The approved feature rollback reduced the observed impact.

## User Impact
- Not recorded in IncidentReport.

## Detection
- 2026-08-07T00:00:00+00:00 | DETECTED | sensor | evidence-before-1

## Timeline
- 2026-08-07T00:00:00+00:00 | DETECTED | sensor | evidence-before-1
- 2026-08-07T00:01:00+00:00 | VERIFIED | worker | evidence-after-1

## Evidence Index
- evidence-before-1 | binding.aggregate | High binding error rate.
- evidence-after-1 | performance.sample | Binding rate and frame p95 improved.

## Root-Cause Hypotheses
- None recorded.

## Confirmed/Candidate Root Cause
- Not recorded in IncidentReport.

## Diagnostic Tools Invoked
- Not recorded in IncidentReport.

## Temporary Mitigation
- Action: action-1
- Tool: recovery.set_feature_flag
- Approval: approval-1

## Permanent Engineering Recommendation
- Summary: Correct DisplayNmae to DisplayName and remove per-row animation.
- Source fix verified: false

## Risk Assessment
- Incident severity: ERROR
- Action risk: Not recorded in IncidentReport.

## Approval Record
- Approval ID: approval-1

## Executed Action
- Action ID: action-1
- Tool: recovery.set_feature_flag

## Before/After Verification
- binding\_errors\_per\_second: 100.0 -> 0.0 errors/s | evidence: evidence-before-1, evidence-after-1

## Rollback Information
- Not recorded in IncidentReport.

## Remaining Uncertainty
- Overall confidence: HIGH

## Reproduction Steps
- Not recorded in IncidentReport.

## Metadata
- Model: gemini-test
- Prompt version: 1
- Schema version: 1.0
- Policy version: 1
- Reuse revision: 900ac97cf9b69b4a3c1f4899b08c9b1e78212af3
