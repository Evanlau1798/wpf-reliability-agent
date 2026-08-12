from datetime import datetime, timedelta

from google.cloud import firestore

from app.agent import proposed_action_for_policy
from app.approval import next_proposal_version, validate_recovery_proposal
from app.contracts import sha256_canonical
from app.firestore_client import (
    APPROVALS_COLLECTION,
    EVIDENCE_COLLECTION,
    INCIDENTS_COLLECTION,
    PROCESSED_RUNS_COLLECTION,
    evidence_snapshot_hash,
)
from app.models import AgentDecision, ApprovalRecord, ApprovalStatus, RiskLevel
from app.policy import POLICY_VERSION
from app.workflow_state import (
    MAX_INVESTIGATION_ROUNDS,
    IncidentState,
    transition_incident_in_transaction,
)


def commit_proposed_action_run(
    client: firestore.Client,
    *,
    run_key: str,
    incident_id: str,
    evidence_revision: int,
    trigger: str,
    model_id: str,
    decision: AgentDecision,
    now: datetime,
) -> bool:
    proposal = validate_recovery_proposal(proposed_action_for_policy(decision))
    incident_document = client.collection(INCIDENTS_COLLECTION).document(incident_id)
    processed_document = client.collection(PROCESSED_RUNS_COLLECTION).document(run_key)

    @firestore.transactional
    def commit(transaction: firestore.Transaction) -> bool:
        if processed_document.get(transaction=transaction).exists:
            return False
        snapshot = incident_document.get(transaction=transaction)
        if not snapshot.exists:
            raise ValueError("Incident does not exist")
        incident = snapshot.to_dict() or {}
        state_version = incident.get("state_version")
        proposal_version = incident.get("proposal_version")
        proposal_count = incident.get("mutation_proposal_count")
        round_count = incident.get("investigation_round_count")
        last_revision = incident.get("last_investigated_evidence_revision")
        no_new_count = incident.get("no_new_evidence_count")
        app_session_id = incident.get("app_session_id")
        if type(state_version) is not int:
            raise ValueError("Incident state version is invalid")
        if type(proposal_version) is not int or proposal_version < 0:
            raise ValueError("Proposal version is invalid")
        if type(proposal_count) is not int or not 0 <= proposal_count < 1:
            raise ValueError("Mutation proposal limit reached")
        if type(round_count) is not int or not 0 <= round_count < MAX_INVESTIGATION_ROUNDS:
            raise ValueError("Investigation round limit reached")
        if last_revision is not None and (type(last_revision) is not int or last_revision < 0):
            raise ValueError("Last investigated evidence revision is invalid")
        if type(no_new_count) is not int or no_new_count < 0:
            raise ValueError("No-new-evidence count is invalid")
        if last_revision is not None and evidence_revision < last_revision:
            raise ValueError("Evidence revision is stale")
        if not isinstance(app_session_id, str) or not app_session_id:
            raise ValueError("Incident app session is invalid")

        material_evidence: list[tuple[str, str]] = []
        evidence_ids: set[str] = set()
        evidence_query = incident_document.collection(EVIDENCE_COLLECTION).order_by("__name__")
        for evidence_snapshot in transaction.get(evidence_query):
            evidence = evidence_snapshot.to_dict() or {}
            evidence_hash = evidence.get("evidence_hash")
            if not isinstance(evidence_hash, str):
                raise ValueError("Incident evidence hash is invalid")
            evidence_id = str(evidence_snapshot.id)
            evidence_ids.add(evidence_id)
            material_evidence.append((evidence_id, evidence_hash))
        if not set(proposal.evidence_ids).issubset(evidence_ids):
            raise ValueError("Proposal evidence does not exist")

        next_version = next_proposal_version(proposal_version)
        evidence_hash = evidence_snapshot_hash(material_evidence)
        arguments_hash = sha256_canonical(proposal.arguments)
        identity = {
            "incident_id": incident_id,
            "proposal_version": next_version,
            "evidence_snapshot_hash": evidence_hash,
            "arguments_hash": arguments_hash,
        }
        action_id = f"action-{sha256_canonical(identity)}"
        approval_id = f"approval-{sha256_canonical({**identity, 'action_id': action_id})}"
        approval = ApprovalRecord(
            schema_version="1.0",
            approval_id=approval_id,
            incident_id=incident_id,
            proposal_version=next_version,
            evidence_snapshot_hash=evidence_hash,
            action_id=action_id,
            tool=proposal.tool,
            canonical_arguments=proposal.arguments,
            canonical_arguments_hash=arguments_hash,
            target_app_session_id=app_session_id,
            policy_version=POLICY_VERSION,
            risk_level=RiskLevel.HIGH,
            expected_effect=proposal.expected_effect,
            rollback_plan=proposal.rollback_plan,
            expires_at_utc=now + timedelta(minutes=10),
            status=ApprovalStatus.PENDING,
        )
        transition_incident_in_transaction(
            transaction,
            incident_document=incident_document,
            expected_state=IncidentState.INVESTIGATING,
            expected_version=state_version,
            target_state=IncidentState.AWAITING_APPROVAL,
        )
        approval_document = incident_document.collection(APPROVALS_COLLECTION).document(approval_id)
        transaction.create(approval_document, approval.model_dump(mode="json"))
        next_no_new = 0 if last_revision is None or evidence_revision > last_revision else no_new_count + 1
        transaction.update(
            incident_document,
            {
                "proposal_version": next_version,
                "mutation_proposal_count": proposal_count + 1,
                "investigation_round_count": round_count + 1,
                "last_investigated_evidence_revision": evidence_revision,
                "no_new_evidence_count": next_no_new,
                "current_hypotheses": [item.model_dump(mode="json") for item in decision.hypotheses],
                "approval_id": approval_id,
                "pending_action_id": action_id,
                "updated_at": firestore.SERVER_TIMESTAMP,
            },
        )
        transaction.create(
            processed_document,
            {
                "incident_id": incident_id,
                "evidence_revision": evidence_revision,
                "trigger": trigger,
                "model_id": model_id,
                "processed_at": firestore.SERVER_TIMESTAMP,
            },
        )
        return True

    return commit(client.transaction())
