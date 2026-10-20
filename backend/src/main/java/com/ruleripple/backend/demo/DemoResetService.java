package com.ruleripple.backend.demo;

import com.ruleripple.backend.internal.BusinessDataLoader;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.io.IOException;

/** 시연 리셋: 작업 기록을 지우고 업무 데이터를 JSON 시드로 되돌린다. 계정은 남긴다. */
@Service
public class DemoResetService {

    private static final Logger log = LoggerFactory.getLogger(DemoResetService.class);

    public record Result(int tasks, int jobs, int workTickets, int products, int productRules, int loanContracts) {}

    private final JdbcTemplate jdbc;
    private final BusinessDataLoader loader;

    public DemoResetService(JdbcTemplate jdbc, BusinessDataLoader loader) {
        this.jdbc = jdbc;
        this.loader = loader;
    }

    @Transactional(rollbackFor = Exception.class)
    public Result reset() throws IOException {
        // jobs · work_tickets 는 approvals 를 cascade 없이 참조해서 작업보다 먼저 지운다.
        // 큐에 남은 메시지는 JobWorker 가 작업이 없으면 버린다.
        int jobs = jdbc.update("delete from jobs");
        int tickets = jdbc.update("delete from work_tickets");
        int tasks = jdbc.update("delete from analysis_tasks");
        loader.reseed();
        Result result = new Result(tasks, jobs, tickets, count("products"), count("product_rules"), count("loan_contracts"));
        log.info("시연 리셋 {}", result);
        return result;
    }

    private int count(String table) {
        Integer n = jdbc.queryForObject("select count(*) from " + table, Integer.class);
        return n == null ? 0 : n;
    }
}
