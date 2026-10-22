package com.ruleripple.backend.task;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.Test;

import java.io.IOException;
import java.nio.file.Path;
import java.time.LocalDate;
import java.util.ArrayList;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;

/** 제안 규칙 (docs/BACKEND_API.md 10.2 · 10.3). 계약 예시(docs/contracts)를 그대로 읽는다. */
class ProposalsTest {

    private static final Path CONTRACTS = Path.of("../docs/contracts");
    private static final ObjectMapper JSON = new ObjectMapper().findAndRegisterModules();

    private static List<Proposals.Item> exampleItems() throws IOException {
        JsonNode items = JSON.readTree(CONTRACTS.resolve("proposals.put.json").toFile()).get("items");
        List<Proposals.Item> out = new ArrayList<>();
        for (JsonNode n : items) out.add(JSON.treeToValue(n, Proposals.Item.class));
        return out;
    }

    private static Proposals.Item change(String ruleId, String currentValue, String unit) {
        return new Proposals.Item("P001:DSR_LIMIT", "REQ-8", "P001", ruleId, "DSR_LIMIT", "change_required",
                "DSR 한도 40%", "DSR 한도 35%", unit, currentValue, "0.35", LocalDate.of(2026, 12, 1),
                "reg://credit-loan-ops/v4/8", "개정 기준 초과", null, List.of(), 1, null, null, null, null, null, null);
    }

    @Test
    void contractExampleIsValid() throws IOException {
        List<Proposals.Item> items = exampleItems();
        assertThat(items).hasSize(4);
        assertThat(items).allSatisfy(i -> assertThat(Proposals.invalid(i)).isNull());
        assertThat(items.get(1).unit()).isEqualTo("percent");
        assertThat(items.get(1).verifyStatus()).isEqualTo(RuleProposal.VerifyStatus.PASSED);
    }

    @Test
    void changeRequiredNeedsValuesToApply() {
        assertThat(Proposals.invalid(change("R001", "0.40", "ratio"))).isNull();
        assertThat(Proposals.invalid(change("R001", null, "ratio"))).contains("currentValue");
        assertThat(Proposals.invalid(change("R001", "0.40", null))).contains("unit");
        // 새 규칙은 지금 값이 없어도 된다
        assertThat(Proposals.invalid(change(null, null, "bool"))).isNull();
    }

    @Test
    void rejectsUnknownImpactAndMissingReason() {
        var base = change("R001", "0.40", "ratio");
        var badImpact = new Proposals.Item(base.proposalKey(), base.reqKey(), base.productId(), base.ruleId(), base.ruleKey(),
                "CHANGE_REQUIRED", null, null, base.unit(), base.currentValue(), base.proposedValue(), base.effectiveDate(),
                null, "이유", null, null, 1, null, null, null, null, null, null);
        var noReason = new Proposals.Item("P003:DSR_LIMIT", "REQ-8", "P003", "R015", "DSR_LIMIT", "keep",
                null, null, null, null, null, null, null, " ", null, null, 1, null, null, null, null, null, null);
        assertThat(Proposals.invalid(badImpact)).contains("impact");
        assertThat(Proposals.invalid(noReason)).contains("reason");
    }

    @Test
    void latestPicksHighestRoundPerKey() {
        var all = List.of(row("P001:DSR_LIMIT", 1, "change_required", RuleProposal.VerifyStatus.FAILED),
                row("P001:DSR_LIMIT", 2, "change_required", RuleProposal.VerifyStatus.PASSED),
                row("P003:DSR_LIMIT", 1, "keep", RuleProposal.VerifyStatus.PASSED));
        List<RuleProposal> latest = Proposals.latest(all);
        assertThat(latest).extracting(RuleProposal::getProposalKey, RuleProposal::getRound)
                .containsExactly(org.assertj.core.groups.Tuple.tuple("P001:DSR_LIMIT", 2),
                        org.assertj.core.groups.Tuple.tuple("P003:DSR_LIMIT", 1));
        assertThat(Proposals.changeKeys(latest)).containsExactly("P001:DSR_LIMIT");
    }

    @Test
    void latestDropsRulesThatVanishedInTheRewrittenRound() {
        // 1회차 LLM 이 대상이 아닌 규칙(EXTRA)을 끼워 넣어 검사에 실패하고, 2회차에서 뺐다.
        var all = List.of(row("P001:DSR_LIMIT", 1, "change_required", RuleProposal.VerifyStatus.FAILED),
                row("P001:EXTRA", 1, "change_required", RuleProposal.VerifyStatus.FAILED),
                row("P001:DSR_LIMIT", 2, "change_required", RuleProposal.VerifyStatus.PASSED),
                row("P002:DSR_LIMIT", 1, "change_required", RuleProposal.VerifyStatus.PASSED));
        List<RuleProposal> latest = Proposals.latest(all);
        assertThat(latest).extracting(RuleProposal::getProposalKey, RuleProposal::getRound)
                .containsExactly(org.assertj.core.groups.Tuple.tuple("P001:DSR_LIMIT", 2),
                        org.assertj.core.groups.Tuple.tuple("P002:DSR_LIMIT", 1));
        assertThat(Proposals.canApprove(TaskStatus.WAITING_REVIEW, latest)).isTrue();
    }

    @Test
    void canApproveWhenAnyChangePassedAndOnlyChosenRowsBlock() {
        var passed = row("P001:DSR_LIMIT", 2, "change_required", RuleProposal.VerifyStatus.PASSED);
        var failed = row("P005:DSR_LIMIT", 1, "change_required", RuleProposal.VerifyStatus.FAILED);
        var keep = row("P003:DSR_LIMIT", 1, "keep", RuleProposal.VerifyStatus.PENDING);

        assertThat(Proposals.canApprove(TaskStatus.WAITING_REVIEW, List.of(passed, keep))).isTrue();
        assertThat(Proposals.canApprove(TaskStatus.WAITING_REVIEW, List.of(passed, failed))).isTrue();   // 실패한 것은 빼고 승인할 수 있다
        assertThat(Proposals.canApprove(TaskStatus.WAITING_REVIEW, List.of(failed))).isFalse();
        assertThat(Proposals.canApprove(TaskStatus.WAITING_REVIEW, List.of(keep))).isFalse();   // 반영할 게 없음
        assertThat(Proposals.canApprove(TaskStatus.VERIFYING, List.of(passed))).isFalse();

        // 승인 요청은 반영으로 고른 키만 검사한다
        assertThat(Proposals.blocked(List.of(passed, failed), List.of("P001:DSR_LIMIT"))).isEmpty();
        assertThat(Proposals.blocked(List.of(passed, failed), List.of("P001:DSR_LIMIT", "P005:DSR_LIMIT"))).containsExactly("P005:DSR_LIMIT");
    }

    @Test
    void latestPrefersHigherVersionOverHigherRound() {
        // 1버전은 2회차까지 갔고, 수정 요청으로 생긴 2버전은 1회차뿐이다. 2버전이 현재다
        var v1r1 = row("P001:DSR_LIMIT", 1, 1, "change_required", RuleProposal.VerifyStatus.FAILED);
        var v1r2 = row("P001:DSR_LIMIT", 1, 2, "change_required", RuleProposal.VerifyStatus.PASSED);
        var v2r1 = row("P001:DSR_LIMIT", 2, 1, "change_required", RuleProposal.VerifyStatus.PASSED);
        var other = row("P002:DSR_LIMIT", 1, 2, "change_required", RuleProposal.VerifyStatus.PASSED);
        List<RuleProposal> latest = Proposals.latest(List.of(v1r1, v1r2, v2r1, other));
        assertThat(latest).extracting(RuleProposal::getProposalKey, RuleProposal::getVersion, RuleProposal::getRound)
                .containsExactly(org.assertj.core.groups.Tuple.tuple("P001:DSR_LIMIT", 2, 1),
                        org.assertj.core.groups.Tuple.tuple("P002:DSR_LIMIT", 1, 2));
    }

    @Test
    void revisableRowsAreTheProposalBundleExceptKeep() {
        var change = row("P001:DSR_LIMIT", 1, "change_required", RuleProposal.VerifyStatus.PASSED);
        var keep = row("P003:DSR_LIMIT", 1, "keep", RuleProposal.VerifyStatus.PASSED);
        var check = row("P010:DSR_LIMIT", 1, "needs_check", RuleProposal.VerifyStatus.PASSED);
        List<RuleProposal> latest = List.of(change, keep, check);
        assertThat(Proposals.proposalId(change)).isEqualTo("REQ-8:P001");
        assertThat(Proposals.revisable(latest, "REQ-8:P001")).containsExactly(change);
        assertThat(Proposals.revisable(latest, "REQ-8:P010")).containsExactly(check);
        assertThat(Proposals.revisable(latest, "REQ-8:P003")).isEmpty();   // 유지는 제안이 아니다
        assertThat(Proposals.revisable(latest, "REQ-8:P999")).isEmpty();
    }

    @Test
    void revisionFieldsDefaultWhenAbsentAndRejectUnknownOutcome() {
        var base = change("R001", "0.40", "ratio");
        assertThat(base.versionOrDefault()).isEqualTo(1);
        assertThat(base.revisionOutcomeOrDefault()).isEqualTo(Proposals.NO_REVISION);
        var revised = new Proposals.Item(base.proposalKey(), base.reqKey(), base.productId(), base.ruleId(), base.ruleKey(),
                base.impact(), base.current(), base.proposed(), base.unit(), base.currentValue(), base.proposedValue(),
                base.effectiveDate(), base.basis(), base.reason(), null, List.of(), 1, null, null, 2, "revised", "조치에 안내 추가", List.of("actions"));
        assertThat(Proposals.invalid(revised)).isNull();
        var odd = new Proposals.Item(base.proposalKey(), base.reqKey(), base.productId(), base.ruleId(), base.ruleKey(),
                base.impact(), base.current(), base.proposed(), base.unit(), base.currentValue(), base.proposedValue(),
                base.effectiveDate(), base.basis(), base.reason(), null, List.of(), 1, null, null, 0, "rewritten", null, null);
        assertThat(Proposals.invalid(odd)).contains("version");
    }

    @Test
    void revisionExampleNamesAProposalAndItsVersion() throws IOException {
        var req = JSON.readValue(CONTRACTS.resolve("revisions.post.json").toFile(), TaskController.RevisionRequest.class);
        assertThat(req.proposalId()).isEqualTo("REQ-8:P001");
        assertThat(req.baseVersion()).isEqualTo(1);
        assertThat(req.comment()).isNotBlank();
        // 제안 예시의 P001 묶음과 맞아야 한다
        assertThat(exampleItems()).anySatisfy(i -> assertThat(i.reqKey() + ":" + i.productId()).isEqualTo(req.proposalId()));
        assertThat(Proposals.missingComment(Approval.Decision.REQUEST_CHANGES, " ")).isTrue();
        assertThat(Proposals.missingComment(Approval.Decision.REQUEST_CHANGES, req.comment())).isFalse();
    }

    @Test
    void reviewExampleCarriesExcludedProposalKeys() throws IOException {
        var req = JSON.readValue(CONTRACTS.resolve("review.post.json").toFile(), TaskController.ReviewRequest.class);
        assertThat(req.decision()).isEqualTo(Approval.Decision.APPROVE);
        assertThat(req.excludedProposalKeys()).containsExactly("P005:DSR_LIMIT");
        // 제외 키는 제안 예시 안의 change_required 제안이어야 한다
        assertThat(exampleItems()).anySatisfy(i -> {
            assertThat(i.proposalKey()).isEqualTo("P005:DSR_LIMIT");
            assertThat(i.impact()).isEqualTo(Proposals.CHANGE_REQUIRED);
        });
    }

    @Test
    void rejectNeedsReason() {
        assertThat(Proposals.missingComment(Approval.Decision.REJECT, null)).isTrue();
        assertThat(Proposals.missingComment(Approval.Decision.REJECT, "  ")).isTrue();
        assertThat(Proposals.missingComment(Approval.Decision.REJECT, "P010 판정을 미룰 수 없다")).isFalse();
        assertThat(Proposals.missingComment(Approval.Decision.APPROVE, null)).isFalse();   // 제외에는 사유를 요구하지 않는다
    }

    @Test
    void rejectExampleCarriesReason() throws IOException {
        var req = JSON.readValue(CONTRACTS.resolve("review.post.reject.json").toFile(), TaskController.ReviewRequest.class);
        assertThat(req.decision()).isEqualTo(Approval.Decision.REJECT);
        assertThat(Proposals.missingComment(req.decision(), req.comment())).isFalse();
    }

    @Test
    void reportExamplesMatchResponse() throws IOException {
        var done = JSON.readValue(CONTRACTS.resolve("report.get.json").toFile(), TaskDtos.ReportResponse.class);
        assertThat(done.review().decision()).isEqualTo("APPROVE");
        assertThat(done.review().approver().username()).isEqualTo("officer1");

        var rejected = JSON.readValue(CONTRACTS.resolve("report.get.rejected.json").toFile(), TaskDtos.ReportResponse.class);
        assertThat(rejected.review().decision()).isEqualTo("REJECT");
        assertThat(rejected.review().comment()).isNotBlank();
        assertThat(rejected.deliveries()).isEmpty();
    }

    private static RuleProposal row(String key, int round, String impact, RuleProposal.VerifyStatus status) {
        return row(key, 1, round, impact, status);
    }

    private static RuleProposal row(String key, int version, int round, String impact, RuleProposal.VerifyStatus status) {
        RuleProposal p = new RuleProposal();
        p.setProposalKey(key);
        p.setReqKey("REQ-8");
        p.setProductId(key.substring(0, key.indexOf(':')));
        p.setVersion(version);
        p.setRound(round);
        p.setImpact(impact);
        p.setVerifyStatus(status);
        return p;
    }
}
