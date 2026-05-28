package com.coremasterkb.serving.infrastructure;

import java.util.*;

/**
 * Client for text embedding via llm_service.
 *
 * <p>Model and dimensions are managed by llm_service — this client only sends
 * the text input and relies on llm_service defaults.
 */
public class EmbeddingClient {

    private final LlmClient llmClient;

    public EmbeddingClient(LlmClient llmClient) {
        this.llmClient = llmClient;
    }

    public boolean isConfigured() {
        return llmClient.isAvailable();
    }

    @SuppressWarnings("unchecked")
    public float[] embed(String text) {
        Map<String, Object> response = llmClient.embed(List.of(text));
        List<Map<String, Object>> data = (List<Map<String, Object>>) response.get("data");
        if (data != null && !data.isEmpty()) {
            List<Number> embedding = (List<Number>) data.get(0).get("embedding");
            if (embedding != null) {
                float[] result = new float[embedding.size()];
                for (int i = 0; i < embedding.size(); i++) {
                    result[i] = embedding.get(i).floatValue();
                }
                return result;
            }
        }
        return null;
    }
}
