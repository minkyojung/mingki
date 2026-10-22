package com.ruleripple.backend.task;

import com.ruleripple.backend.auth.AuthUser;
import com.ruleripple.backend.auth.User;
import com.ruleripple.backend.auth.UserRepository;
import com.ruleripple.backend.config.RuleRippleProperties;
import com.ruleripple.backend.internal.LoanContract;
import com.ruleripple.backend.internal.LoanContractRepository;
import com.ruleripple.backend.internal.Product;
import com.ruleripple.backend.internal.ProductRepository;
import com.ruleripple.backend.queue.Job;
import com.ruleripple.backend.queue.JobRepository;
import com.ruleripple.backend.task.TaskDtos.ApprovalView;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.multipart.MultipartFile;
import org.springframework.web.server.ResponseStatusException;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.time.OffsetDateTime;
import java.util.*;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/** 공개 API 의 업무 로직: 작업 생성, 화면별 조회, 사람 확인, 다시 시도. */
@Service
public class TaskService {

    private static final long MAX_FILE_BYTES = 1024 * 1024;
    private static final Set<String> ALLOWED_EXT = Set.of("md", "markdown", "txt");

    private final AnalysisTaskRepository tasks;
    private final RegulationDocumentRepository documents;
    private final RequirementRepository requirements;
    private final ApprovalRepository approvals;
    private final DeliveryRepository deliveries;
    private final TaskEventService events;
    private final UserRepository users;
    private final RuleProposalRepository proposals;
    private final JobRepository jobs;
    private final ProductRepository products;
    private final LoanContractRepository contracts;
    private final RuleRippleProperties props;

    public TaskService(AnalysisTaskRepository tasks, RegulationDocumentRepository documents,
                       RequirementRepository requirements, ApprovalRepository approvals,
                       DeliveryRepository deliveries, TaskEventService events,
                       UserRepository users, RuleProposalRepository proposals, JobRepository jobs,
                       ProductRepository products, LoanContractRepository contracts, RuleRippleProperties props) {
        this.props = props;
        this.tasks = tasks;
        this.documents = documents;
        this.requirements = requirements;
        this.approvals = approvals;
        this.deliveries = deliveries;
        this.events = events;
        this.users = users;
        this.proposals = proposals;
        this.jobs = jobs;
        this.products = products;
        this.contracts = contracts;
    }

    // ===================== 생성 =====================

    /** 규정 v3(구) · v4(신) 파일을 받아 작업을 만든다. Agent 호출은 커밋 뒤 AgentDispatcher 가 한다. */
    @Transactional
    public AnalysisTask create(AuthUser me, MultipartFile oldFile, MultipartFile newFile,
                               String title, String oldVersion, String newVersion) {
        String oldText = readText(oldFile, "oldFile");
        String newText = readText(newFile, "newFile");
        if (sha256(oldText).equals(sha256(newText))) {
            throw bad("신·구 규정 파일 내용이 같습니다");
        }
        String oldLabel = blankToDefault(oldVersion, "v3");
        String newLabel = blankToDefault(newVersion, "v4");
        String taskTitle = blankToDefault(title, regulationName(newText, newFile) + " " + oldLabel + " → " + newLabel);

        AnalysisTask task = tasks.save(AnalysisTask.create(me.id(), taskTitle));
        documents.save(new RegulationDocument(task.getId(), RegulationDocument.Side.OLD, oldLabel,
                safeName(oldFile), oldText, sha256(oldText)));
        documents.save(new RegulationDocument(task.getId(), RegulationDocument.Side.NEW, newLabel,
                safeName(newFile), newText, sha256(newText)));
        events.append(task.getId(), "STEP", Map.<String, Object>of("node", "requirement_agent", "message", "규정 업로드 완료, 요건 추출을 시작합니다"));
        return task;
    }

    // ===================== 조회 =====================

    @Transactional(readOnly = true)
    public AnalysisTask get(UUID taskId) {
        return tasks.findById(taskId).orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "task not found"));
    }

    @Transactional(readOnly = true)
    public List<AnalysisTask> list(AuthUser me, boolean mine) {
        return mine ? tasks.findByRequesterIdOrderByCreatedAtDesc(me.id()) : tasks.findAllByOrderByCreatedAtDesc();
    }

    @Transactional(readOnly = true)
    public List<RegulationDocument> documents(UUID taskId) {
        get(taskId);
        return documents.findByTaskIdOrderByIdAsc(taskId);
    }

    @Transactional(readOnly = true)
    public List<Requirement> requirements(UUID taskId) {
        get(taskId);
        return requirements.findByTaskIdOrderByIdAsc(taskId);
    }

    @Transactional(readOnly = true)
    public List<ApprovalView> approvals(UUID taskId) {
        List<Approval> list = approvals.findByTaskIdOrderByIdAsc(taskId);
        Map<Long, User> byId = new HashMap<>();
        users.findAllById(list.stream().map(Approval::getApproverId).distinct().toList())
                .forEach(u -> byId.put(u.getId(), u));
        return list.stream().map(a -> ApprovalView.of(a, byId.get(a.getApproverId()))).toList();
    }

    @Transactional(readOnly = true)
    public ApprovalView approvalView(Approval a) {
        return ApprovalView.of(a, users.findById(a.getApproverId()).orElseThrow());
    }

    @Transactional(readOnly = true)
    public List<RuleProposal> proposals(UUID taskId) {
        get(taskId);
        return proposals.findByTaskIdOrderByProposalKeyAscVersionAscRoundAsc(taskId);
    }

    /** 상품 ID → 이름. 화면이 제안 · 계약에 이름을 같이 보이게 한다. */
    @Transactional(readOnly = true)
    public Map<String, String> productNames() {
        Map<String, String> names = new HashMap<>();
        for (Product p : products.findAll()) names.put(p.getProductId(), p.getName());
        return names;
    }

    /** Agent 가 저장한 계약 영향에 loan_contracts 의 상품 · 신청일 · DSR · 만기 · 연장 횟수를 붙인다. */
    @Transactional(readOnly = true)
    public List<Map<String, Object>> contractImpacts(UUID taskId) {
        List<Map<String, Object>> items = get(taskId).getContractImpacts();
        if (items == null || items.isEmpty()) return List.of();
        List<String> ids = items.stream().map(i -> i.get("contractId")).filter(Objects::nonNull).map(Object::toString).toList();
        Map<String, LoanContract> byId = new HashMap<>();
        for (LoanContract c : contracts.findAllById(ids)) byId.put(c.getContractId(), c);
        return ContractImpacts.enrich(items, byId, productNames());
    }

    /** 제안 키마다 가장 최근 반영 작업의 결과 (v2 10.4). */
    @Transactional(readOnly = true)
    public Map<String, TaskDtos.ApplyResult> applyResults(UUID taskId) {
        Map<String, TaskDtos.ApplyResult> out = new HashMap<>();
        for (Job j : jobs.findByTaskIdOrderByCreatedAtAsc(taskId)) {
            if (j.getType() != Job.Type.APPLY_RULE_VERSION || j.getPayload() == null) continue;
            Object key = j.getPayload().get("proposalKey");
            if (key == null) continue;
            Map<String, Object> r = j.getResult() == null ? Map.of() : j.getResult();
            out.put(key.toString(), new TaskDtos.ApplyResult(j.getId().toString(), j.getStatus().name(),
                    (String) r.get("closedRuleId"), (String) r.get("newRuleId")));
        }
        return out;
    }

    /** 가장 최근 확인(REVIEW) 기록. 제안마다 반영 · 제외를 붙이고, 리포트에 확인 결과를 보여 줄 때 쓴다. */
    @Transactional(readOnly = true)
    public Optional<Approval> latestReview(UUID taskId) {
        List<Approval> reviews = approvals.findByTaskIdOrderByIdAsc(taskId).stream()
                .filter(a -> a.getStep() == Approval.Step.REVIEW).toList();
        return reviews.isEmpty() ? Optional.empty() : Optional.of(reviews.get(reviews.size() - 1));
    }

    @Transactional(readOnly = true)
    public List<Delivery> deliveries(UUID taskId) {
        return deliveries.findByTaskIdOrderByIdAsc(taskId);
    }

    // ===================== 확인 (10.3) =====================

    /**
     * 최종 확인 1번. APPROVE 는 제외하지 않은 change_required 제안을 반영 대상으로 확정하고, REJECT 는 작업을 끝낸다.
     * 반영으로 고른 제안만 근거 검사를 통과하면 된다. 제외한 제안은 보지 않는다. 로그인한 사용자 누구나 할 수 있다.
     */
    @Transactional
    public Approval review(UUID taskId, AuthUser me, Approval.Decision decision, String comment,
                           List<String> excludedProposalKeys) {
        if (decision == null) throw bad("decision 은 APPROVE | REJECT 입니다");
        if (decision == Approval.Decision.REQUEST_CHANGES) throw bad("수정 요청은 POST /revisions 로 보냅니다");
        if (Proposals.missingComment(decision, comment)) throw bad("반려 사유(comment)가 필요합니다");
        AnalysisTask task = lockAndExpect(taskId, TaskStatus.WAITING_REVIEW);
        task.setErrorMessage(null);   // 앞선 결정을 Agent 가 받지 않았던 사유는 새 결정으로 지운다
        List<RuleProposal> latest = Proposals.latest(proposals.findByTaskIdOrderByProposalKeyAscVersionAscRoundAsc(taskId));
        List<String> changeKeys = Proposals.changeKeys(latest);
        List<String> excluded = excludedProposalKeys == null ? List.of() : List.copyOf(new LinkedHashSet<>(excludedProposalKeys));
        Map<String, Object> payload = new LinkedHashMap<>();

        if (decision == Approval.Decision.APPROVE) {
            List<String> unknown = excluded.stream().filter(k -> !changeKeys.contains(k)).toList();
            if (!unknown.isEmpty()) throw bad("변경 필요 제안이 아닌 키입니다: " + unknown);
            List<String> approved = changeKeys.stream().filter(k -> !excluded.contains(k)).toList();
            if (approved.isEmpty()) throw bad("반영할 제안이 없습니다. 전부 제외하려면 REJECT 를 쓰세요");
            List<String> blocked = Proposals.blocked(latest, approved);
            if (!blocked.isEmpty()) throw conflict("근거 검사를 통과하지 못한 제안이 있습니다. 제외하거나 수정 요청하세요: " + blocked);
            payload.put("approvedProposalKeys", approved);
        }
        payload.put("excludedProposalKeys", excluded);

        Approval saved = approvals.save(new Approval(taskId, Approval.Step.REVIEW, decision, me.id(), comment, payload));
        events.append(taskId, "RESUMED", Map.<String, Object>of("step", "REVIEW", "decision", decision.name(),
                "by", me.username(), "approvalId", saved.getId()));
        switch (decision) {
            case APPROVE -> events.append(taskId, "STEP", Map.<String, Object>of("node", "apply"));
            case REJECT -> events.append(taskId, "STATUS", Map.<String, Object>of("status", TaskStatus.REJECTED.name()));
            case REQUEST_CHANGES -> throw bad("수정 요청은 POST /revisions 로 보냅니다");
        }
        return saved;
    }

    // ===================== 수정 요청 (10.3) =====================

    /** 접수 결과. duplicate 면 같은 revisionId 의 처음 접수라 Agent 를 다시 부르지 않는다. */
    public record RevisionReceipt(Approval approval, String proposalId, int baseVersion, String status, boolean duplicate) {}

    /**
     * 제안 하나를 AI 가 다시 쓰게 한다. 확인 기록에 REQUEST_CHANGES 로 남기고, 작업을 DESIGNING 으로 옮긴다.
     * Agent 이벤트는 확인 대기에서 상태를 못 옮기므로 백엔드가 먼저 옮겨야 한다. Agent 호출은 컨트롤러가 커밋 뒤에 한다.
     */
    @Transactional
    public RevisionReceipt requestRevision(UUID taskId, AuthUser me, String proposalId, Integer baseVersion,
                                           String comment, String revisionId) {
        if (Proposals.blank(proposalId)) throw bad("proposalId 가 필요합니다 (예: REQ-8:P001)");
        if (baseVersion == null || baseVersion < 1) throw bad("baseVersion 은 1 이상입니다");
        if (Proposals.blank(comment)) throw bad("무엇을 바꿀지 comment 에 적으세요");
        AnalysisTask task = tasks.findForUpdate(taskId)
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "task not found"));

        // 화면의 재전송: 같은 revisionId 는 처음 접수를 돌려준다. 내용이 다르면 거절
        if (!Proposals.blank(revisionId)) {
            Optional<Approval> same = approvals.findByTaskIdOrderByIdAsc(taskId).stream()
                    .filter(a -> a.getDecision() == Approval.Decision.REQUEST_CHANGES && revisionId.equals(a.getPayload().get("revisionId")))
                    .findFirst();
            if (same.isPresent()) {
                Approval a = same.get();
                boolean equal = proposalId.equals(a.getPayload().get("proposalId"))
                        && Integer.valueOf(baseVersion).equals(a.getPayload().get("baseVersion"))
                        && comment.equals(a.getComment());
                if (!equal) throw conflict("같은 revisionId 로 다른 내용을 보냈습니다: " + revisionId);
                return new RevisionReceipt(a, proposalId, baseVersion, task.getStatus().name(), true);
            }
        }

        if (task.getStatus() != TaskStatus.WAITING_REVIEW) {
            throw conflict("작업 상태가 " + task.getStatus() + " 입니다 (필요: WAITING_REVIEW)");
        }
        List<RuleProposal> latest = Proposals.latest(proposals.findByTaskIdOrderByProposalKeyAscVersionAscRoundAsc(taskId));
        List<RuleProposal> target = Proposals.revisable(latest, proposalId);
        if (target.isEmpty()) throw bad("현재 제안에 없는 proposalId 입니다 (유지 판정은 다시 쓸 수 없습니다): " + proposalId);
        int current = target.get(0).getVersion();
        if (current != baseVersion) throw conflict("baseVersion 이 현재 버전과 다릅니다. 현재 버전: " + current);

        task.setErrorMessage(null);
        Map<String, Object> payload = new LinkedHashMap<>();
        payload.put("proposalId", proposalId);
        payload.put("baseVersion", baseVersion);
        if (!Proposals.blank(revisionId)) payload.put("revisionId", revisionId);
        Approval saved = approvals.save(new Approval(taskId, Approval.Step.REVIEW, Approval.Decision.REQUEST_CHANGES,
                me.id(), comment, payload));
        events.append(taskId, "RESUMED", Map.<String, Object>of("step", "REVIEW", "decision", Approval.Decision.REQUEST_CHANGES.name(),
                "by", me.username(), "approvalId", saved.getId(), "proposalId", proposalId));
        events.append(taskId, "STEP", Map.<String, Object>of("node", "proposal_agent"));
        return new RevisionReceipt(saved, proposalId, baseVersion, TaskStatus.DESIGNING.name(), false);
    }

    /**
     * 수정 요청 하나의 접수 내용과 결과. 결과는 이 요청이 만든 버전(baseVersion + 1)의 행이다. 뒤에 다른 요청이
     * 더 높은 버전을 만들어도 이 요청의 결과는 바뀌지 않는다. 다시 쓰는 동안(DESIGNING · VERIFYING)은 새 버전 행이
     * 이미 저장됐어도 processing 이다. 검사가 끝나야 결과다.
     */
    @Transactional(readOnly = true)
    public TaskDtos.RevisionView revision(UUID taskId, Long revisionId) {
        AnalysisTask task = get(taskId);
        Approval a = approvals.findById(revisionId)
                .filter(x -> x.getTaskId().equals(taskId) && x.getDecision() == Approval.Decision.REQUEST_CHANGES)
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "revision not found"));
        String proposalId = String.valueOf(a.getPayload().get("proposalId"));
        int baseVersion = ((Number) a.getPayload().get("baseVersion")).intValue();
        List<RuleProposal> bundle = proposals.findByTaskIdOrderByProposalKeyAscVersionAscRoundAsc(taskId).stream()
                .filter(p -> Proposals.proposalId(p).equals(proposalId)).toList();
        int producedVersion = baseVersion + 1;
        Optional<RuleProposal> result = Proposals.latest(bundle.stream().filter(p -> p.getVersion() == producedVersion).toList())
                .stream().findFirst();
        boolean latestRequest = latestReview(taskId).map(x -> x.getId().equals(a.getId())).orElse(false);
        boolean rewriting = task.getStatus() == TaskStatus.DESIGNING || task.getStatus() == TaskStatus.VERIFYING;

        String state;
        if (latestRequest && rewriting) state = "processing";
        else if (latestRequest && task.getStatus() == TaskStatus.FAILED) state = "failed";
        else if (result.isPresent()) state = "done";
        else state = "rejected";

        Map<String, String> names = productNames();
        return new TaskDtos.RevisionView(a.getId(), proposalId, baseVersion, a.getComment(),
                TaskDtos.Approver.of(users.findById(a.getApproverId()).orElseThrow()), a.getCreatedAt(), state,
                result.map(RuleProposal::getVersion).orElse(null), result.map(RuleProposal::getRevisionOutcome).orElse(null),
                result.map(RuleProposal::getRevisionResponse).orElse(null), result.map(RuleProposal::getChangedFields).orElse(null),
                bundle.stream().map(p -> TaskDtos.ProposalView.of(p, names.get(p.getProductId()),
                        result.map(r -> r.getVersion() == p.getVersion() && r.getRound() == p.getRound()).orElse(false), null, null)).toList());
    }

    // ===================== 이어가기 (Agent 가 도중에 죽었을 때) =====================

    /**
     * 다시 시도 준비 결과. wasFailed 면 상태를 되돌린 것이라 Agent 가 거절하면 FAILED 로 되돌려야 한다.
     * firstError 는 되돌리기 전의 실패 사유다. 다시 시도도 실패하면 함께 남긴다.
     */
    public record RetryPlan(AnalysisTask task, boolean wasFailed, String firstError) {}

    /**
     * "다시 시도" 준비. 진행 중이거나 FAILED 인 작업만 (RetryGuard). Agent 호출은 컨트롤러가 커밋 뒤에 동기로 한다.
     * FAILED 였으면 상태를 멈추기 전 자리로 되돌린다 (TaskStatus.restoredAfterFailure).
     * Agent 이벤트는 FAILED 에서 나올 수 없어서 백엔드가 먼저 되돌려야 한다.
     */
    @Transactional
    public RetryPlan prepareRetry(UUID taskId, AuthUser me) {
        AnalysisTask task = tasks.findForUpdate(taskId)
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "task not found"));
        RetryGuard.problem(task.getStatus(), task.getUpdatedAt(), props.agent().retryQuietSeconds(), OffsetDateTime.now())
                .ifPresent(reason -> { throw conflict(reason); });
        boolean wasFailed = task.getStatus() == TaskStatus.FAILED;
        String firstError = wasFailed ? task.getErrorMessage() : null;
        events.append(taskId, "RESUMED", Map.<String, Object>of("step", "RETRY", "by", me.username(),
                "message", "멈춘 자리부터 다시 시작합니다"));
        if (wasFailed) {
            TaskStatus restored = TaskStatus.restoredAfterFailure(task.getCurrentNode(), latestApprove(taskId).isPresent());
            task.setErrorMessage(null);
            tasks.save(task);
            events.append(taskId, "STATUS", Map.<String, Object>of("status", restored.name()));
            task.setStatus(restored);
        }
        return new RetryPlan(task, wasFailed, firstError);
    }

    /**
     * Agent 가 확인 결정 · 수정 요청을 그래프에 넣기 전에 거절했다 (/resume 400). Agent 는 그대로 확인을 기다리므로
     * 작업을 확인 대기로 되돌리고 사유를 남긴다. 사용자는 문제 제안을 빼거나 반려하거나, 요청을 구체적으로 다시 쓴다.
     */
    @Transactional
    public void reviewNotAccepted(UUID taskId, Approval.Decision decision, String detail) {
        String reason = decision == Approval.Decision.REQUEST_CHANGES
                ? "Agent 가 수정 요청을 받지 않았습니다: " + detail + " — 무엇을 바꿀지 구체적으로 다시 적으세요"
                : "Agent 가 확인 결정을 받지 않았습니다: " + detail + " — 문제 제안을 제외하거나 반려하세요";
        events.append(taskId, "STATUS", Map.<String, Object>of("status", TaskStatus.WAITING_REVIEW.name(), "reason", reason));
    }

    /** Agent 가 "아직 실행 중"(423)이라 거절했다. 되돌렸던 상태를 FAILED 로 돌리고, 진행 중이던 작업은 그대로 둔다. */
    @Transactional
    public void retryStillRunning(UUID taskId, RetryPlan plan) {
        if (plan.wasFailed()) retryFailed(taskId, "Agent 가 아직 실행 중입니다. 잠시 뒤 다시 시도하세요", plan.firstError());
    }

    /** 가장 최근 확인이 APPROVE 면 그 기록. 다시 시도가 상태를 되돌릴 때 반영 중으로 갈지 정한다. */
    @Transactional(readOnly = true)
    public Optional<Approval> latestApprove(UUID taskId) {
        return latestReview(taskId).filter(a -> a.getDecision() == Approval.Decision.APPROVE);
    }

    /** 가장 최근 확인이 APPROVE · REQUEST_CHANGES 면 그 기록. Agent 가 확인 대기 상태로 남아 있을 때 다시 보낼 결정이다. */
    @Transactional(readOnly = true)
    public Optional<Approval> latestResumable(UUID taskId) {
        return latestReview(taskId).filter(a -> a.getDecision() != Approval.Decision.REJECT);
    }

    /** 다시 시도가 FAILED 로 끝나면 사용자에게 보이게 기록한다. 처음 실패 사유를 함께 남기고, 남긴 사유를 돌려준다. */
    @Transactional
    public String retryFailed(UUID taskId, String reason, String firstError) {
        String message = RetryGuard.failureReason(reason, firstError);
        events.append(taskId, "FAILED", Map.<String, Object>of("reason", message));
        return message;
    }

    // ===================== helpers =====================

    private AnalysisTask lockAndExpect(UUID taskId, TaskStatus expected) {
        AnalysisTask task = tasks.findForUpdate(taskId)
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "task not found"));
        if (task.getStatus() != expected) {
            throw conflict("작업 상태가 " + task.getStatus() + " 입니다 (필요: " + expected + ")");
        }
        return task;
    }

    private static String readText(MultipartFile file, String field) {
        if (file == null || file.isEmpty()) throw bad(field + " 파일이 필요합니다");
        if (file.getSize() > MAX_FILE_BYTES) throw bad(field + " 는 1MB 이하여야 합니다");
        String ext = extOf(file.getOriginalFilename());
        if (!ALLOWED_EXT.contains(ext)) throw bad(field + " 는 .md / .txt 파일이어야 합니다");
        try {
            String text = new String(file.getBytes(), StandardCharsets.UTF_8);
            if (text.isBlank()) throw bad(field + " 내용이 비어 있습니다");
            return text;
        } catch (IOException e) {
            throw bad(field + " 를 읽을 수 없습니다");
        }
    }

    static String sha256(String text) {
        try {
            byte[] h = MessageDigest.getInstance("SHA-256").digest(text.getBytes(StandardCharsets.UTF_8));
            return HexFormat.of().formatHex(h);
        } catch (NoSuchAlgorithmException e) {
            throw new IllegalStateException(e);
        }
    }

    private static String safeName(MultipartFile f) {
        String n = f.getOriginalFilename();
        if (n == null || n.isBlank()) return "regulation.md";
        n = n.replace('\\', '/');
        n = n.substring(n.lastIndexOf('/') + 1);
        return n.length() > 255 ? n.substring(0, 255) : n;
    }

    private static String extOf(String name) {
        if (name == null) return "";
        int i = name.lastIndexOf('.');
        return i < 0 ? "" : name.substring(i + 1).toLowerCase(Locale.ROOT);
    }

    private static final Pattern FRONTMATTER_TITLE = Pattern.compile("\\A---\\R(?:(?!---).*\\R)*?title:\\s*(.+?)\\s*\\R");

    /** 규정 이름은 frontmatter 의 title 을 쓰고, 없으면 파일명을 쓴다. */
    private static String regulationName(String text, MultipartFile file) {
        Matcher m = FRONTMATTER_TITLE.matcher(text);
        return m.find() ? m.group(1) : stripExt(file.getOriginalFilename());
    }

    private static String stripExt(String name) {
        if (name == null || name.isBlank()) return "규정";
        String n = name.replace('\\', '/');
        n = n.substring(n.lastIndexOf('/') + 1);
        int i = n.lastIndexOf('.');
        return i <= 0 ? n : n.substring(0, i);
    }

    private static String blankToDefault(String v, String def) {
        if (v == null || v.isBlank()) return def;
        String t = v.trim();
        return t.length() > 200 ? t.substring(0, 200) : t;
    }

    private static ResponseStatusException bad(String m) {
        return new ResponseStatusException(HttpStatus.BAD_REQUEST, m);
    }

    private static ResponseStatusException conflict(String m) {
        return new ResponseStatusException(HttpStatus.CONFLICT, m);
    }
}
