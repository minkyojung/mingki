package com.ruleripple.backend.config;

import org.springframework.boot.context.properties.ConfigurationProperties;

@ConfigurationProperties(prefix = "ruleripple")
public record RuleRippleProperties(Jwt jwt, Internal internal, Agent agent, Queue queue,
                                  BusinessData businessData, Demo demo) {

    public record Jwt(String secret, int expiresMinutes) {}

    public record Internal(String token, String callbackBaseUrl) {}

    public record Agent(String baseUrl, int connectTimeoutMs, int readTimeoutMs) {}

    public record Queue(String exchange, String mainQueue, String retryQueue, String deadLetterQueue,
                        int retryDelayMs, int maxAttempts, double failureRate) {}

    /** 샘플 업무 시드 JSON 폴더 (data/business). */
    public record BusinessData(String dir) {}

    /** 시연 리셋 API 허용 여부. 운영에서는 끈다. */
    public record Demo(boolean resetEnabled) {}
}
