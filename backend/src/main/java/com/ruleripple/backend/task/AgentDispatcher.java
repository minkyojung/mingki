package com.ruleripple.backend.task;

import lombok.extern.slf4j.Slf4j;
import org.springframework.scheduling.annotation.Async;
import org.springframework.stereotype.Component;
import org.springframework.web.client.HttpClientErrorException;

import java.util.Map;
import java.util.UUID;

/**
 * Agent 호출은 트랜잭션이 커밋된 뒤, 요청 스레드 밖에서 한다.
 * (트랜잭션 안에서 부르면 Agent 콜백이 잠긴 작업 행을 기다리며 서로 멈출 수 있다.)
 * 컨트롤러가 서비스 호출(커밋) 직후 이 빈을 부른다.
 */
@Slf4j
@Component
public class AgentDispatcher {

    private final AgentClient agent;
    private final AnalysisTaskRepository tasks;
    private final RegulationDocumentRepository documents;
    private final ApprovalRepository approvals;
    private final TaskEventService events;
    private final TaskService service;

    public AgentDispatcher(AgentClient agent, AnalysisTaskRepository tasks, RegulationDocumentRepository documents,
                           ApprovalRepository approvals, TaskEventService events, TaskService service) {
        this.agent = agent;
        this.tasks = tasks;
        this.documents = documents;
        this.approvals = approvals;
        this.events = events;
        this.service = service;
    }

    @Async
    public void run(UUID taskId) {
        try {
            AnalysisTask task = tasks.findById(taskId).orElseThrow();
            agent.run(task, documents.findByTaskIdOrderByIdAsc(taskId));
        } catch (Exception e) {
            log.warn("agent run failed task={} : {}", taskId, e.getMessage());
            events.append(taskId, "FAILED", Map.<String, Object>of("reason", "Agent 호출 실패: " + e.getMessage()));
        }
    }

    @Async
    public void resume(UUID taskId, Long approvalId) {
        try {
            AnalysisTask task = tasks.findById(taskId).orElseThrow();
            Approval approval = approvals.findById(approvalId).orElseThrow();
            agent.resume(task, approval);
        } catch (HttpClientErrorException.BadRequest notAccepted) {
            // 결정이 Agent 의 현재 제안과 맞지 않는다. Agent 는 그대로 확인을 기다리므로 FAILED 대신 확인 대기로
            log.warn("agent did not accept review task={} : {}", taskId, notAccepted.getMessage());
            service.reviewNotAccepted(taskId, decisionOf(approvalId), AgentClient.detail(notAccepted));
        } catch (Exception e) {
            log.warn("agent resume failed task={} : {}", taskId, e.getMessage());
            events.append(taskId, "FAILED", Map.<String, Object>of("reason", "Agent 재개 실패: " + e.getMessage()));
        }
    }

    private Approval.Decision decisionOf(Long approvalId) {
        return approvals.findById(approvalId).map(Approval::getDecision).orElse(Approval.Decision.APPROVE);
    }
}
