package com.ruleripple.backend.task;

import com.ruleripple.backend.auth.AuthUser;
import com.ruleripple.backend.task.TaskDtos.*;

import static com.ruleripple.backend.task.TaskDtos.MAX_VERIFY_ROUNDS;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.client.HttpClientErrorException;
import org.springframework.web.client.HttpStatusCodeException;
import org.springframework.web.client.ResourceAccessException;
import org.springframework.web.server.ResponseStatusException;
import org.springframework.web.multipart.MultipartFile;
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter;

import java.io.IOException;
import java.net.URI;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.Set;
import java.util.UUID;

/**
 * 공개 API — docs/FRONTEND_PLAN.md 4절 함수와 1:1.
 *
 *   createTask(files)                → POST /api/ai/tasks  (multipart: oldFile, newFile, title?, oldVersion?, newVersion?) → 202
 *   listTasks()                      → GET  /api/ai/tasks?mine=false
 *   getTask(id)                      → GET  /api/ai/tasks/{id}
 *   getRequirements(id)              → GET  /api/ai/tasks/{id}/requirements      (바뀐 조항 · 요건)
 *   getProposals(id)                 → GET  /api/ai/tasks/{id}/proposals          (상품 × 규칙 변경 제안)
 *   getContractImpacts(id)           → GET  /api/ai/tasks/{id}/contract-impacts   (경과조치 계약)
 *   reviewProposals(id, decision)    → POST /api/ai/tasks/{id}/review            (최종 확인 1번)
 *   requestRevision(id, r)           → POST /api/ai/tasks/{id}/revisions         (제안 하나를 AI 가 다시 쓰게) → 202
 *   getRevision(id, rid)             → GET  /api/ai/tasks/{id}/revisions/{rid}
 *   retryTask(id)                    → POST /api/ai/tasks/{id}/retry             ("다시 시도": Agent 가 죽었거나 호출에 실패한 작업)
 *   getReport(id)                    → GET  /api/ai/tasks/{id}/report
 *   getEvaluation(id)                → GET  /api/ai/tasks/{id}/evaluation
 * 진행 상황
 *   폴링                              → GET  /api/ai/tasks/{id}/events?after={seq}
 *   SSE                              → GET  /api/ai/tasks/{id}/stream?access_token={jwt}  (EventSource 는 헤더를 못 보냄)
 */
@RestController
@RequestMapping("/api/ai/tasks")
public class TaskController {

    public record CreateResponse(UUID taskId, String status) {}
    public record ReviewRequest(Approval.Decision decision, String comment, List<String> excludedProposalKeys) {}
    /** revisionId 는 화면의 재전송 식별자다. 없어도 된다 (10.3). */
    public record RevisionRequest(String proposalId, Integer baseVersion, String comment, String revisionId) {}

    private final TaskService service;
    private final TaskEventService events;
    private final AgentDispatcher dispatcher;
    private final AgentClient agent;
    private final SseBroadcaster sse;

    public TaskController(TaskService service, TaskEventService events, AgentDispatcher dispatcher, AgentClient agent,
                          SseBroadcaster sse) {
        this.service = service;
        this.events = events;
        this.dispatcher = dispatcher;
        this.agent = agent;
        this.sse = sse;
    }

    // ---------- 생성 ----------

    @PostMapping(consumes = MediaType.MULTIPART_FORM_DATA_VALUE)
    public ResponseEntity<CreateResponse> create(@AuthenticationPrincipal AuthUser me,
                                                 @RequestPart("oldFile") MultipartFile oldFile,
                                                 @RequestPart("newFile") MultipartFile newFile,
                                                 @RequestParam(value = "title", required = false) String title,
                                                 @RequestParam(value = "oldVersion", required = false) String oldVersion,
                                                 @RequestParam(value = "newVersion", required = false) String newVersion) {
        AnalysisTask task = service.create(me, oldFile, newFile, title, oldVersion, newVersion);
        dispatcher.run(task.getId()); // 커밋 이후 비동기 호출
        return ResponseEntity.status(HttpStatus.ACCEPTED)
                .location(URI.create("/api/ai/tasks/" + task.getId()))
                .body(new CreateResponse(task.getId(), task.getStatus().name()));
    }

    // ---------- 목록 · 상세 ----------

    @GetMapping
    public List<TaskSummary> list(@AuthenticationPrincipal AuthUser me, @RequestParam(defaultValue = "false") boolean mine) {
        return service.list(me, mine).stream().map(TaskSummary::of).toList();
    }

    @GetMapping("/{id}")
    public TaskDetail get(@PathVariable UUID id) {
        AnalysisTask t = service.get(id);
        Counts counts = new Counts(service.requirements(id).size(), Proposals.latest(service.proposals(id)).size());
        return new TaskDetail(TaskSummary.of(t), t.getErrorMessage(), MAX_VERIFY_ROUNDS,
                service.documents(id).stream().map(DocumentMeta::of).toList(),
                PendingAction.of(t.getStatus(), t.getId()), counts,
                service.approvals(id),
                service.deliveries(id).stream().map(DeliveryView::of).toList());
    }

    // ---------- 요건 · 제안 · 계약 영향 ----------

    @GetMapping("/{id}/requirements")
    public RequirementsResponse requirements(@PathVariable UUID id) {
        AnalysisTask t = service.get(id);
        return new RequirementsResponse(TaskSummary.of(t),
                service.documents(id).stream().map(DocumentMeta::of).toList(),
                service.requirements(id).stream().map(RequirementView::of).toList());
    }

    @GetMapping("/{id}/proposals")
    public ProposalsResponse proposals(@PathVariable UUID id) {
        AnalysisTask t = service.get(id);
        List<RuleProposal> all = service.proposals(id);
        List<RuleProposal> latest = Proposals.latest(all);
        Set<Long> latestIds = latest.stream().map(RuleProposal::getId).collect(java.util.stream.Collectors.toSet());

        // 확인 기록에서 제안마다 반영 · 제외를 붙인다. 거절이면 붙이지 않는다.
        Map<String, String> decisions = new HashMap<>();
        service.latestReview(id).filter(a -> a.getDecision() == Approval.Decision.APPROVE).ifPresent(a -> {
            keys(a.getPayload(), "approvedProposalKeys").forEach(k -> decisions.put(k, "APPLY"));
            keys(a.getPayload(), "excludedProposalKeys").forEach(k -> decisions.put(k, "EXCLUDE"));
        });
        Map<String, ApplyResult> applied = service.applyResults(id);
        Map<String, String> names = service.productNames();
        int latestRound = latest.stream().mapToInt(RuleProposal::getRound).max().orElse(0);
        return new ProposalsResponse(TaskSummary.of(t), MAX_VERIFY_ROUNDS, latestRound,
                Proposals.canApprove(t.getStatus(), latest),
                all.stream().map(p -> {
                    boolean isLatest = latestIds.contains(p.getId());
                    String name = names.get(p.getProductId());
                    return isLatest
                            ? ProposalView.of(p, name, true, decisions.get(p.getProposalKey()), applied.get(p.getProposalKey()))
                            : ProposalView.of(p, name, false, null, null);
                }).toList());
    }

    @GetMapping("/{id}/contract-impacts")
    public ContractImpactsResponse contractImpacts(@PathVariable UUID id) {
        return new ContractImpactsResponse(TaskSummary.of(service.get(id)), service.contractImpacts(id));
    }

    @SuppressWarnings("unchecked")
    private static List<String> keys(Map<String, Object> payload, String name) {
        Object v = payload == null ? null : payload.get(name);
        return v instanceof List<?> l ? (List<String>) l : List.of();
    }

    // ---------- 확인 ----------

    @PostMapping("/{id}/review")
    public ApprovalView review(@AuthenticationPrincipal AuthUser me, @PathVariable UUID id, @RequestBody ReviewRequest req) {
        Approval a = service.review(id, me, req.decision(), req.comment(), req.excludedProposalKeys());
        dispatcher.resume(id, a.getId());
        return service.approvalView(a);
    }

    // ---------- 수정 요청 (10.3) ----------

    /** 접수하면 작업은 DESIGNING 이 되고 Agent 가 그 제안만 다시 쓴다. 같은 revisionId 를 다시 보내면 처음 접수를 돌려준다. */
    @PostMapping("/{id}/revisions")
    public ResponseEntity<RevisionAccepted> requestRevision(@AuthenticationPrincipal AuthUser me, @PathVariable UUID id,
                                                            @RequestBody RevisionRequest req) {
        TaskService.RevisionReceipt r = service.requestRevision(id, me, req.proposalId(), req.baseVersion(), req.comment(), req.revisionId());
        if (!r.duplicate()) dispatcher.resume(id, r.approval().getId());
        return ResponseEntity.accepted().body(new RevisionAccepted(r.approval().getId(), r.proposalId(), r.baseVersion(), r.status()));
    }

    @GetMapping("/{id}/revisions/{revisionId}")
    public RevisionView revision(@PathVariable UUID id, @PathVariable Long revisionId) {
        return service.revision(id, revisionId);
    }

    /**
     * "다시 시도". Agent 가 어디서 멈췄는지에 따라 세 가지 중 하나를 다시 보낸다. 백엔드가 Agent 상태를 묻지 않고,
     * /continue 를 먼저 보내고 Agent 가 409 로 거절하면 백엔드가 가진 기록으로 다음을 고른다.
     *   1. 반영(apply) 도중 죽음 → /continue (같은 멱등키라 큐 작업이 중복 생기지 않음)
     *   2. 승인 직후 /resume 호출이 실패 → 저장된 최근 승인으로 /resume
     *   3. 업로드 직후 /run 호출이 실패 → /run
     * 동기 호출이라 Agent 의 409 와 연결 실패를 사용자에게 바로 돌려준다. 연결 실패면 작업은 다시 FAILED.
     */
    @PostMapping("/{id}/retry")
    public ResponseEntity<Map<String, Object>> retryTask(@AuthenticationPrincipal AuthUser me, @PathVariable UUID id) {
        TaskService.RetryPlan plan = service.prepareRetry(id, me);
        AnalysisTask task = plan.task();
        String how;
        if (task.getStatus().isWaiting()) {
            // 확인 대기에서 FAILED 가 됐던 작업. Agent 는 그대로 기다리고 있으니 부르지 않고 사람이 결정하게 둔다
            how = "waiting";
        } else {
            try {
                how = retryAgent(task);
            } catch (HttpStatusCodeException e) {
                if (e.getStatusCode().value() == 423) {
                    // Agent 가 그 thread 를 아직 돌리고 있다. 겹쳐 보내지 않고 사용자에게 알린다
                    service.retryStillRunning(id, plan);
                    throw new ResponseStatusException(HttpStatus.CONFLICT, "Agent 가 아직 실행 중입니다. 잠시 뒤 다시 시도하세요");
                }
                // Agent 쪽 작업이 이미 끝났거나(반영 실패로 report 까지 감) 기록과 어긋남. 되돌린 상태를 다시 FAILED 로
                String reason = service.retryFailed(id, "Agent 가 다시 시작하지 못했습니다: " + agentMessage(e), plan.firstError());
                throw new ResponseStatusException(HttpStatus.CONFLICT, reason);
            } catch (ResourceAccessException e) {
                String reason = service.retryFailed(id, "Agent 에 연결할 수 없습니다: " + e.getMessage(), plan.firstError());
                throw new ResponseStatusException(HttpStatus.SERVICE_UNAVAILABLE, reason);
            }
        }
        String status = "waiting".equals(how) ? TaskStatus.WAITING_REVIEW.name() : task.getStatus().name();
        return ResponseEntity.accepted().body(Map.<String, Object>of(
                "taskId", id, "status", status, "accepted", true, "how", how));
    }

    private String retryAgent(AnalysisTask task) {
        try {
            agent.continueTask(task);
            return "continue";
        } catch (HttpClientErrorException.Conflict notContinuable) {
            Optional<Approval> resumable = service.latestResumable(task.getId());
            if (resumable.isPresent()) {
                try {
                    agent.resume(task, resumable.get());
                    return "resume";
                } catch (HttpClientErrorException.BadRequest notAccepted) {
                    // Agent 가 저장된 승인 · 수정 요청을 받지 않았다. Agent 는 확인을 기다리므로 사람이 다시 결정하게 둔다
                    service.reviewNotAccepted(task.getId(), resumable.get().getDecision(), agentMessage(notAccepted));
                    return "waiting";
                }
            }
            if (service.requirements(task.getId()).isEmpty()) {
                agent.run(task, service.documents(task.getId()));
                return "run";
            }
            throw notContinuable;
        }
    }

    private static String agentMessage(HttpStatusCodeException e) {
        return AgentClient.detail(e);
    }

    // ---------- 리포트 · 평가 ----------

    @GetMapping("/{id}/report")
    public ReportResponse report(@PathVariable UUID id) {
        AnalysisTask t = service.get(id);
        return new ReportResponse(TaskSummary.of(t), t.getReport() == null ? Map.<String, Object>of() : t.getReport(),
                service.latestReview(id).map(service::approvalView).orElse(null),
                service.deliveries(id).stream().map(DeliveryView::of).toList());
    }

    @GetMapping("/{id}/evaluation")
    public EvaluationResponse evaluation(@PathVariable UUID id) {
        AnalysisTask t = service.get(id);
        return new EvaluationResponse(TaskSummary.of(t), t.getEvaluation() == null ? Map.<String, Object>of() : t.getEvaluation());
    }

    // ---------- 진행 상황 ----------

    @GetMapping("/{id}/events")
    public List<EventView> events(@PathVariable UUID id, @RequestParam(defaultValue = "0") int after) {
        service.get(id);
        return events.after(id, after).stream().map(EventView::of).toList();
    }

    @GetMapping(value = "/{id}/stream", produces = MediaType.TEXT_EVENT_STREAM_VALUE)
    public SseEmitter stream(@PathVariable UUID id,
                             @RequestHeader(value = "Last-Event-ID", required = false) Integer lastEventId,
                             @RequestParam(value = "after", required = false) Integer after) throws IOException {
        AnalysisTask t = service.get(id);
        SseEmitter emitter = sse.subscribe(id);
        int from = lastEventId != null ? lastEventId : (after != null ? after : 0);
        for (TaskEvent e : events.after(id, from)) sse.sendTo(emitter, e);  // 놓친 이벤트 재생
        if (t.getStatus().isTerminal()) emitter.complete();
        return emitter;
    }
}
