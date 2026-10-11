package demo.study;

import java.util.Map;

public final class ToolDispatcher {
    private final ToolArgumentValidator validator;
    private final OrderTools orderTools;

    public ToolDispatcher(ToolArgumentValidator validator, OrderTools orderTools) {
        this.validator = validator;
        this.orderTools = orderTools;
    }

    public ToolResult<?> dispatch(String toolName, Map<String, Object> arguments) {
        try {
            ToolResult<Void> validation = validator.validate(toolName, arguments);
            if (!validation.success()) { return validation; }
            if ("list_my_orders".equals(toolName)) { return orderTools.listMyOrders(); }
            return ToolResult.failure("UNKNOWN_TOOL", "工具未登记");
        } catch (RuntimeException failure) {
            return ToolResult.failure("INTERNAL_ERROR", "工具执行失败");
        }
    }
}
