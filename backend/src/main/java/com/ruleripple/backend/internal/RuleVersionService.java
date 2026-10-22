package com.ruleripple.backend.internal;

import com.ruleripple.backend.queue.Job;
import com.ruleripple.backend.queue.JobPublisher;
import com.ruleripple.backend.queue.JobRepository;
import com.ruleripple.backend.queue.PermanentJobFailure;
import com.ruleripple.backend.task.*;
import org.springframework.http.HttpStatus;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.server.ResponseStatusException;

import java.time.LocalDate;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.Optional;
import java.util.UUID;

/**
 * 상품 규칙 반영 (docs/BACKEND_API.md 10.4).
 * Agent 는 값을 보내지 않는다. 사용자가 확인한 제안 행에서 지금 값 · 제안 값 · 단위 · 시행일을 꺼낸다.
 */
@Service
public class RuleVersionService {

    public record ApplyRequest(UUID taskId, Long approvalId, String proposalKey, String idempotencyKey) {}

    private final ApprovalGuard guard;
    private final RuleProposalRepository proposals;
    private final JobRepository jobs;
    private final JobPublisher publisher;
    private final DeliveryRepository deliveries;
    private final JdbcTemplate jdbc;

    public RuleVersionService(ApprovalGuard guard, RuleProposalRepository proposals, JobRepository jobs,
                              JobPublisher publisher, DeliveryRepository deliveries, JdbcTemplate jdbc) {
        this.guard = guard;
        this.proposals = proposals;
        this.jobs = jobs;
        this.publisher = publisher;
        this.deliveries = deliveries;
        this.jdbc = jdbc;
    }

    /**
     * 접수. 검사를 통과하면 큐 작업을 만든다. 같은 멱등키 재요청은 반영 뒤라도 기존 작업을 돌려준다.
     * 단 그 작업이 같은 작업 · 승인 · 제안의 것이어야 한다. 멱등키는 호출자가 정하므로, 다른 제안에 같은 키를 쓰면
     * 두 번째 제안이 반영되지 않았는데 접수된 것처럼 보인다.
     */
    public JobPublisher.Enqueued enqueue(ApplyRequest r) {
        if (r.proposalKey() == null || r.proposalKey().isBlank()) throw bad("proposalKey 는 필수입니다");
        if (r.idempotencyKey() == null || r.idempotencyKey().isBlank()) throw bad("idempotencyKey 는 필수입니다");
        Optional<Job> existing = jobs.findByIdempotencyKey(r.idempotencyKey());
        if (existing.isPresent()) {
            Job j = existing.get();
            boolean same = j.getType() == Job.Type.APPLY_RULE_VERSION && Objects.equals(r.taskId(), j.getTaskId())
                    && Objects.equals(r.approvalId(), j.getApprovalId())
                    && r.proposalKey().equals(j.getPayload().get("proposalKey"));
            if (!same) {
                throw new ResponseStatusException(HttpStatus.CONFLICT,
                        "같은 idempotencyKey 가 다른 제안 · 승인에 쓰였습니다: " + r.idempotencyKey());
            }
            return new JobPublisher.Enqueued(j.getId(), j.getStatus(), true);
        }

        Approval approval = guard.check(r.taskId(), r.approvalId());
        if (!keys(approval.getPayload(), "approvedProposalKeys").contains(r.proposalKey())) {
            throw forbidden("확인에서 반영으로 고른 제안이 아닙니다: " + r.proposalKey());
        }
        RuleProposal p = Proposals.latest(proposals.findByTaskIdOrderByProposalKeyAscVersionAscRoundAsc(r.taskId())).stream()
                .filter(x -> x.getProposalKey().equals(r.proposalKey())).findFirst()
                .orElseThrow(() -> forbidden("없는 제안입니다: " + r.proposalKey()));
        if (!Proposals.CHANGE_REQUIRED.equals(p.getImpact()) || p.getVerifyStatus() != RuleProposal.VerifyStatus.PASSED) {
            throw forbidden("근거 검사를 통과한 변경 필요 제안이 아닙니다: " + r.proposalKey());
        }
        String problem = RuleVersions.problem(p.getRuleId(), p.getCurrentValue(), p.getEffectiveDate(),
                rows(p.getProductId(), p.getRuleKey()), LocalDate.now());
        if (problem != null) throw new ResponseStatusException(HttpStatus.CONFLICT, "이미 바뀐 규칙: " + problem);

        // 확인한 그 회차의 행을 고정해서 넘긴다
        Map<String, Object> payload = new LinkedHashMap<>();
        payload.put("proposalKey", p.getProposalKey());
        payload.put("proposalId", p.getId());
        return publisher.publish(r.taskId(), Job.Type.APPLY_RULE_VERSION, payload, r.approvalId(), r.idempotencyKey());
    }

    /**
     * 워커가 부른다. 테이블을 잠그고 다시 검사한 뒤 기존 행을 닫고 새 행을 넣는다.
     * 검사에서 걸리면 다시 해도 같으므로 PermanentJobFailure (재시도 없이 DEAD).
     * 규칙 행 · 전달 기록 · 작업 완료(DONE, result)를 한 트랜잭션에 커밋한다. 커밋 뒤 워커가 죽어 메시지를 다시
     * 받아도 작업이 DONE 이라 반영을 반복하지 않는다 (반복하면 "이미 바뀐 규칙" 으로 DEAD 가 됐다).
     */
    @Transactional
    public Map<String, Object> apply(Job job) {
        Object id = job.getPayload().get("proposalId");
        RuleProposal p = id == null ? null : proposals.findById(((Number) id).longValue()).orElse(null);
        if (p == null) throw new PermanentJobFailure("제안을 찾을 수 없습니다: " + job.getPayload());

        jdbc.execute("lock table product_rules in share row exclusive mode");
        String problem = RuleVersions.problem(p.getRuleId(), p.getCurrentValue(), p.getEffectiveDate(),
                rows(p.getProductId(), p.getRuleKey()), LocalDate.now());
        if (problem != null) throw new PermanentJobFailure("이미 바뀐 규칙: " + problem);

        if (p.getRuleId() != null) {
            jdbc.update("update product_rules set effective_to = ? where rule_id = ?",
                    p.getEffectiveDate().minusDays(1), p.getRuleId());
        }
        String newRuleId = RuleVersions.nextRuleId(jdbc.queryForList("select rule_id from product_rules", String.class));
        // 근거 조항은 기존 행을 이어받는다. 새 규칙(기존 행 없음)은 제안 basis 의 첫 조항
        jdbc.update("""
                insert into product_rules (rule_id, product_id, rule_key, value, unit, effective_from, effective_to, basis_articles)
                values (?, ?, ?, ?, ?, ?, null,
                        coalesce((select basis_articles from product_rules where rule_id = ?), ?::jsonb))
                """, newRuleId, p.getProductId(), p.getRuleKey(), p.getProposedValue(), p.getUnit(), p.getEffectiveDate(),
                p.getRuleId(), RuleVersions.basisArticlesJson(p.getBasis()));
        deliveries.save(new Delivery(job.getTaskId(), Delivery.Kind.RULE_VERSION, newRuleId, null, job.getApprovalId()));

        Map<String, Object> result = new LinkedHashMap<>();
        result.put("proposalKey", p.getProposalKey());
        result.put("productId", p.getProductId());
        result.put("closedRuleId", p.getRuleId());
        result.put("newRuleId", newRuleId);
        result.put("effectiveFrom", p.getEffectiveDate().toString());
        job.setStatus(Job.Status.DONE);
        job.setResult(result);
        job.setLastError(null);
        jobs.save(job);
        return result;
    }

    private List<RuleVersions.Row> rows(String productId, String ruleKey) {
        return jdbc.query("""
                        select rule_id, value, effective_from, effective_to from product_rules
                        where product_id = ? and rule_key = ?
                        """,
                (rs, i) -> new RuleVersions.Row(rs.getString(1), rs.getString(2),
                        rs.getObject(3, LocalDate.class), rs.getObject(4, LocalDate.class)),
                productId, ruleKey);
    }

    @SuppressWarnings("unchecked")
    private static List<String> keys(Map<String, Object> payload, String name) {
        Object v = payload == null ? null : payload.get(name);
        return v instanceof List<?> l ? (List<String>) l : List.of();
    }

    private static ResponseStatusException bad(String m) {
        return new ResponseStatusException(HttpStatus.BAD_REQUEST, m);
    }

    private static ResponseStatusException forbidden(String m) {
        return new ResponseStatusException(HttpStatus.FORBIDDEN, m);
    }
}
