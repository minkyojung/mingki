package com.ruleripple.backend.task;

import jakarta.persistence.*;
import lombok.Getter;
import lombok.NoArgsConstructor;
import lombok.Setter;
import org.hibernate.annotations.JdbcTypeCode;
import org.hibernate.type.SqlTypes;

import java.time.LocalDate;
import java.time.OffsetDateTime;
import java.util.List;
import java.util.UUID;

/**
 * 상품별 변경 제안 (v2). (version, round) 마다 새 행이라 사람이 다시 쓰게 한 버전과 근거 검사 회차 이력이 남는다.
 * version 은 수정 요청(10.3)마다 1 씩 오르고, round 는 그 버전 안의 작성 회차(최대 2)다.
 */
@Entity
@Table(name = "rule_proposals")
@Getter
@Setter
@NoArgsConstructor
public class RuleProposal {

    public enum VerifyStatus { PENDING, PASSED, FAILED }

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(name = "task_id", nullable = false)
    private UUID taskId;

    @Column(name = "proposal_key", nullable = false, length = 100)
    private String proposalKey;

    @Column(nullable = false)
    private int version = 1;

    @Column(nullable = false)
    private int round;

    @Column(name = "req_key", length = 64)
    private String reqKey;

    @Column(name = "product_id", nullable = false, length = 10)
    private String productId;

    @Column(name = "rule_id", length = 10)
    private String ruleId;

    @Column(name = "rule_key", length = 50)
    private String ruleKey;

    /** change_required | keep | needs_check (Proposals.IMPACTS) */
    @Column(nullable = false, length = 20)
    private String impact;

    @Column(name = "current_text", columnDefinition = "text")
    private String currentText;

    @Column(name = "proposed_text", columnDefinition = "text")
    private String proposedText;

    @Column(length = 20)
    private String unit;

    @Column(name = "current_value", length = 50)
    private String currentValue;

    @Column(name = "proposed_value", length = 50)
    private String proposedValue;

    @Column(name = "effective_date")
    private LocalDate effectiveDate;

    @Column(length = 200)
    private String basis;

    @Column(nullable = false, columnDefinition = "text")
    private String reason;

    @Column(columnDefinition = "text")
    private String transition;

    @JdbcTypeCode(SqlTypes.JSON)
    @Column(name = "follow_up", nullable = false, columnDefinition = "jsonb")
    private List<String> followUp = List.of();

    @Enumerated(EnumType.STRING)
    @Column(name = "verify_status", nullable = false, length = 16)
    private VerifyStatus verifyStatus = VerifyStatus.PENDING;

    @Column(name = "verify_log", columnDefinition = "text")
    private String verifyLog;

    /** none | revised | cannot_apply | needs_clarification | failed (Proposals.REVISION_OUTCOMES) */
    @Column(name = "revision_outcome", nullable = false, length = 20)
    private String revisionOutcome = Proposals.NO_REVISION;

    @Column(name = "revision_response", columnDefinition = "text")
    private String revisionResponse;

    @JdbcTypeCode(SqlTypes.JSON)
    @Column(name = "changed_fields", nullable = false, columnDefinition = "jsonb")
    private List<String> changedFields = List.of();

    @Column(name = "created_at", nullable = false, insertable = false, updatable = false)
    private OffsetDateTime createdAt;
}
