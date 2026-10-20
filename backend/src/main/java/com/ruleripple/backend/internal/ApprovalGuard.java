package com.ruleripple.backend.internal;

import com.ruleripple.backend.task.*;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.server.ResponseStatusException;

import java.util.Collection;
import java.util.List;
import java.util.Set;
import java.util.UUID;

/**
 * 쓰기 도구(create_draft_pr, create_work_ticket)의 서버 측 검증.
 * 그래프의 interrupt 만 믿지 않고, 호출마다 approval_id 가 유효한지 다시 확인한다.
 *
 * 통과 조건
 *  1. approval 이 존재하고 같은 작업의 것
 *  2. step = REVIEW, decision = APPROVE
 *  3. 그 작업의 가장 최근 REVIEW 결정 (이후 수정 요청·거절로 뒤집히지 않음)
 *  4. 작업 상태 = PUBLISHING
 *  5. 대상 specKey 가 수정 요청으로 제외한 위치(누적)가 아님
 */
@Component
public class ApprovalGuard {

    private final ApprovalRepository approvals;
    private final AnalysisTaskRepository tasks;

    public ApprovalGuard(ApprovalRepository approvals, AnalysisTaskRepository tasks) {
        this.approvals = approvals;
        this.tasks = tasks;
    }

    @Transactional(readOnly = true)
    public Approval check(UUID taskId, Long approvalId, Collection<String> specKeys) {
        if (taskId == null || approvalId == null) throw forbidden("taskId 와 approvalId 가 필요합니다");
        Approval a = approvals.findById(approvalId).orElseThrow(() -> forbidden("존재하지 않는 approvalId: " + approvalId));
        if (!a.getTaskId().equals(taskId)) throw forbidden("다른 작업의 승인입니다");
        if (a.getStep() != Approval.Step.REVIEW || a.getDecision() != Approval.Decision.APPROVE) {
            throw forbidden("검토·승인(REVIEW/APPROVE) 기록이 아닙니다");
        }
        List<Approval> history = approvals.findByTaskIdOrderByIdAsc(taskId);
        Approval latestReview = history.stream()
                .filter(x -> x.getStep() == Approval.Step.REVIEW)
                .reduce((first, second) -> second)
                .orElseThrow();
        if (!latestReview.getId().equals(a.getId())) throw forbidden("이후 결정으로 무효가 된 승인입니다");

        AnalysisTask task = tasks.findById(taskId).orElseThrow(() -> forbidden("task not found"));
        if (task.getStatus() != TaskStatus.PUBLISHING) {
            throw new ResponseStatusException(HttpStatus.CONFLICT, "작업 상태가 PUBLISHING 이 아닙니다 (현재 " + task.getStatus() + ")");
        }
        if (specKeys != null && !specKeys.isEmpty()) {
            Set<String> excluded = TaskService.excludedSpecKeys(history);
            List<String> blocked = specKeys.stream().filter(excluded::contains).toList();
            if (!blocked.isEmpty()) throw forbidden("수정 요청으로 제외한 위치입니다: " + blocked);
        }
        return a;
    }

    private static ResponseStatusException forbidden(String m) {
        return new ResponseStatusException(HttpStatus.FORBIDDEN, m);
    }
}
