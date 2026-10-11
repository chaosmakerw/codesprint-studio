package demo.study;

import java.util.Map;

public final class ToolArgumentValidator {
    public ToolResult<Void> validate(String toolName, Map<String, Object> arguments) {
        if ("list_my_orders".equals(toolName)) {
            if (arguments == null) {
                return ToolResult.failure("MISSING_ARGUMENT", "请传入参数对象");
            }
            if (!arguments.isEmpty()) {
                return ToolResult.failure("UNKNOWN_ARGUMENT", "此工具不接受任何参数");
            }
            return ToolResult.success(null);
        }
        return ToolResult.failure("UNKNOWN_TOOL", "工具未登记");
    }
}
