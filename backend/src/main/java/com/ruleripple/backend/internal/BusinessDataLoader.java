package com.ruleripple.backend.internal;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.DeserializationFeature;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.PropertyNamingStrategies;
import com.ruleripple.backend.config.RuleRippleProperties;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.ApplicationArguments;
import org.springframework.boot.ApplicationRunner;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Transactional;

import java.io.IOException;
import java.math.BigDecimal;
import java.nio.file.Files;
import java.nio.file.Path;
import java.sql.Types;
import java.time.LocalDate;
import java.util.List;

/**
 * 기동 시 data/business/*.json 을 읽어 비어 있는 업무 테이블에 넣는다.
 * 샘플 업무 데이터의 원천은 JSON 하나뿐이다. 기동 때는 이미 행이 있는 테이블을 건드리지 않는다.
 * 시연 리셋(reseed)은 세 테이블을 비우고 다시 넣는다.
 * source · note 는 정답 힌트라 DB 에 넣지 않는다. 그 밖의 모르는 필드는 기동 오류로 잡는다.
 */
@Component
public class BusinessDataLoader implements ApplicationRunner {

    private static final Logger log = LoggerFactory.getLogger(BusinessDataLoader.class);

    record ProductRow(String productId, String name, String description, List<String> repaymentTypes,
                      int minTermMonths, int maxTermMonths, String status) {}

    @JsonIgnoreProperties({"source"})
    record RuleRow(String ruleId, String productId, String ruleKey, String value, String unit,
                   LocalDate effectiveFrom, LocalDate effectiveTo) {}

    @JsonIgnoreProperties({"note"})
    record ContractRow(String contractId, String productId, String customerId, LocalDate birthDate,
                       LocalDate appliedAt, LocalDate contractedAt, String status, long amount, int termMonths,
                       String repaymentType, LocalDate maturityDate, int extensionCount, long annualIncome,
                       BigDecimal dsrAtApproval, Boolean seniorApplied, BigDecimal rateDiscountPp,
                       String rejectReason) {}

    private final JdbcTemplate jdbc;
    private final ObjectMapper json;
    private final Path dir;

    public BusinessDataLoader(JdbcTemplate jdbc, ObjectMapper objectMapper, RuleRippleProperties props) {
        this.jdbc = jdbc;
        this.json = objectMapper.copy()
                .setPropertyNamingStrategy(PropertyNamingStrategies.SNAKE_CASE)
                .enable(DeserializationFeature.FAIL_ON_UNKNOWN_PROPERTIES);
        this.dir = Path.of(props.businessData().dir());
    }

    @Override
    @Transactional(rollbackFor = Exception.class)   // JSON 파싱 오류(IOException)에도 전부 되돌린다
    public void run(ApplicationArguments args) throws IOException {
        if (!Files.isDirectory(dir)) {
            log.warn("업무 데이터 폴더가 없어 시드를 건너뜀: {}", dir.toAbsolutePath());
            return;
        }
        loadProducts();
        loadRules();
        loadContracts();
    }

    /** 세 업무 테이블을 비우고 JSON 에서 다시 넣는다. 호출한 쪽 트랜잭션에 참여한다. */
    @Transactional(rollbackFor = Exception.class)
    public void reseed() throws IOException {
        if (!Files.isDirectory(dir)) {
            throw new IllegalStateException("업무 데이터 폴더가 없습니다: " + dir.toAbsolutePath());
        }
        jdbc.update("delete from loan_contracts");
        jdbc.update("delete from product_rules");
        jdbc.update("delete from products");
        loadProducts();
        loadRules();
        loadContracts();
    }

    private void loadProducts() throws IOException {
        if (!isEmpty("products")) return;
        List<ProductRow> rows = read("products.json", new TypeReference<>() {});
        jdbc.batchUpdate("""
                insert into products (product_id, name, description, repayment_types, min_term_months, max_term_months, status)
                values (?, ?, ?, ?::jsonb, ?, ?, ?)
                """, rows.stream().map(r -> new Object[]{
                        r.productId(), r.name(), r.description(), toJson(r.repaymentTypes()),
                        r.minTermMonths(), r.maxTermMonths(), r.status()}).toList(),
                new int[]{Types.VARCHAR, Types.VARCHAR, Types.VARCHAR, Types.VARCHAR, Types.INTEGER, Types.INTEGER, Types.VARCHAR});
        log.info("products {}건 시드", rows.size());
    }

    private void loadRules() throws IOException {
        if (!isEmpty("product_rules")) return;
        List<RuleRow> rows = read("product_rules.json", new TypeReference<>() {});
        jdbc.batchUpdate("""
                insert into product_rules (rule_id, product_id, rule_key, value, unit, effective_from, effective_to)
                values (?, ?, ?, ?, ?, ?, ?)
                """, rows.stream().map(r -> new Object[]{
                        r.ruleId(), r.productId(), r.ruleKey(), r.value(), r.unit(),
                        r.effectiveFrom(), r.effectiveTo()}).toList(),
                new int[]{Types.VARCHAR, Types.VARCHAR, Types.VARCHAR, Types.VARCHAR, Types.VARCHAR,
                        Types.DATE, Types.DATE});
        log.info("product_rules {}건 시드", rows.size());
    }

    private void loadContracts() throws IOException {
        if (!isEmpty("loan_contracts")) return;
        List<ContractRow> rows = read("loan_contracts.json", new TypeReference<>() {});
        jdbc.batchUpdate("""
                insert into loan_contracts (contract_id, product_id, customer_id, birth_date, applied_at, contracted_at,
                    status, amount, term_months, repayment_type, maturity_date, extension_count, annual_income,
                    dsr_at_approval, senior_applied, rate_discount_pp, reject_reason)
                values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, rows.stream().map(r -> new Object[]{
                        r.contractId(), r.productId(), r.customerId(), r.birthDate(), r.appliedAt(), r.contractedAt(),
                        r.status(), r.amount(), r.termMonths(), r.repaymentType(), r.maturityDate(), r.extensionCount(),
                        r.annualIncome(), r.dsrAtApproval(), r.seniorApplied(), r.rateDiscountPp(), r.rejectReason()}).toList(),
                new int[]{Types.VARCHAR, Types.VARCHAR, Types.VARCHAR, Types.DATE, Types.DATE, Types.DATE,
                        Types.VARCHAR, Types.BIGINT, Types.INTEGER, Types.VARCHAR, Types.DATE, Types.INTEGER,
                        Types.BIGINT, Types.NUMERIC, Types.BOOLEAN, Types.NUMERIC, Types.VARCHAR});
        log.info("loan_contracts {}건 시드", rows.size());
    }

    private boolean isEmpty(String table) {
        Boolean exists = jdbc.queryForObject("select exists (select 1 from " + table + ")", Boolean.class);
        return !Boolean.TRUE.equals(exists);
    }

    private <T> List<T> read(String file, TypeReference<List<T>> type) throws IOException {
        return json.readValue(dir.resolve(file).toFile(), type);
    }

    private String toJson(Object value) {
        try {
            return json.writeValueAsString(value);
        } catch (IOException e) {
            throw new IllegalStateException(e);
        }
    }
}
