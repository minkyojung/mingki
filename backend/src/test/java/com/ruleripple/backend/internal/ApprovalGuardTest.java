package com.ruleripple.backend.internal;

import com.ruleripple.backend.task.*;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.http.HttpStatus;
import org.springframework.test.util.ReflectionTestUtils;
import org.springframework.web.server.ResponseStatusException;

import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

class ApprovalGuardTest {

    private final UUID taskId = UUID.randomUUID();
    private final ApprovalRepository approvals = mock(ApprovalRepository.class);
    private final AnalysisTaskRepository tasks = mock(AnalysisTaskRepository.class);
    private final ApprovalGuard guard = new ApprovalGuard(approvals, tasks);
    private Approval approve;

    @BeforeEach
    void setUp() {
        // S2 를 제외한 수정 요청 → 재설계 · 재검증 → 승인
        Approval changes = approval(1L, Approval.Decision.REQUEST_CHANGES, Map.of("excludedSpecKeys", List.of("S2")));
        approve = approval(2L, Approval.Decision.APPROVE, Map.of("approvedPatchIds", List.of(3L)));
        when(approvals.findById(2L)).thenReturn(Optional.of(approve));
        when(approvals.findByTaskIdOrderByIdAsc(taskId)).thenReturn(List.of(changes, approve));

        AnalysisTask task = AnalysisTask.create(1L, "t");
        task.setStatus(TaskStatus.PUBLISHING);
        when(tasks.findById(taskId)).thenReturn(Optional.of(task));
    }

    @Test
    void passesForSpecsStillInScope() {
        assertThat(guard.check(taskId, 2L, List.of("S1"))).isSameAs(approve);
    }

    @Test
    void blocksSpecExcludedByEarlierRequestChanges() {
        assertThatThrownBy(() -> guard.check(taskId, 2L, List.of("S1", "S2")))
                .isInstanceOfSatisfying(ResponseStatusException.class,
                        e -> assertThat(e.getStatusCode()).isEqualTo(HttpStatus.FORBIDDEN));
    }

    private Approval approval(Long id, Approval.Decision decision, Map<String, Object> payload) {
        Approval a = new Approval(taskId, Approval.Step.REVIEW, decision, 3L, null, payload);
        ReflectionTestUtils.setField(a, "id", id);
        return a;
    }
}
