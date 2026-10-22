package com.ruleripple.backend.internal;

import com.ruleripple.backend.task.*;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.server.ResponseStatusException;

import java.time.LocalDate;
import java.util.Arrays;
import java.util.List;
import java.util.Map;
import java.util.UUID;

/** Agent 노드가 만든 산출물을 저장한다. 각 노드가 쓸 수 있는 시점을 상태로 제한한다. */
@Service
public class AgentResultService {

    public record RequirementItem(String reqKey, String summary, String target, LocalDate effectiveDate,
                                  String transitional, String articleRef, String oldText, String newText,
                                  Map<String, Object> detail) {}

    private final AnalysisTaskRepository tasks;
    private final RequirementRepository requirements;
    private final RuleProposalRepository proposals;

    public AgentResultService(AnalysisTaskRepository tasks, RequirementRepository requirements,
                              RuleProposalRepository proposals) {
        this.tasks = tasks;
        this.requirements = requirements;
        this.proposals = proposals;
    }

    /** requirement_agent: 요건 추출 중에만, 전체 교체. */
    @Transactional
    public int replaceRequirements(UUID taskId, List<RequirementItem> items) {
        lockIn(taskId, "요건은", TaskStatus.EXTRACTING);
        requirements.deleteAll(requirements.findByTaskIdOrderByIdAsc(taskId));
        requirements.flush();
        for (RequirementItem i : nonNull(items)) {
            if (blank(i.reqKey()) || blank(i.summary())) throw bad("reqKey, summary 는 필수입니다");
            Requirement r = new Requirement();
            r.setTaskId(taskId);
            r.setReqKey(i.reqKey());
            r.setSummary(i.summary());
            r.setTarget(i.target());
            r.setEffectiveDate(i.effectiveDate());
            r.setTransitional(i.transitional());
            r.setArticleRef(i.articleRef());
            r.setOldText(i.oldText());
            r.setNewText(i.newText());
            r.setDetail(i.detail() == null ? Map.of() : i.detail());
            requirements.save(r);
        }
        return nonNull(items).size();
    }

    @Transactional
    public void saveReport(UUID taskId, Map<String, Object> report) {
        AnalysisTask task = load(taskId);
        task.setReport(report);
        tasks.save(task);
    }

    /**
     * proposal_agent · verify (v2 10.2): (proposalKey, version, round) 기준 upsert.
     * 확인 대기부터는 고정한다. 사용자가 본 제안과 반영할 제안이 같아야 한다. 수정 요청(10.3)이 들어오면
     * 백엔드가 다시 DESIGNING 으로 옮기고, 그 제안 묶음의 행이 새 version 으로 온다.
     */
    @Transactional
    public int upsertProposals(UUID taskId, List<Proposals.Item> items) {
        lockIn(taskId, "제안은", TaskStatus.DESIGNING, TaskStatus.VERIFYING);
        for (Proposals.Item i : nonNull(items)) {
            String invalid = Proposals.invalid(i);
            if (invalid != null) throw bad(invalid);
            RuleProposal p = proposals.findByTaskIdAndProposalKeyAndVersionAndRound(taskId, i.proposalKey(), i.versionOrDefault(), i.round())
                    .orElseGet(RuleProposal::new);
            p.setTaskId(taskId);
            p.setProposalKey(i.proposalKey());
            p.setVersion(i.versionOrDefault());
            p.setRound(i.round());
            p.setReqKey(i.reqKey());
            p.setProductId(i.productId());
            p.setRuleId(blank(i.ruleId()) ? null : i.ruleId());
            p.setRuleKey(i.ruleKey());
            p.setImpact(i.impact());
            p.setCurrentText(i.current());
            p.setProposedText(i.proposed());
            p.setUnit(i.unit());
            p.setCurrentValue(i.currentValue());
            p.setProposedValue(i.proposedValue());
            p.setEffectiveDate(i.effectiveDate());
            p.setBasis(i.basis());
            p.setReason(i.reason());
            p.setTransition(i.transition());
            p.setFollowUp(i.followUp() == null ? List.of() : List.copyOf(i.followUp()));
            p.setVerifyStatus(i.verifyStatus() == null ? RuleProposal.VerifyStatus.PENDING : i.verifyStatus());
            p.setVerifyLog(i.verifyLog());
            p.setRevisionOutcome(i.revisionOutcomeOrDefault());
            p.setRevisionResponse(i.revisionResponse());
            p.setChangedFields(i.changedFields() == null ? List.of() : List.copyOf(i.changedFields()));
            proposals.save(p);
        }
        return nonNull(items).size();
    }

    /** impact_agent ~ verify (v2 10.6): 전체 교체. 계약 값은 바꾸지 않고 목록만 남긴다. */
    @Transactional
    public int replaceContractImpacts(UUID taskId, List<Map<String, Object>> items) {
        lockIn(taskId, "계약 영향은", TaskStatus.LOCATING, TaskStatus.DESIGNING, TaskStatus.VERIFYING);
        for (Map<String, Object> i : nonNull(items)) {
            if (i.get("contractId") == null) throw bad("contractId 는 필수입니다");
        }
        AnalysisTask task = load(taskId);
        task.setContractImpacts(List.copyOf(nonNull(items)));
        tasks.save(task);
        return nonNull(items).size();
    }

    @Transactional
    public void saveEvaluation(UUID taskId, Map<String, Object> evaluation) {
        AnalysisTask task = load(taskId);
        task.setEvaluation(evaluation);
        tasks.save(task);
    }

    /** 작업 행을 잠그고 상태를 확인한다. 상태 전이(TaskEventService)와 같은 잠금을 쓴다. */
    private void lockIn(UUID taskId, String what, TaskStatus... allowed) {
        AnalysisTask task = tasks.findForUpdate(taskId)
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "task not found"));
        if (!Arrays.asList(allowed).contains(task.getStatus())) {
            throw conflict(what + " " + Arrays.toString(allowed) + " 상태에서만 저장할 수 있습니다 (현재 " + task.getStatus() + ")");
        }
    }

    private AnalysisTask load(UUID taskId) {
        return tasks.findById(taskId).orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "task not found"));
    }

    private static <T> List<T> nonNull(List<T> l) {
        return l == null ? List.of() : l;
    }

    private static boolean blank(String s) {
        return s == null || s.isBlank();
    }

    private static ResponseStatusException bad(String m) {
        return new ResponseStatusException(HttpStatus.BAD_REQUEST, m);
    }

    private static ResponseStatusException conflict(String m) {
        return new ResponseStatusException(HttpStatus.CONFLICT, m);
    }
}
