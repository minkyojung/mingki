package com.ruleripple.backend.demo;

import com.ruleripple.backend.config.RuleRippleProperties;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.server.ResponseStatusException;

import java.io.IOException;

/** 화면의 "데모 초기화". 로그인한 사용자 누구나. DEMO_RESET_ENABLED=false 면 404. */
@RestController
@RequestMapping("/api/demo")
public class DemoController {

    private final DemoResetService reset;
    private final boolean enabled;

    public DemoController(DemoResetService reset, RuleRippleProperties props) {
        this.reset = reset;
        this.enabled = props.demo() != null && props.demo().resetEnabled();
    }

    @PostMapping("/reset")
    public DemoResetService.Result reset() throws IOException {
        if (!enabled) throw new ResponseStatusException(HttpStatus.NOT_FOUND, "시연 리셋이 꺼져 있습니다");
        return reset.reset();
    }
}
