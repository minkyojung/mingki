package com.ruleripple.backend.task;

import com.ruleripple.backend.auth.User;

import java.time.LocalDate;
import java.time.OffsetDateTime;
import java.util.List;
import java.util.Map;
import java.util.UUID;

/** 공개 API 응답 형태. 프론트 src/api/mappers.ts 가 이 모양을 도메인 타입으로 바꾼다. */
public final class TaskDtos {
    private TaskDtos() {}

    public static final int MAX_VERIFY_ROUNDS = 2;

    public record TaskSummary(UUID id, String title, String status, String currentNode, int verifyRound,
                              Long requesterId, OffsetDateTime createdAt, OffsetDateTime updatedAt) {
        public static TaskSummary of(AnalysisTask t) {
            return new TaskSummary(t.getId(), t.getTitle(), t.getStatus().name(), t.getCurrentNode(),
                    t.getVerifyRound(), t.getRequesterId(), t.getCreatedAt(), t.getUpdatedAt());
        }
    }

    public record DocumentMeta(String side, String versionLabel, String filename, String sha256) {
        public static DocumentMeta of(RegulationDocument d) {
            return new DocumentMeta(d.getSide().name(), d.getVersionLabel(), d.getFilename(), d.getSha256());
        }
    }

    /** 지금 사람이 해야 할 일. 없으면 null. */
    public record PendingAction(String step, String path) {
        public static PendingAction of(TaskStatus s, UUID id) {
            return s == TaskStatus.WAITING_REVIEW ? new PendingAction("REVIEW", "/tasks/" + id + "/review") : null;
        }
    }

    /** proposals: 현재 회차 제안 수. */
    public record Counts(int requirements, int proposals) {}

    public record TaskDetail(TaskSummary task, String errorMessage, int maxVerifyRounds,
                             List<DocumentMeta> documents, PendingAction pendingAction, Counts counts,
                             List<ApprovalView> approvals, List<DeliveryView> deliveries) {}

    public record RequirementView(Long id, String reqKey, String summary, String target, LocalDate effectiveDate,
                                  String transitional, String articleRef, String oldText, String newText,
                                  Map<String, Object> detail) {
        public static RequirementView of(Requirement r) {
            return new RequirementView(r.getId(), r.getReqKey(), r.getSummary(), r.getTarget(), r.getEffectiveDate(),
                    r.getTransitional(), r.getArticleRef(), r.getOldText(), r.getNewText(), r.getDetail());
        }
    }

    public record RequirementsResponse(TaskSummary task, List<DocumentMeta> documents, List<RequirementView> requirements) {}

    // ---------- 제안 (docs/BACKEND_API.md 10.2) ----------

    public record ApplyResult(String jobId, String status, String closedRuleId, String newRuleId) {}

    public record ProposalView(Long id, String proposalKey, String reqKey, String productId, String productName,
                               String ruleId, String ruleKey, String impact, String current, String proposed, String unit,
                               String currentValue, String proposedValue, LocalDate effectiveDate, String basis,
                               String reason, String transition, List<String> followUp, int version, int round,
                               String verifyStatus, String verifyLog, String revisionOutcome, String revisionResponse,
                               List<String> changedFields, boolean latest, String decision, ApplyResult apply) {
        /** productName 은 products 에서 붙인다 (없는 상품이면 null). decision: APPLY · EXCLUDE · null. apply 는 반영 작업이 생기면 채운다. */
        public static ProposalView of(RuleProposal p, String productName, boolean latest, String decision, ApplyResult apply) {
            return new ProposalView(p.getId(), p.getProposalKey(), p.getReqKey(), p.getProductId(), productName, p.getRuleId(),
                    p.getRuleKey(), p.getImpact(), p.getCurrentText(), p.getProposedText(), p.getUnit(),
                    p.getCurrentValue(), p.getProposedValue(), p.getEffectiveDate(), p.getBasis(), p.getReason(),
                    p.getTransition(), p.getFollowUp(), p.getVersion(), p.getRound(), p.getVerifyStatus().name(), p.getVerifyLog(),
                    p.getRevisionOutcome(), p.getRevisionResponse(), p.getChangedFields(), latest, decision, apply);
        }
    }

    /** canApprove=false 면 화면에서도 반영 버튼을 막는다 (서버도 409 로 막음). latestRound 는 현재 버전 안의 회차다. */
    public record ProposalsResponse(TaskSummary task, int maxVerifyRounds, int latestRound, boolean canApprove,
                                    List<ProposalView> proposals) {}

    // ---------- 수정 요청 (docs/BACKEND_API.md 10.3) ----------

    /** 접수 응답 (202). 접수이지 완료가 아니다. status 는 접수 직후의 작업 상태다. */
    public record RevisionAccepted(Long revisionId, String proposalId, int baseVersion, String status) {}

    /**
     * 수정 요청 하나의 접수 내용과 결과. state: processing(다시 쓰는 중) · done(새 버전이 있음) · rejected(Agent 가 받지 않음) · failed.
     * version · outcome · response · changedFields 는 새 버전의 첫 행에서 가져오고, 없으면 null.
     * proposals 는 그 제안 묶음의 모든 버전 · 회차 행이라 화면이 변경 전후를 비교할 수 있다.
     */
    public record RevisionView(Long revisionId, String proposalId, int baseVersion, String comment, Approver requestedBy,
                               OffsetDateTime createdAt, String state, Integer version, String outcome, String response,
                               List<String> changedFields, List<ProposalView> proposals) {}

    public record ContractImpactsResponse(TaskSummary task, List<Map<String, Object>> items) {}

    public record ApprovalView(Long id, String step, String decision, Long approverId, Approver approver,
                               String comment, Map<String, Object> payload, OffsetDateTime createdAt) {
        public static ApprovalView of(Approval a, User approver) {
            return new ApprovalView(a.getId(), a.getStep().name(), a.getDecision().name(), a.getApproverId(),
                    Approver.of(approver), a.getComment(), a.getPayload(), a.getCreatedAt());
        }
    }

    /** 승인자. 화면이 이름을 따로 조회하지 않아도 되게 같이 내려준다. */
    public record Approver(Long userId, String username, String displayName) {
        public static Approver of(User u) {
            return new Approver(u.getId(), u.getUsername(), u.getDisplayName());
        }
    }

    public record DeliveryView(Long id, String kind, String externalRef, String url, Long approvalId, OffsetDateTime createdAt) {
        public static DeliveryView of(Delivery d) {
            return new DeliveryView(d.getId(), d.getKind().name(), d.getExternalRef(), d.getUrl(), d.getApprovalId(), d.getCreatedAt());
        }
    }

    /** review: 가장 최근 확인 기록 (결정 · 사유 · 확인한 사람). 확인 전이면 null. */
    public record ReportResponse(TaskSummary task, Map<String, Object> report, ApprovalView review,
                                 List<DeliveryView> deliveries) {}

    public record EvaluationResponse(TaskSummary task, Map<String, Object> evaluation) {}

    public record EventView(int seq, String type, Map<String, Object> payload, OffsetDateTime createdAt) {
        public static EventView of(TaskEvent e) {
            return new EventView(e.getSeq(), e.getType(), e.getPayload(), e.getCreatedAt());
        }
    }
}
