package com.ruleripple.backend.task;

import com.ruleripple.backend.auth.AuthUser;
import com.ruleripple.backend.auth.User;
import com.ruleripple.backend.auth.UserRepository;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.http.HttpStatus;
import org.springframework.test.util.ReflectionTestUtils;
import org.springframework.web.server.ResponseStatusException;

import java.util.*;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.*;

class TaskServiceReviewTest {

    private static final AuthUser DEV = new AuthUser(3L, "dev1", "개발자", User.Role.DEVELOPER);

    private final UUID taskId = UUID.randomUUID();
    private final AnalysisTaskRepository tasks = mock(AnalysisTaskRepository.class);
    private final ChangeSpecRepository changeSpecs = mock(ChangeSpecRepository.class);
    private final CodePatchRepository patches = mock(CodePatchRepository.class);
    private final ApprovalRepository approvals = mock(ApprovalRepository.class);
    private final List<Approval> history = new ArrayList<>();
    private TaskService service;

    @BeforeEach
    void setUp() {
        service = new TaskService(tasks, mock(RegulationDocumentRepository.class), mock(RequirementRepository.class),
                changeSpecs, patches, mock(TestCaseRepository.class), approvals, mock(DeliveryRepository.class),
                mock(TaskEventService.class), mock(UserRepository.class));

        AnalysisTask task = AnalysisTask.create(1L, "credit-loan-ops v3 → v4");
        task.setStatus(TaskStatus.WAITING_REVIEW);
        when(tasks.findForUpdate(taskId)).thenReturn(Optional.of(task));
        when(changeSpecs.findByTaskIdOrderByIdAsc(taskId)).thenReturn(List.of(spec("S1"), spec("S2")));
        when(approvals.findByTaskIdOrderByIdAsc(taskId)).thenReturn(history);
        when(approvals.save(any())).thenAnswer(inv -> {
            Approval a = inv.getArgument(0);
            ReflectionTestUtils.setField(a, "id", (long) history.size() + 1);
            history.add(a);
            return a;
        });
    }

    @Test
    void approveRejectsExclusion() {
        assertBadRequest(() -> service.review(taskId, DEV, Approval.Decision.APPROVE, null, List.of("S1")));
    }

    @Test
    void exclusionOnlyRequestNeedsNoComment() {
        Approval a = service.review(taskId, DEV, Approval.Decision.REQUEST_CHANGES, null, List.of("S2"));
        assertThat(a.getPayload()).containsEntry("excludedSpecKeys", List.of("S2"));
    }

    @Test
    void requestChangesNeedsCommentOrExclusion() {
        assertBadRequest(() -> service.review(taskId, DEV, Approval.Decision.REQUEST_CHANGES, " ", List.of()));
    }

    @Test
    void cannotExcludeUnknownOrAlreadyExcludedSpec() {
        assertBadRequest(() -> service.review(taskId, DEV, Approval.Decision.REQUEST_CHANGES, null, List.of("S9")));
        service.review(taskId, DEV, Approval.Decision.REQUEST_CHANGES, null, List.of("S2"));
        assertBadRequest(() -> service.review(taskId, DEV, Approval.Decision.REQUEST_CHANGES, null, List.of("S2")));
    }

    @Test
    void approveSkipsStalePatchOfExcludedSpec() {
        // a.py 는 2회차에서 다시 만들어졌고, b.py 는 S2 만 다뤄서 제외 뒤 다시 만들어지지 않았다.
        CodePatch a1 = patch(1L, "a.py", 1, List.of("S1", "S2"));
        CodePatch b1 = patch(2L, "b.py", 1, List.of("S2"));
        CodePatch a2 = patch(3L, "a.py", 2, List.of("S1"));
        when(patches.findByTaskIdOrderByRoundAscIdAsc(taskId)).thenReturn(List.of(a1, b1, a2));

        service.review(taskId, DEV, Approval.Decision.REQUEST_CHANGES, null, List.of("S2"));
        Approval approved = service.review(taskId, DEV, Approval.Decision.APPROVE, null, null);

        assertThat(approved.getPayload().get("approvedPatchIds")).isEqualTo(List.of(3L));
        assertThat(approved.getPayload()).doesNotContainKey("excludedSpecKeys");
    }

    @Test
    void currentPatchesDropsOnlyFullyExcluded() {
        CodePatch mixed = patch(1L, "a.py", 1, List.of("S1", "S2"));
        CodePatch only = patch(2L, "b.py", 1, List.of("S2"));
        CodePatch none = patch(3L, "c.py", 1, List.of());

        assertThat(TaskService.currentPatches(List.of(mixed, only, none), Set.of("S2")))
                .containsExactly(mixed, none);
    }

    @Test
    void excludedSpecKeysAccumulatesRequestChangesOnly() {
        history.add(new Approval(taskId, Approval.Step.REVIEW, Approval.Decision.REQUEST_CHANGES, 3L, null,
                Map.of("excludedSpecKeys", List.of("S1"))));
        history.add(new Approval(taskId, Approval.Step.REVIEW, Approval.Decision.REQUEST_CHANGES, 3L, "c",
                Map.of("excludedSpecKeys", List.of("S2"))));
        history.add(new Approval(taskId, Approval.Step.REVIEW, Approval.Decision.APPROVE, 3L, null,
                Map.of("excludedSpecKeys", List.of("S3"))));

        assertThat(TaskService.excludedSpecKeys(history)).containsExactly("S1", "S2");
    }

    private static ChangeSpec spec(String key) {
        ChangeSpec c = new ChangeSpec();
        c.setSpecKey(key);
        c.setTarget(true);
        return c;
    }

    private static CodePatch patch(Long id, String file, int round, List<String> specKeys) {
        CodePatch p = new CodePatch();
        p.setId(id);
        p.setBundle("B:" + file);
        p.setFilePath(file);
        p.setRound(round);
        p.setSpecKeys(specKeys);
        p.setVerifyStatus(CodePatch.VerifyStatus.PASSED);
        return p;
    }

    private static void assertBadRequest(Runnable call) {
        assertThatThrownBy(call::run)
                .isInstanceOfSatisfying(ResponseStatusException.class,
                        e -> assertThat(e.getStatusCode()).isEqualTo(HttpStatus.BAD_REQUEST));
    }
}
