package demo.study;

import java.util.List;

public final class OrderTools {
    private final CurrentUserContext currentUserContext;
    private final OrderService orderService;

    public OrderTools(CurrentUserContext currentUserContext, OrderService orderService) {
        this.currentUserContext = currentUserContext;
        this.orderService = orderService;
    }

    public ToolResult<List<OrderSummary>> listMyOrders() {
        Long currentUserId = currentUserContext.getUserId();
        if (currentUserId == null) {
            return ToolResult.failure("UNAUTHORIZED", "请先登录");
        }
        List<OrderSummary> orders = orderService.listMyOrders(currentUserId);
        return ToolResult.success(orders);
    }
}
