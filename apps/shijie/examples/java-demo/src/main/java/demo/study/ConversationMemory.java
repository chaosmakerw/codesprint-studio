package demo.study;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

public final class ConversationMemory {
    private final Map<String, List<String>> conversations = new HashMap<>();

    public void append(String key, String message) {
        conversations.computeIfAbsent(key, ignored -> new ArrayList<>()).add(message);
    }

    public List<String> load(String key) {
        return conversations.getOrDefault(key, List.of());
    }
}
