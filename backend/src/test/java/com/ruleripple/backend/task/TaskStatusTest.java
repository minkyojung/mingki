package com.ruleripple.backend.task;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.EnumSource;

import static com.ruleripple.backend.task.TaskStatus.*;
import static org.assertj.core.api.Assertions.assertThat;

class TaskStatusTest {

    @Test
    void agentFollowsHappyPath() {
        assertThat(EXTRACTING.agentCanMoveTo(WAITING_REQUIREMENT_APPROVAL)).isTrue();
        assertThat(LOCATING.agentCanMoveTo(DESIGNING)).isTrue();
        assertThat(DESIGNING.agentCanMoveTo(VERIFYING)).isTrue();
        assertThat(VERIFYING.agentCanMoveTo(DESIGNING)).isTrue();
        assertThat(VERIFYING.agentCanMoveTo(LOCATING)).isTrue();
        assertThat(VERIFYING.agentCanMoveTo(WAITING_REVIEW)).isTrue();
        assertThat(PUBLISHING.agentCanMoveTo(DONE)).isTrue();
        assertThat(EXTRACTING.agentCanMoveTo(NO_CHANGE)).isTrue();
    }

    @Test
    void simplifiedFlowSkipsRequirementApproval() {
        assertThat(EXTRACTING.agentCanMoveTo(LOCATING)).isTrue();
        assertThat(EXTRACTING.agentCanMoveTo(DESIGNING)).isFalse();
    }

    @Test
    void agentCannotSkipReview() {
        assertThat(VERIFYING.agentCanMoveTo(PUBLISHING)).isFalse();
    }

    @ParameterizedTest
    @EnumSource(value = TaskStatus.class, names = {"WAITING_REQUIREMENT_APPROVAL", "WAITING_REVIEW"})
    void onlyHumansLeaveWaitingStates(TaskStatus waiting) {
        for (TaskStatus next : TaskStatus.values()) {
            boolean allowed = next == waiting || next == FAILED;
            assertThat(waiting.agentCanMoveTo(next)).as(waiting + " → " + next).isEqualTo(allowed);
        }
    }

    @ParameterizedTest
    @EnumSource(value = TaskStatus.class, names = {"DONE", "NO_CHANGE", "HELD", "REJECTED", "FAILED"})
    void terminalStatesStayPut(TaskStatus terminal) {
        for (TaskStatus next : TaskStatus.values()) {
            assertThat(terminal.agentCanMoveTo(next)).as(terminal + " → " + next).isEqualTo(next == terminal);
        }
    }
}
