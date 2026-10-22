package com.ruleripple.backend.task;

import java.time.LocalDate;
import java.util.*;

/**
 * 제안 규칙 (docs/BACKEND_API.md 10.2 · 10.3). 저장 · 확인 · 수정 요청 API 가 같은 판단을 쓰도록 한곳에 둔다.
 * DB 없이 테스트할 수 있게 행 목록만 받는다.
 */
public final class Proposals {

    private Proposals() {}

    public static final String CHANGE_REQUIRED = "change_required";
    public static final Set<String> IMPACTS = Set.of(CHANGE_REQUIRED, "keep", "needs_check");
    public static final String NO_REVISION = "none";
    public static final Set<String> REVISION_OUTCOMES = Set.of(NO_REVISION, "revised", "cannot_apply", "needs_clarification", "failed");

    /** Agent 가 보내는 제안 1건. (proposalKey, version, round) 기준 upsert. 수정 요청 칸 4개는 없어도 된다 (기본값). */
    public record Item(String proposalKey, String reqKey, String productId, String ruleId, String ruleKey,
                       String impact, String current, String proposed, String unit,
                       String currentValue, String proposedValue, LocalDate effectiveDate,
                       String basis, String reason, String transition, List<String> followUp,
                       Integer round, RuleProposal.VerifyStatus verifyStatus, String verifyLog,
                       Integer version, String revisionOutcome, String revisionResponse, List<String> changedFields) {

        public int versionOrDefault() {
            return version == null ? 1 : version;
        }

        public String revisionOutcomeOrDefault() {
            return revisionOutcome == null ? NO_REVISION : revisionOutcome;
        }
    }

    /** 계약 10.2 필드 규칙. 어긋나면 이유를, 맞으면 null 을 돌려준다. */
    public static String invalid(Item i) {
        if (blank(i.proposalKey())) return "proposalKey 는 필수입니다";
        String key = i.proposalKey();
        if (i.round() == null || i.round() < 1) return key + ": round 는 1 이상입니다";
        if (i.version() != null && i.version() < 1) return key + ": version 은 1 이상입니다";
        if (i.revisionOutcome() != null && !REVISION_OUTCOMES.contains(i.revisionOutcome())) {
            return key + ": revisionOutcome 은 none | revised | cannot_apply | needs_clarification | failed 입니다";
        }
        if (blank(i.productId())) return key + ": productId 는 필수입니다";
        if (!IMPACTS.contains(i.impact())) return key + ": impact 는 change_required | keep | needs_check 입니다";
        if (blank(i.reason())) return key + ": reason 은 필수입니다";
        if (CHANGE_REQUIRED.equals(i.impact())) {
            if (blank(i.ruleKey()) || blank(i.unit()) || blank(i.proposedValue()) || i.effectiveDate() == null) {
                return key + ": change_required 는 ruleKey · unit · proposedValue · effectiveDate 가 필요합니다";
            }
            if (!blank(i.ruleId()) && blank(i.currentValue())) {
                return key + ": 기존 규칙 변경은 currentValue 가 필요합니다";
            }
        }
        return null;
    }

    /**
     * 현재 제안. Agent 제안 묶음(reqKey × productId)마다 가장 높은 version, 그 안에서 가장 높은 round 의 행만 남긴다. 키 순서.
     * 다시 쓴 버전 · 회차에서 빠진 규칙 행은 이전 것에 남지만 현재 제안이 아니다. 키마다 최고를 고르면
     * 1회차에만 있던 FAILED 행이 계속 남아 승인을 영원히 막는다.
     */
    public static List<RuleProposal> latest(List<RuleProposal> all) {
        Map<String, Long> top = new HashMap<>();
        for (RuleProposal p : all) top.merge(bundle(p), rank(p), Math::max);
        Map<String, RuleProposal> byKey = new TreeMap<>();
        for (RuleProposal p : all) {
            if (rank(p) == top.get(bundle(p))) byKey.put(p.getProposalKey(), p);
        }
        return List.copyOf(byKey.values());
    }

    private static long rank(RuleProposal p) {
        return (long) p.getVersion() << 32 | p.getRound();
    }

    private static String bundle(RuleProposal p) {
        return p.getReqKey() + "|" + p.getProductId();
    }

    /** Agent 제안 ID (`{reqKey}:{productId}`). 수정 요청은 이 단위로 받는다 (10.3). */
    public static String proposalId(RuleProposal p) {
        return p.getReqKey() + ":" + p.getProductId();
    }

    /** 한 제안 묶음의 행. 유지(keep)는 Agent 제안이 아니라 영향 판단이라 다시 쓸 수 없다. */
    public static List<RuleProposal> revisable(List<RuleProposal> rows, String proposalId) {
        return rows.stream().filter(p -> proposalId(p).equals(proposalId) && !"keep".equals(p.getImpact())).toList();
    }

    public static List<String> changeKeys(List<RuleProposal> latest) {
        return latest.stream().filter(p -> CHANGE_REQUIRED.equals(p.getImpact())).map(RuleProposal::getProposalKey).toList();
    }

    /** 확인 대기 중이고, 근거 검사를 통과한 변경 제안이 1건 이상이다. 승인은 반영으로 고른 제안만 검사한다 (blocked). */
    public static boolean canApprove(TaskStatus status, List<RuleProposal> latest) {
        return status == TaskStatus.WAITING_REVIEW && latest.stream()
                .anyMatch(p -> CHANGE_REQUIRED.equals(p.getImpact()) && p.getVerifyStatus() == RuleProposal.VerifyStatus.PASSED);
    }

    /** 반영으로 고른 키 중 근거 검사를 통과하지 못한 것. 비어 있어야 승인할 수 있다. */
    public static List<String> blocked(List<RuleProposal> latest, Collection<String> approvedKeys) {
        return latest.stream()
                .filter(p -> approvedKeys.contains(p.getProposalKey()) && p.getVerifyStatus() != RuleProposal.VerifyStatus.PASSED)
                .map(RuleProposal::getProposalKey).toList();
    }

    /** 반려는 작업을 끝내고 수정 요청은 AI 에게 줄 지시라서 둘 다 글이 필수다. 사유는 리포트 응답의 review 에 나온다 (10.3). */
    public static boolean missingComment(Approval.Decision decision, String comment) {
        return (decision == Approval.Decision.REJECT || decision == Approval.Decision.REQUEST_CHANGES) && blank(comment);
    }

    static boolean blank(String s) {
        return s == null || s.isBlank();
    }
}
