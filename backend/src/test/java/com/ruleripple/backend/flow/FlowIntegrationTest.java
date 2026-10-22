package com.ruleripple.backend.flow;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.ruleripple.backend.task.AgentClient;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.mock.mockito.MockBean;
import org.springframework.boot.testcontainers.service.connection.ServiceConnection;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.mock.web.MockMultipartFile;
import org.springframework.test.context.TestPropertySource;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.ResultActions;
import org.springframework.test.web.servlet.request.MockHttpServletRequestBuilder;
import org.springframework.web.client.HttpClientErrorException;
import org.testcontainers.containers.PostgreSQLContainer;
import org.testcontainers.containers.RabbitMQContainer;
import org.testcontainers.junit.jupiter.Container;
import org.testcontainers.junit.jupiter.Testcontainers;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.function.Predicate;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.doThrow;
import org.mockito.ArgumentCaptor;
import com.ruleripple.backend.task.Approval;
import static org.mockito.Mockito.timeout;
import static org.mockito.Mockito.verify;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.*;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * 진짜 Postgres · RabbitMQ 위에서 업로드부터 반영 완료까지 한 바퀴 (Docker 가 없으면 건너뛴다).
 *
 * Agent 는 가짜다. Agent 가 보낼 콜백은 테스트가 /internal/** 로 직접 보낸다. 제안 · 확인 요청 본문은
 * docs/contracts 예시를 그대로 써서 계약 파일과 서버가 맞는지도 같이 본다.
 * 기동만으로 Flyway 전체 적용과 Hibernate 스키마 검증(ddl-auto=validate)을 확인한다.
 */
@SpringBootTest
@AutoConfigureMockMvc
@Testcontainers(disabledWithoutDocker = true)
@TestPropertySource(properties = "ruleripple.queue.failure-rate=0")
class FlowIntegrationTest {

    @Container
    @ServiceConnection
    static PostgreSQLContainer<?> postgres = new PostgreSQLContainer<>("postgres:16-alpine");

    @Container
    @ServiceConnection
    static RabbitMQContainer rabbit = new RabbitMQContainer("rabbitmq:3.13-management-alpine");

    private static final Path CONTRACTS = Path.of("../docs/contracts");
    private static final Path REGULATIONS = Path.of("../data/regulations/credit-loan-ops");
    private static final String INTERNAL = "rr-internal-dev-token";
    private static final TypeReference<Map<String, Object>> MAP = new TypeReference<>() {};

    @Autowired MockMvc mvc;
    @Autowired ObjectMapper json;
    @MockBean AgentClient agent;

    private String token;

    @BeforeEach
    void loginAndReset() throws Exception {
        JsonNode login = read(mvc.perform(post("/api/auth/login").contentType(MediaType.APPLICATION_JSON)
                .content("{\"username\":\"officer1\",\"password\":\"password\"}")).andExpect(status().isOk()));
        token = login.get("token").asText();
        mvc.perform(user(post("/api/demo/reset"))).andExpect(status().isOk());
    }

    @Test
    void uploadToReviewToApplyToDone() throws Exception {
        UUID id = uploadAndReachReview();

        JsonNode task = getJson("/api/ai/tasks/" + id);
        assertThat(task.at("/pendingAction/step").asText()).isEqualTo("REVIEW");
        assertThat(task.at("/counts/proposals").asInt()).isEqualTo(4);
        assertThat(getJson("/api/ai/tasks/" + id + "/proposals").get("canApprove").asBoolean()).isTrue();

        // 반려는 사유가 필수다
        mvc.perform(user(post("/api/ai/tasks/" + id + "/review")).contentType(MediaType.APPLICATION_JSON)
                .content("{\"decision\":\"REJECT\"}")).andExpect(status().isBadRequest());

        // 확인: P005 를 빼고 P001 만 반영 (계약 예시 그대로)
        JsonNode approval = read(mvc.perform(user(post("/api/ai/tasks/" + id + "/review"))
                .contentType(MediaType.APPLICATION_JSON).content(contract("review.post.json"))).andExpect(status().isOk()));
        long approvalId = approval.get("id").asLong();
        verify(agent, timeout(5000)).resume(any(), any());
        assertThat(taskStatus(id)).isEqualTo("PUBLISHING");

        // Agent apply 노드 → business-mcp → 반영 접수. 진짜 RabbitMQ 를 거쳐 워커가 처리한다
        String key = id + ":apply:P001:DSR_LIMIT";
        JsonNode queued = read(internal(post("/internal/product-rules/versions"), body(Map.of("taskId", id, "approvalId", approvalId, "proposalKey", "P001:DSR_LIMIT", "idempotencyKey", key)))
                .andExpect(status().isAccepted()));
        String jobId = queued.get("jobId").asText();
        JsonNode job = await("/internal/jobs/" + jobId, true, j -> "DONE".equals(j.get("status").asText()));
        assertThat(job.at("/result/closedRuleId").asText()).isEqualTo("R001");
        assertThat(job.at("/result/newRuleId").asText()).isEqualTo("R083");

        // 같은 멱등키는 새로 만들지 않고, 제외한 제안은 반영할 수 없다
        assertThat(read(internal(post("/internal/product-rules/versions"), body(Map.of("taskId", id, "approvalId", approvalId, "proposalKey", "P001:DSR_LIMIT", "idempotencyKey", key)))
                .andExpect(status().isAccepted())).get("duplicate").asBoolean()).isTrue();
        internal(post("/internal/product-rules/versions"), body(Map.of("taskId", id, "approvalId", approvalId, "proposalKey", "P005:DSR_LIMIT", "idempotencyKey", id + ":apply:P005:DSR_LIMIT")))
                .andExpect(status().isForbidden());

        // 시행일 전날은 종전 0.40, 시행일부터 0.35
        assertThat(rule("2026-11-30")).containsEntry("ruleId", "R001").containsEntry("value", "0.40");
        assertThat(rule("2026-12-01")).containsEntry("ruleId", "R083").containsEntry("value", "0.35");

        // Agent report → DONE
        internal(put("/internal/tasks/" + id + "/report"), body(Map.of("status", "DONE", "summary", "1건 반영", "markdown", "# 제안서"))).andExpect(status().isOk());
        agentEvent(id, "DONE", Map.of("outcome", "DONE"));
        JsonNode report = getJson("/api/ai/tasks/" + id + "/report");
        assertThat(report.at("/task/status").asText()).isEqualTo("DONE");
        assertThat(report.at("/review/decision").asText()).isEqualTo("APPROVE");
        assertThat(report.at("/deliveries/0/kind").asText()).isEqualTo("RULE_VERSION");
        assertThat(report.at("/deliveries/0/externalRef").asText()).isEqualTo("R083");
    }

    @Test
    void decisionTheAgentRefusesGoesBackToReview() throws Exception {
        doThrow(HttpClientErrorException.create(HttpStatus.BAD_REQUEST, "Bad Request", HttpHeaders.EMPTY,
                "{\"detail\":\"승인 불가 제안: P001:DSR_LIMIT: 검증 실패\"}".getBytes(StandardCharsets.UTF_8), StandardCharsets.UTF_8))
                .when(agent).resume(any(), any());
        UUID id = uploadAndReachReview();

        mvc.perform(user(post("/api/ai/tasks/" + id + "/review")).contentType(MediaType.APPLICATION_JSON)
                .content(contract("review.post.json"))).andExpect(status().isOk());

        // FAILED 가 아니라 확인 대기로 돌아오고, Agent 가 받지 않은 사유가 보인다
        JsonNode task = await("/api/ai/tasks/" + id, false, t -> "WAITING_REVIEW".equals(t.at("/task/status").asText())
                && !t.get("errorMessage").isNull());
        assertThat(task.get("errorMessage").asText()).contains("승인 불가 제안: P001:DSR_LIMIT");
        assertThat(task.at("/pendingAction/step").asText()).isEqualTo("REVIEW");

        // 다시 결정할 수 있다. 반려하면 사유는 지워진다
        mvc.perform(user(post("/api/ai/tasks/" + id + "/review")).contentType(MediaType.APPLICATION_JSON)
                .content(contract("review.post.reject.json"))).andExpect(status().isOk());
        JsonNode rejected = getJson("/api/ai/tasks/" + id);
        assertThat(rejected.at("/task/status").asText()).isEqualTo("REJECTED");
        assertThat(rejected.get("errorMessage").isNull()).isTrue();
    }

    @Test
    void newRuleFromANewArticleIsAddedWithoutClosingARow() throws Exception {
        // 새로 생긴 조항(제15조의2)의 의무: 기존 규칙이 없어 ruleId · currentValue 가 없고, 값은 true · bool
        String newRule = """
                {"items": [{"proposalKey": "P001:SENIOR_EXPLANATION_REQUIRED", "reqKey": "REQ-15-2", "productId": "P001",
                  "ruleId": null, "ruleKey": "SENIOR_EXPLANATION_REQUIRED", "impact": "change_required",
                  "current": "없음", "proposed": "고령자에게 설명했는지 확인한다", "unit": "bool",
                  "currentValue": null, "proposedValue": "true", "effectiveDate": "2026-12-01",
                  "basis": "reg://credit-loan-ops/v4/15-2", "reason": "새 조항의 의무", "transition": null,
                  "followUp": [], "round": 1, "verifyStatus": "PASSED", "verifyLog": null}]}
                """;
        UUID id = uploadAndReachReview(newRule);
        JsonNode approval = read(mvc.perform(user(post("/api/ai/tasks/" + id + "/review")).contentType(MediaType.APPLICATION_JSON)
                .content("{\"decision\":\"APPROVE\",\"comment\":\"새 규칙 반영\"}")).andExpect(status().isOk()));

        JsonNode queued = read(internal(post("/internal/product-rules/versions"), body(Map.of("taskId", id,
                "approvalId", approval.get("id").asLong(), "proposalKey", "P001:SENIOR_EXPLANATION_REQUIRED",
                "idempotencyKey", id + ":apply:P001:SENIOR_EXPLANATION_REQUIRED"))).andExpect(status().isAccepted()));
        JsonNode job = await("/internal/jobs/" + queued.get("jobId").asText(), true, j -> "DONE".equals(j.get("status").asText()));
        assertThat(job.at("/result/closedRuleId").isNull()).isTrue();
        assertThat(job.at("/result/newRuleId").asText()).isEqualTo("R083");

        // 시행일 전에는 없고, 시행일부터 새 행 하나. 근거 조항은 제안의 basis 에서 온다
        assertThat(rules("SENIOR_EXPLANATION_REQUIRED", "2026-11-30")).isEmpty();
        JsonNode added = rules("SENIOR_EXPLANATION_REQUIRED", "2026-12-01");
        assertThat(added).hasSize(1);
        assertThat(added.get(0).get("value").asText()).isEqualTo("true");
        assertThat(added.get(0).get("unit").asText()).isEqualTo("bool");
        assertThat(added.get(0).get("basisArticles").get(0).asText()).isEqualTo("credit-loan-ops/15-2");
    }

    // ---------- 수정 요청 (10.3) ----------

    @Test
    void requestChangesRewritesOneProposalAndReturnsToReview() throws Exception {
        UUID id = uploadAndReachReview();

        // 접수: 확인 기록에 REQUEST_CHANGES, 작업은 DESIGNING, Agent 에 /resume
        JsonNode accepted = read(mvc.perform(user(post("/api/ai/tasks/" + id + "/revisions")).contentType(MediaType.APPLICATION_JSON)
                .content(contract("revisions.post.json"))).andExpect(status().isAccepted()));
        long revisionId = accepted.get("revisionId").asLong();
        assertThat(accepted.get("status").asText()).isEqualTo("DESIGNING");
        assertThat(taskStatus(id)).isEqualTo("DESIGNING");
        ArgumentCaptor<Approval> sent = ArgumentCaptor.forClass(Approval.class);
        verify(agent, timeout(5000)).resume(any(), sent.capture());
        assertThat(sent.getValue().getDecision()).isEqualTo(Approval.Decision.REQUEST_CHANGES);
        assertThat(sent.getValue().getPayload()).containsEntry("proposalId", "REQ-8:P001").containsEntry("baseVersion", 1);
        assertThat(getJson("/api/ai/tasks/" + id + "/revisions/" + revisionId).get("state").asText()).isEqualTo("processing");

        // 다시 쓰는 동안 확인 · 두 번째 수정 요청은 409
        mvc.perform(user(post("/api/ai/tasks/" + id + "/review")).contentType(MediaType.APPLICATION_JSON)
                .content(contract("review.post.json"))).andExpect(status().isConflict());
        mvc.perform(user(post("/api/ai/tasks/" + id + "/revisions")).contentType(MediaType.APPLICATION_JSON)
                .content(contract("revisions.post.json"))).andExpect(status().isConflict());

        // Agent: P001 만 2버전 1회차로 다시 쓰고, 검사 뒤 확인 대기
        agentEvent(id, "STEP", Map.of("node", "proposal_agent"));
        internal(put("/internal/tasks/" + id + "/proposals"), rewritten("P001:DSR_LIMIT", 2, "revised",
                "필수 조치에 기존 계약 연장 안내를 넣었습니다", List.of("actions"))).andExpect(status().isOk());
        agentEvent(id, "STEP", Map.of("node", "verify", "round", 1));
        agentEvent(id, "APPROVAL_REQUIRED", Map.of("step", "REVIEW"));
        assertThat(taskStatus(id)).isEqualTo("WAITING_REVIEW");

        JsonNode proposals = getJson("/api/ai/tasks/" + id + "/proposals");
        assertThat(proposals.get("latestRound").asInt()).isEqualTo(2);   // 다른 묶음은 아직 1버전 2회차. 현재 행 기준이다
        assertThat(proposals.get("canApprove").asBoolean()).isTrue();
        JsonNode current = null;
        for (JsonNode p : proposals.get("proposals")) {
            if ("P001:DSR_LIMIT".equals(p.get("proposalKey").asText()) && p.get("latest").asBoolean()) current = p;
        }
        assertThat(current).isNotNull();
        assertThat(current.get("version").asInt()).isEqualTo(2);
        assertThat(current.get("revisionOutcome").asText()).isEqualTo("revised");
        assertThat(current.get("changedFields").get(0).asText()).isEqualTo("actions");
        assertThat(current.get("followUp").get(0).asText()).contains("기존 계약 연장");

        JsonNode revision = getJson("/api/ai/tasks/" + id + "/revisions/" + revisionId);
        assertThat(revision.get("state").asText()).isEqualTo("done");
        assertThat(revision.get("version").asInt()).isEqualTo(2);
        assertThat(revision.get("outcome").asText()).isEqualTo("revised");
        assertThat(revision.get("proposals")).hasSize(2);   // 1버전 2회차 + 2버전 1회차

        // 새 버전을 보고 승인 → 반영 중. 확인 기록은 수정 요청 · 승인 두 줄
        mvc.perform(user(post("/api/ai/tasks/" + id + "/review")).contentType(MediaType.APPLICATION_JSON)
                .content(contract("review.post.json"))).andExpect(status().isOk());
        assertThat(taskStatus(id)).isEqualTo("PUBLISHING");
        JsonNode approvals = getJson("/api/ai/tasks/" + id).get("approvals");
        assertThat(approvals).hasSize(2);
        assertThat(approvals.get(0).get("decision").asText()).isEqualTo("REQUEST_CHANGES");
        assertThat(approvals.get(1).get("decision").asText()).isEqualTo("APPROVE");
    }

    @Test
    void revisionRequestChecksProposalVersionAndComment() throws Exception {
        UUID id = uploadAndReachReview();
        String path = "/api/ai/tasks/" + id + "/revisions";

        mvc.perform(user(post(path)).contentType(MediaType.APPLICATION_JSON)
                .content("{\"proposalId\":\"REQ-8:P001\",\"baseVersion\":2,\"comment\":\"안내 추가\"}")).andExpect(status().isConflict());
        mvc.perform(user(post(path)).contentType(MediaType.APPLICATION_JSON)
                .content("{\"proposalId\":\"REQ-8:P999\",\"baseVersion\":1,\"comment\":\"안내 추가\"}")).andExpect(status().isBadRequest());
        mvc.perform(user(post(path)).contentType(MediaType.APPLICATION_JSON)
                .content("{\"proposalId\":\"REQ-8:P003\",\"baseVersion\":1,\"comment\":\"안내 추가\"}")).andExpect(status().isBadRequest());   // 유지
        mvc.perform(user(post(path)).contentType(MediaType.APPLICATION_JSON)
                .content("{\"proposalId\":\"REQ-8:P001\",\"baseVersion\":1,\"comment\":\" \"}")).andExpect(status().isBadRequest());
        assertThat(taskStatus(id)).isEqualTo("WAITING_REVIEW");

        // 같은 revisionId 재전송은 처음 접수를 돌려주고 Agent 를 다시 부르지 않는다. 내용이 다르면 409
        String first = "{\"revisionId\":\"rev-1\",\"proposalId\":\"REQ-8:P001\",\"baseVersion\":1,\"comment\":\"안내 추가\"}";
        JsonNode a = read(mvc.perform(user(post(path)).contentType(MediaType.APPLICATION_JSON).content(first)).andExpect(status().isAccepted()));
        JsonNode b = read(mvc.perform(user(post(path)).contentType(MediaType.APPLICATION_JSON).content(first)).andExpect(status().isAccepted()));
        assertThat(b.get("revisionId").asLong()).isEqualTo(a.get("revisionId").asLong());
        mvc.perform(user(post(path)).contentType(MediaType.APPLICATION_JSON)
                .content(first.replace("안내 추가", "다른 요청"))).andExpect(status().isConflict());
        verify(agent, timeout(5000).times(1)).resume(any(), any());
    }

    @Test
    void revisionTheAgentRefusesGoesBackToReview() throws Exception {
        doThrow(HttpClientErrorException.create(HttpStatus.BAD_REQUEST, "Bad Request", HttpHeaders.EMPTY,
                "{\"detail\":\"무엇을 바꿀지 구체적으로 적어주세요\"}".getBytes(StandardCharsets.UTF_8), StandardCharsets.UTF_8))
                .when(agent).resume(any(), any());
        UUID id = uploadAndReachReview();
        JsonNode accepted = read(mvc.perform(user(post("/api/ai/tasks/" + id + "/revisions")).contentType(MediaType.APPLICATION_JSON)
                .content("{\"proposalId\":\"REQ-8:P001\",\"baseVersion\":1,\"comment\":\"다시 써줘\"}")).andExpect(status().isAccepted()));

        JsonNode task = await("/api/ai/tasks/" + id, false, t -> "WAITING_REVIEW".equals(t.at("/task/status").asText())
                && !t.get("errorMessage").isNull());
        assertThat(task.get("errorMessage").asText()).contains("수정 요청을 받지 않았습니다").contains("구체적으로");
        assertThat(getJson("/api/ai/tasks/" + id + "/revisions/" + accepted.get("revisionId").asLong()).get("state").asText()).isEqualTo("rejected");
        // 제안은 1버전 그대로이고 바로 승인할 수 있다
        assertThat(getJson("/api/ai/tasks/" + id + "/proposals").get("canApprove").asBoolean()).isTrue();
    }

    @Test
    void approvalChecksOnlyChosenProposals() throws Exception {
        // P005 가 근거 검사에 실패한 채 확인 대기. 빼고 승인할 수 있고, 넣으면 409
        JsonNode items = json.readTree(contract("proposals.put.json"));
        for (JsonNode i : items.get("items")) {
            if ("P005:DSR_LIMIT".equals(i.get("proposalKey").asText())) {
                ((com.fasterxml.jackson.databind.node.ObjectNode) i).put("verifyStatus", "FAILED").put("verifyLog", "단위가 다릅니다");
            }
        }
        UUID id = uploadAndReachReview(json.writeValueAsString(items));
        assertThat(getJson("/api/ai/tasks/" + id + "/proposals").get("canApprove").asBoolean()).isTrue();

        mvc.perform(user(post("/api/ai/tasks/" + id + "/review")).contentType(MediaType.APPLICATION_JSON)
                .content("{\"decision\":\"APPROVE\"}")).andExpect(status().isConflict());
        mvc.perform(user(post("/api/ai/tasks/" + id + "/review")).contentType(MediaType.APPLICATION_JSON)
                .content(contract("review.post.json"))).andExpect(status().isOk());
        assertThat(taskStatus(id)).isEqualTo("PUBLISHING");
    }

    // ---------- Agent 흉내 ----------

    /** 업로드 → 요건 → 영향 → 제안(기본은 계약 예시) → 검사 → 확인 대기. Agent 콜백 순서는 BACKEND_API 6절. */
    private UUID uploadAndReachReview() throws Exception {
        return uploadAndReachReview(contract("proposals.put.json"));
    }

    private UUID uploadAndReachReview(String proposals) throws Exception {
        JsonNode created = read(mvc.perform(user(multipart("/api/ai/tasks")
                        .file(new MockMultipartFile("oldFile", "v3.md", "text/markdown", Files.readAllBytes(REGULATIONS.resolve("v3.md"))))
                        .file(new MockMultipartFile("newFile", "v4.md", "text/markdown", Files.readAllBytes(REGULATIONS.resolve("v4.md"))))))
                .andExpect(status().isAccepted()));
        UUID id = UUID.fromString(created.get("taskId").asText());
        verify(agent, timeout(5000)).run(any(), any());

        agentEvent(id, "STEP", Map.of("node", "requirement_agent"));
        internal(put("/internal/tasks/" + id + "/requirements"), body(Map.of("items", List.of(Map.of(
                "reqKey", "REQ-8", "summary", "DSR 한도 40% → 35%", "articleRef", "제8조", "effectiveDate", "2026-12-01")))))
                .andExpect(status().isOk());
        agentEvent(id, "STEP", Map.of("node", "product_impact_agent"));
        agentEvent(id, "STEP", Map.of("node", "proposal_agent"));
        internal(put("/internal/tasks/" + id + "/proposals"), proposals).andExpect(status().isOk());
        agentEvent(id, "STEP", Map.of("node", "verify", "round", 2));
        agentEvent(id, "APPROVAL_REQUIRED", Map.of("step", "REVIEW"));
        assertThat(taskStatus(id)).isEqualTo("WAITING_REVIEW");
        return id;
    }

    /** 수정 요청 뒤 Agent 가 보내는 제안 저장: 계약 예시의 한 행을 새 버전 1회차로, 수정 요청 칸을 채워서. */
    private String rewritten(String key, int version, String outcome, String response, List<String> changed) throws Exception {
        JsonNode items = json.readTree(contract("proposals.put.json"));
        for (JsonNode i : items.get("items")) {
            if (key.equals(i.get("proposalKey").asText())) {
                var row = (com.fasterxml.jackson.databind.node.ObjectNode) i;
                row.put("version", version).put("round", 1).put("revisionOutcome", outcome).put("revisionResponse", response);
                row.putArray("changedFields").add(changed.get(0));
                row.putArray("followUp").add("기존 계약 연장은 종전 0.40 이 적용된다고 안내").add("심사 전산 한도 비율 변경");
                return json.writeValueAsString(Map.of("items", List.of(row)));
            }
        }
        throw new AssertionError("예시에 없는 키: " + key);
    }

    private void agentEvent(UUID id, String type, Map<String, Object> payload) throws Exception {
        internal(post("/internal/tasks/" + id + "/events"), body(Map.of("type", type, "payload", payload)))
                .andExpect(status().isOk());
    }

    // ---------- 도우미 ----------

    private MockHttpServletRequestBuilder user(MockHttpServletRequestBuilder req) {
        return req.header(HttpHeaders.AUTHORIZATION, "Bearer " + token);
    }

    private ResultActions internal(MockHttpServletRequestBuilder req, String body) throws Exception {
        return mvc.perform(req.header("X-Internal-Token", INTERNAL).contentType(MediaType.APPLICATION_JSON).content(body));
    }

    private JsonNode getJson(String path) throws Exception {
        return read(mvc.perform(user(get(path))).andExpect(status().isOk()));
    }

    private String taskStatus(UUID id) throws Exception {
        return getJson("/api/ai/tasks/" + id).at("/task/status").asText();
    }

    private Map<String, Object> rule(String asOf) throws Exception {
        JsonNode rows = rules("DSR_LIMIT", asOf);
        assertThat(rows).hasSize(1);
        return json.convertValue(rows.get(0), MAP);
    }

    /** P001 의 그날 유효한 규칙 행 */
    private JsonNode rules(String key, String asOf) throws Exception {
        return read(mvc.perform(get("/internal/product-rules").header("X-Internal-Token", INTERNAL)
                .param("productId", "P001").param("key", key).param("asOf", asOf)).andExpect(status().isOk()));
    }

    /** 비동기 처리(Agent 디스패처 · 큐 워커)가 끝날 때까지 10초 기다린다. */
    private JsonNode await(String path, boolean internalPath, Predicate<JsonNode> done) throws Exception {
        long until = System.currentTimeMillis() + 10_000;
        JsonNode last = null;
        while (System.currentTimeMillis() < until) {
            MockHttpServletRequestBuilder req = get(path);
            last = read(mvc.perform(internalPath ? req.header("X-Internal-Token", INTERNAL) : user(req)).andExpect(status().isOk()));
            if (done.test(last)) return last;
            Thread.sleep(200);
        }
        throw new AssertionError("기다린 상태가 오지 않았습니다: " + path + " → " + last);
    }

    private String body(Object o) throws Exception {
        return json.writeValueAsString(o);
    }

    private static String contract(String name) throws Exception {
        return Files.readString(CONTRACTS.resolve(name));
    }

    private JsonNode read(ResultActions result) throws Exception {
        return json.readTree(result.andReturn().getResponse().getContentAsString(StandardCharsets.UTF_8));
    }
}
