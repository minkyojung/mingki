package com.ruleripple.backend.task;

import org.springframework.data.jpa.repository.JpaRepository;

import java.util.List;
import java.util.Optional;
import java.util.UUID;

public interface RuleProposalRepository extends JpaRepository<RuleProposal, Long> {
    List<RuleProposal> findByTaskIdOrderByProposalKeyAscVersionAscRoundAsc(UUID taskId);

    Optional<RuleProposal> findByTaskIdAndProposalKeyAndVersionAndRound(UUID taskId, String proposalKey, int version, int round);
}
