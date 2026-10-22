package com.ruleripple.backend.task;

import jakarta.persistence.*;
import lombok.Getter;
import lombok.NoArgsConstructor;
import org.hibernate.annotations.JdbcTypeCode;
import org.hibernate.type.SqlTypes;

import java.time.OffsetDateTime;
import java.util.Map;
import java.util.UUID;

/**
 * 사람 확인 기록. 반영 요청(apply_rule_version)은 이 id 를 approvalId 로 받아 서버가 다시 검증한다.
 * 그래프의 interrupt 만 믿지 않는다.
 */
@Entity
@Table(name = "approvals")
@Getter
@NoArgsConstructor
public class Approval {

    /** 확인 단계. 사람 확인은 제안 검토 한 번뿐이다. Agent /resume 에 그대로 보낸다. */
    public enum Step { REVIEW }

    /** APPROVE · REJECT 는 최종 결정, REQUEST_CHANGES 는 제안 하나를 AI 가 다시 쓰게 하는 중간 요청 (10.3). */
    public enum Decision { APPROVE, REJECT, REQUEST_CHANGES }

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(name = "task_id", nullable = false)
    private UUID taskId;

    @Enumerated(EnumType.STRING)
    @Column(nullable = false, length = 16)
    private Step step;

    @Enumerated(EnumType.STRING)
    @Column(nullable = false, length = 20)
    private Decision decision;

    @Column(name = "approver_id", nullable = false)
    private Long approverId;

    @Column(columnDefinition = "text")
    private String comment;

    @JdbcTypeCode(SqlTypes.JSON)
    @Column(nullable = false, columnDefinition = "jsonb")
    private Map<String, Object> payload;

    @Column(name = "created_at", nullable = false, insertable = false, updatable = false)
    private OffsetDateTime createdAt;

    public Approval(UUID taskId, Step step, Decision decision, Long approverId, String comment, Map<String, Object> payload) {
        this.taskId = taskId;
        this.step = step;
        this.decision = decision;
        this.approverId = approverId;
        this.comment = comment;
        this.payload = payload == null ? Map.of() : payload;
    }
}
