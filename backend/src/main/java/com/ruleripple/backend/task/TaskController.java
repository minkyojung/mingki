package com.ruleripple.backend.task;

import com.ruleripple.backend.auth.AuthUser;
import com.ruleripple.backend.task.TaskDtos.*;

import static com.ruleripple.backend.task.TaskDtos.MAX_VERIFY_ROUNDS;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.multipart.MultipartFile;
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter;

import java.io.IOException;
import java.net.URI;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;

/**
 * 공개 API — docs/FRONTEND_PLAN.md 4절 함수와 1:1.
 *
 *   createTask(files)                → POST /api/ai/tasks  (multipart: oldFile, newFile, title?, oldVersion?, newVersion?) → 202
 *   listTasks()                      → GET  /api/ai/tasks?mine=false
 *   getTask(id)                      → GET  /api/ai/tasks/{id}
 *   getRequirements(id)              → GET  /api/ai/tasks/{id}/requirements
 *   approveRequirements(id, d)       → POST /api/ai/tasks/{id}/requirements/approval
 *   getChangeSpecs(id)               → GET  /api/ai/tasks/{id}/change-specs
 *   getPatches(id)                   → GET  /api/ai/tasks/{id}/patches
 *   reviewPatches(id, decision)      → POST /api/ai/tasks/{id}/review
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
    public record RequirementApprovalRequest(List<TaskService.RequirementDecision> decisions, String comment) {}
    public record ReviewRequest(Approval.Decision decision, String comment, List<String> excludedSpecKeys) {}

    private final TaskService service;
    private final TaskEventService events;
    private final AgentDispatcher dispatcher;
    private final SseBroadcaster sse;

    public TaskController(TaskService service, TaskEventService events, AgentDispatcher dispatcher, SseBroadcaster sse) {
        this.service = service;
        this.events = events;
        this.dispatcher = dispatcher;
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
        List<Requirement> reqs = service.requirements(id);
        List<ChangeSpec> specs = service.changeSpecs(id);
        int targets = (int) specs.stream().filter(ChangeSpec::isTarget).count();
        Counts counts = new Counts(reqs.size(), targets, specs.size() - targets,
                TaskService.currentPatches(service.patches(id), service.excludedSpecKeys(id)).size(), service.testCases(id).size());
        return new TaskDetail(TaskSummary.of(t), t.getErrorMessage(), MAX_VERIFY_ROUNDS,
                service.documents(id).stream().map(DocumentMeta::of).toList(),
                PendingAction.of(t.getStatus(), t.getId()), counts,
                service.approvals(id),
                service.deliveries(id).stream().map(DeliveryView::of).toList());
    }

    // ---------- ① 요건 확정 ----------

    @GetMapping("/{id}/requirements")
    public RequirementsResponse requirements(@PathVariable UUID id) {
        AnalysisTask t = service.get(id);
        return new RequirementsResponse(TaskSummary.of(t),
                service.documents(id).stream().map(DocumentMeta::of).toList(),
                service.requirements(id).stream().map(RequirementView::of).toList());
    }

    @PostMapping("/{id}/requirements/approval")
    public ApprovalView approveRequirements(@AuthenticationPrincipal AuthUser me, @PathVariable UUID id,
                                            @RequestBody RequirementApprovalRequest req) {
        Approval a = service.approveRequirements(id, me, req.decisions(), req.comment());
        dispatcher.resume(id, a.getId());
        return service.approvalView(a);
    }

    // ---------- 영향 위치 ----------

    @GetMapping("/{id}/change-specs")
    public ChangeSpecsResponse changeSpecs(@PathVariable UUID id) {
        AnalysisTask t = service.get(id);
        List<ChangeSpecView> all = service.changeSpecs(id).stream().map(ChangeSpecView::of).toList();
        return new ChangeSpecsResponse(TaskSummary.of(t),
                all.stream().filter(ChangeSpecView::target).toList(),
                all.stream().filter(c -> !c.target()).toList());
    }

    // ---------- ② 검토 · 승인 ----------

    @GetMapping("/{id}/patches")
    public PatchesResponse patches(@PathVariable UUID id) {
        AnalysisTask t = service.get(id);
        List<CodePatch> all = service.patches(id);
        List<CodePatch> latest = TaskService.currentPatches(all, service.excludedSpecKeys(id));
        Set<Long> latestIds = latest.stream().map(CodePatch::getId).collect(java.util.stream.Collectors.toSet());
        int passed = (int) latest.stream().filter(p -> p.getVerifyStatus() == CodePatch.VerifyStatus.PASSED).count();
        int failed = (int) latest.stream().filter(p -> p.getVerifyStatus() == CodePatch.VerifyStatus.FAILED).count();
        int pending = latest.size() - passed - failed;
        int latestRound = all.stream().mapToInt(CodePatch::getRound).max().orElse(0);
        boolean canApprove = t.getStatus() == TaskStatus.WAITING_REVIEW && !latest.isEmpty() && failed == 0 && pending == 0;
        return new PatchesResponse(TaskSummary.of(t), MAX_VERIFY_ROUNDS, latestRound, passed, failed, pending, canApprove,
                all.stream().map(p -> PatchView.of(p, latestIds.contains(p.getId()))).toList(),
                service.testCases(id).stream().map(TestCaseView::of).toList());
    }

    @PostMapping("/{id}/review")
    public ApprovalView review(@AuthenticationPrincipal AuthUser me, @PathVariable UUID id, @RequestBody ReviewRequest req) {
        Approval a = service.review(id, me, req.decision(), req.comment(), req.excludedSpecKeys());
        dispatcher.resume(id, a.getId());
        return service.approvalView(a);
    }

    // ---------- 리포트 · 평가 ----------

    @GetMapping("/{id}/report")
    public ReportResponse report(@PathVariable UUID id) {
        AnalysisTask t = service.get(id);
        return new ReportResponse(TaskSummary.of(t), t.getReport() == null ? Map.<String, Object>of() : t.getReport(),
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
