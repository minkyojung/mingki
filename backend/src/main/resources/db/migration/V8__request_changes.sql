-- 제안 수정 요청 (docs/BACKEND_API.md 10.3). 확인 기록에 REQUEST_CHANGES 가 다시 생기고,
-- 제안 행은 (proposal_key, version, round) 로 구분한다. version 은 사람이 다시 쓰게 한 횟수, round 는 그 안의 근거 검사 회차다.
ALTER TABLE approvals DROP CONSTRAINT approvals_decision_check;
ALTER TABLE approvals ADD CONSTRAINT approvals_decision_check CHECK (decision IN ('APPROVE', 'REJECT', 'REQUEST_CHANGES'));

ALTER TABLE rule_proposals ADD COLUMN version           INT         NOT NULL DEFAULT 1 CHECK (version >= 1);
ALTER TABLE rule_proposals ADD COLUMN revision_outcome  VARCHAR(20) NOT NULL DEFAULT 'none'
    CHECK (revision_outcome IN ('none', 'revised', 'cannot_apply', 'needs_clarification', 'failed'));
ALTER TABLE rule_proposals ADD COLUMN revision_response TEXT;
ALTER TABLE rule_proposals ADD COLUMN changed_fields    JSONB       NOT NULL DEFAULT '[]'::jsonb;

ALTER TABLE rule_proposals DROP CONSTRAINT rule_proposals_task_id_proposal_key_round_key;
ALTER TABLE rule_proposals ADD CONSTRAINT rule_proposals_task_id_proposal_key_version_round_key
    UNIQUE (task_id, proposal_key, version, round);
