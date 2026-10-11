package demo.study;

import java.util.ArrayList;
import java.util.List;

public final class FakeOrderService implements OrderService {
    private final List<OrderSummary> orders = List.of(
        new OrderSummary(1L, 1001L, "接口练习册", "PAID"),
        new OrderSummary(2L, 1002L, "集合练习册", "CREATED")
    );

    @Override
    public List<OrderSummary> listMyOrders(Long currentUserId) {
        if (currentUserId == null) throw new IllegalArgumentException("身份不能为空");
        List<OrderSummary> result = new ArrayList<>();
        for (OrderSummary order : orders) {
            if (currentUserId.equals(order.buyerId())) { result.add(order); }
        }
        return result;
    }
}
