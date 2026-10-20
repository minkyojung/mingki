package com.ruleripple.backend.task;

import com.ruleripple.backend.auth.AuthUser;
import com.ruleripple.backend.auth.User;
import com.ruleripple.backend.auth.UserRepository;
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
import java.util.*;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/** 공개 API 의 업무 로직: 작업 생성, 화면별 조회, interrupt ①② 처리. */
@Service
public class TaskService {

    private static final long MAX_FILE_BYTES = 1024 * 1024;
    private static final Set<String> ALLOWED_EXT = Set.of("md", "markdown", "txt");

    private final AnalysisTaskRepository tasks;
    private final RegulationDocumentRepository documents;
    private final RequirementRepository requirements;
    private final ChangeSpecRepository changeSpecs;
    private final CodePatchRepository patches;
    private final TestCaseRepository testCases;
    private final ApprovalRepository approvals;
    private final DeliveryRepository deliveries;
    private final TaskEventService events;
    private final UserRepository users;

    public TaskService(AnalysisTaskRepository tasks, RegulationDocumentRepository documents,
                       RequirementRepository requirements, ChangeSpecRepository changeSpecs,
                       CodePatchRepository patches, TestCaseRepository testCases, ApprovalRepository approvals,
                       DeliveryRepository deliveries, TaskEventService events,
                       UserRepository users) {
        this.tasks = tasks;
        this.documents = documents;
        this.requirements = requirements;
        this.changeSpecs = changeSpecs;
        this.patches = patches;
        this.testCases = testCases;
        this.approvals = approvals;
        this.deliveries = deliveries;
        this.events = events;
        this.users = users;
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
    public List<ChangeSpec> changeSpecs(UUID taskId) {
        get(taskId);
        return changeSpecs.findByTaskIdOrderByIdAsc(taskId);
    }

    @Transactional(readOnly = true)
    public List<CodePatch> patches(UUID taskId) {
        get(taskId);
        return patches.findByTaskIdOrderByRoundAscIdAsc(taskId);
    }

    @Transactional(readOnly = true)
    public List<TestCase> testCases(UUID taskId) {
        get(taskId);
        return testCases.findByTaskIdOrderByIdAsc(taskId);
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
    public List<Delivery> deliveries(UUID taskId) {
        return deliveries.findByTaskIdOrderByIdAsc(taskId);
    }

    /** (bundle, file) 마다 가장 높은 회차의 패치 = 현재 패치. */
    public static List<CodePatch> latestPatches(List<CodePatch> all) {
        Map<String, CodePatch> latest = new LinkedHashMap<>();
        for (CodePatch p : all) {
            String key = p.getBundle() + "::" + p.getFilePath();
            CodePatch cur = latest.get(key);
            if (cur == null || p.getRound() >= cur.getRound()) latest.put(key, p);
        }
        return new ArrayList<>(latest.values());
    }

    /**
     * 현재 패치 = latestPatches 에서 위치가 전부 제외된 패치를 뺀 것.
     * Agent 는 재설계 때 제외된 위치의 패치를 다시 만들지 않으므로, 예전 회차 행이 남아 있어도 현재 패치가 아니다.
     */
    public static List<CodePatch> currentPatches(List<CodePatch> all, Set<String> excluded) {
        return latestPatches(all).stream()
                .filter(p -> p.getSpecKeys().isEmpty() || !excluded.containsAll(p.getSpecKeys()))
                .toList();
    }

    /** 수정 요청(REVIEW · REQUEST_CHANGES)으로 제외한 위치의 누적. */
    @Transactional(readOnly = true)
    public Set<String> excludedSpecKeys(UUID taskId) {
        return excludedSpecKeys(approvals.findByTaskIdOrderByIdAsc(taskId));
    }

    public static Set<String> excludedSpecKeys(List<Approval> history) {
        Set<String> out = new LinkedHashSet<>();
        for (Approval a : history) {
            if (a.getStep() != Approval.Step.REVIEW || a.getDecision() != Approval.Decision.REQUEST_CHANGES) continue;
            if (a.getPayload().get("excludedSpecKeys") instanceof Collection<?> keys) {
                keys.forEach(k -> out.add(String.valueOf(k)));
            }
        }
        return out;
    }

    // ===================== interrupt ① 요건 확정 (OFFICER) =====================

    public record RequirementDecision(Long requirementId, Requirement.Status decision) {}

    @Transactional
    public Approval approveRequirements(UUID taskId, AuthUser me, List<RequirementDecision> decisions, String comment) {
        requireRole(me, User.Role.OFFICER, "요건 확정은 준법감시·상품기획 담당자만 할 수 있습니다");
        AnalysisTask task = lockAndExpect(taskId, TaskStatus.WAITING_REQUIREMENT_APPROVAL);

        List<Requirement> reqs = requirements.findByTaskIdOrderByIdAsc(taskId);
        if (reqs.isEmpty()) throw conflict("확정할 요건이 없습니다");
        Map<Long, Requirement.Status> byId = new HashMap<>();
        for (RequirementDecision d : decisions == null ? List.<RequirementDecision>of() : decisions) {
            if (d.decision() != Requirement.Status.CONFIRMED && d.decision() != Requirement.Status.HELD) {
                throw bad("decision 은 CONFIRMED 또는 HELD 입니다");
            }
            byId.put(d.requirementId(), d.decision());
        }
        List<Map<String, Object>> recorded = new ArrayList<>();
        List<String> confirmedKeys = new ArrayList<>();
        for (Requirement r : reqs) {
            Requirement.Status s = byId.remove(r.getId());
            if (s == null) throw bad("요건 " + r.getReqKey() + "(id " + r.getId() + ")에 대한 결정이 없습니다");
            r.setStatus(s);
            requirements.save(r);
            recorded.add(Map.<String, Object>of("requirementId", r.getId(), "reqKey", r.getReqKey(), "decision", s.name()));
            if (s == Requirement.Status.CONFIRMED) confirmedKeys.add(r.getReqKey());
        }
        if (!byId.isEmpty()) throw bad("이 작업에 없는 요건 id: " + byId.keySet());

        Approval.Decision overall = confirmedKeys.isEmpty() ? Approval.Decision.HELD : Approval.Decision.CONFIRMED;
        Approval saved = approvals.save(new Approval(taskId, Approval.Step.REQUIREMENTS, overall, me.id(), comment,
                Map.<String, Object>of("decisions", recorded, "confirmedReqKeys", confirmedKeys)));

        // 전부 보류 → 바로 HELD (Agent 는 report 만 만들고 끝낸다). 일부라도 확정 → 영향 위치 탐색.
        events.append(taskId, "RESUMED", Map.<String, Object>of("step", "REQUIREMENTS", "decision", overall.name(),
                "by", me.username(), "approvalId", saved.getId()));
        if (overall == Approval.Decision.HELD) {
            events.append(taskId, "STATUS", Map.<String, Object>of("status", TaskStatus.HELD.name()));
        } else {
            events.append(taskId, "STEP", Map.<String, Object>of("node", "locate_agent"));
        }
        return saved;
    }

    // ===================== interrupt ② 검토 · 승인 (DEVELOPER) =====================

    @Transactional
    public Approval review(UUID taskId, AuthUser me, Approval.Decision decision, String comment, List<String> excludedSpecKeys) {
        requireRole(me, User.Role.DEVELOPER, "검토·승인은 개발자만 할 수 있습니다");
        if (decision != Approval.Decision.APPROVE && decision != Approval.Decision.REJECT
                && decision != Approval.Decision.REQUEST_CHANGES) {
            throw bad("decision 은 APPROVE | REJECT | REQUEST_CHANGES 입니다");
        }
        AnalysisTask task = lockAndExpect(taskId, TaskStatus.WAITING_REVIEW);
        List<String> excluded = excludedSpecKeys == null ? List.of() : List.copyOf(excludedSpecKeys);
        if (!excluded.isEmpty() && decision != Approval.Decision.REQUEST_CHANGES) {
            throw bad("위치를 제외하려면 수정 요청(REQUEST_CHANGES)을 쓰세요. 제외한 뒤 다시 설계 · 검증합니다");
        }
        Set<String> alreadyExcluded = excludedSpecKeys(taskId);
        Map<String, Object> payload = new LinkedHashMap<>();

        switch (decision) {
            case APPROVE -> {
                // 현재 패치가 전부 검증을 통과해야 승인한다 (예외 없음).
                List<CodePatch> targets = currentPatches(patches.findByTaskIdOrderByRoundAscIdAsc(taskId), alreadyExcluded);
                if (targets.isEmpty()) throw conflict("승인할 패치가 없습니다. 전부 버리려면 REJECT 를 쓰세요");
                List<String> notPassed = targets.stream()
                        .filter(p -> p.getVerifyStatus() != CodePatch.VerifyStatus.PASSED)
                        .map(p -> p.getBundle() + ":" + p.getFilePath() + "(" + p.getVerifyStatus() + ")")
                        .toList();
                if (!notPassed.isEmpty()) {
                    throw conflict("검증을 통과하지 못한 패치가 있어 승인할 수 없습니다: " + notPassed
                            + " — 수정 요청으로 해당 위치를 제외하거나 고치세요");
                }
                payload.put("approvedPatchIds", targets.stream().map(CodePatch::getId).toList());
            }
            case REQUEST_CHANGES -> {
                // 제외만 하는 수정 요청은 comment 없이 받는다. Agent 는 영향 없는 패치를 재사용할 수 있다.
                if (excluded.isEmpty() && (comment == null || comment.isBlank())) {
                    throw bad("수정 요청에는 comment 나 excludedSpecKeys 가 필요합니다");
                }
                validateExclusion(taskId, excluded, alreadyExcluded);
                payload.put("excludedSpecKeys", excluded);
            }
            default -> { /* REJECT */ }
        }

        Approval saved = approvals.save(new Approval(taskId, Approval.Step.REVIEW, decision, me.id(), comment, payload));
        events.append(taskId, "RESUMED", Map.<String, Object>of("step", "REVIEW", "decision", decision.name(),
                "by", me.username(), "approvalId", saved.getId()));
        switch (decision) {
            case APPROVE -> events.append(taskId, "STEP", Map.<String, Object>of("node", "publish"));
            case REJECT -> events.append(taskId, "STATUS", Map.<String, Object>of("status", TaskStatus.REJECTED.name()));
            // 사람이 고친 내용도 다시 검증한다: design 으로 돌아가 반영 → verify
            default -> events.append(taskId, "STEP", Map.<String, Object>of("node", "design_agent", "message", "수정 요청 반영 후 재검증"));
        }
        return saved;
    }

    // ===================== helpers =====================

    /** 제외할 위치는 이 작업의 변경 대상이고, 아직 제외되지 않은 것이어야 한다. */
    private void validateExclusion(UUID taskId, List<String> excluded, Set<String> alreadyExcluded) {
        if (excluded.isEmpty()) return;
        Set<String> targets = new HashSet<>();
        for (ChangeSpec c : changeSpecs.findByTaskIdOrderByIdAsc(taskId)) {
            if (c.isTarget()) targets.add(c.getSpecKey());
        }
        List<String> unknown = excluded.stream().filter(k -> !targets.contains(k)).toList();
        if (!unknown.isEmpty()) throw bad("이 작업의 변경 대상이 아닌 위치: " + unknown);
        List<String> repeated = excluded.stream().filter(alreadyExcluded::contains).toList();
        if (!repeated.isEmpty()) throw bad("이미 제외한 위치: " + repeated);
    }

    private AnalysisTask lockAndExpect(UUID taskId, TaskStatus expected) {
        AnalysisTask task = tasks.findForUpdate(taskId)
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "task not found"));
        if (task.getStatus() != expected) {
            throw conflict("작업 상태가 " + task.getStatus() + " 입니다 (필요: " + expected + ")");
        }
        return task;
    }

    private static void requireRole(AuthUser me, User.Role role, String message) {
        if (me.role() != role) throw new ResponseStatusException(HttpStatus.FORBIDDEN, message);
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
