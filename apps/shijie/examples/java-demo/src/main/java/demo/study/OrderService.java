package demo.study;

import java.util.List;

public interface OrderService {
    List<OrderSummary> listMyOrders(Long currentUserId);
}
