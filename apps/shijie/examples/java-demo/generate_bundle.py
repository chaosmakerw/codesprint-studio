"""Build original source-backed teaching data without running Java or a project script.

All frames below are static predictions under the explicit manual wiring premise.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent
JAVA = "src/main/java/demo/study/"


def build_bundle() -> dict:
    paths = ["README.md", "pom.xml"] + [p.relative_to(ROOT).as_posix() for p in sorted((ROOT / "src").rglob("*.java"))]
    files = [{"path": p, "text": (ROOT / p).read_text(encoding="utf-8")} for p in paths]
    sources = {f["path"]: f["text"] for f in files}

    def anchor(filename, needle):
        path = filename if "/" in filename or filename == "README.md" else JAVA + filename
        matches = [(n, line) for n, line in enumerate(sources[path].splitlines(), 1) if needle in line]
        if len(matches) != 1:
            raise ValueError(f"Expected one actual line for {path}: {needle!r}")
        n, quote = matches[0]
        return {"path": path, "line": n, "quote": quote,
                "sha256": hashlib.sha256(sources[path].encode("utf-8")).hexdigest()}

    declarations = [
        ("java-types", "这一条订单链的类型怎样变化", "Java 基础", [],
         "Map 的键是 String，值可以是 Object；校验结果是 ToolResult<Void>，订单成功结果是 ToolResult<List<OrderSummary>>。Long 是引用类型，record 的访问方法读取字段。",
         "原始空 Map 进入 dispatch；validate 返回成功且 data=null 的 ToolResult<Void>；Tools 取 Long 身份；Service 返回 List<OrderSummary>；Tools 把列表放入带泛型的 ToolResult。",
         "接口边界用类型表达各阶段职责；无参数工具无需数值转换。ToolResult<?> 允许分发器返回不同数据类型，不能靠通配符直接认定内部数据就是订单列表。",
         ["把 Void 的 null 数据当作校验失败", "认为 Object 允许模型设置可信身份", "用 == 比较两个 Long 的身份", "认为 record 会深度冻结它持有的所有对象"],
         "先看一次具体请求：toolName 是 String，值为 list_my_orders；arguments 的声明类型是 Map<String,Object>，值为空 Map。尖括号里的 String 和 Object 是泛型类型参数，它们约束键和值的使用，Object 并不代表值可信。工具禁止任何参数，所以这一次 Map 只提供空参数对象。\n\nvalidate 的返回类型是 ToolResult<Void>。Void 在这里表达没有业务数据，success(null) 得到 success=true、code=OK、message=空串、data=null；判断是否通过看 success()，不能看 data 是否为空。record ToolResult<T> 声明了这四个组成部分，Java 为它生成 success()、code()、message()、data() 访问方法。T 是调用时确定的数据类型；同名静态工厂 success(data) 根据传入数据创建新结果。\n\nOrderTools 声明 currentUserId 为 Long，假设可信上下文返回 1001L。L 表示 long 字面量，赋给 Long 时装箱。接口调用返回 List<OrderSummary>，它表示订单记录列表；order.buyerId() 返回记录中的 Long。currentUserId.equals(order.buyerId()) 比较数值，1001L 与1001L相等，1001L 与1002L不等。Long 的 == 比较引用，不能把小数值缓存现象当作权限设计。\n\nTools 再把列表装进 ToolResult<List<OrderSummary>>。dispatch 声明返回 ToolResult<?>，问号是未知类型参数：这层只统一结果外形，并没有证明 data 的具体类型。一个业务成功但列表为空仍是 success=true；未登录失败则 success=false、data=null。\n\n请手写四行类型跟踪：arguments、validation、currentUserId、orders；再解释两个 data=null 为什么可能代表不同情况。验收要求说清声明类型、具体值、来源和 success 字段。这里只依据源码预测，没有编译Java或运行真实模型；看懂泛型还不等于能独立复现。",
         [anchor("ToolDispatcher.java", "public ToolResult<?> dispatch"), anchor("ToolResult.java", "public record ToolResult<T>"), anchor("FakeOrderService.java", "currentUserId.equals(order.buyerId())")]),
        ("control-flow", "在哪一行继续，在哪一行立即停止", "Java 基础", ["java-types"],
         "if 判断决定分支；return 结束当前方法并把结果交还调用者；throw 沿调用栈传播到匹配的 catch。",
         "Validator 先拒绝 null 或非空参数；Dispatcher 先检查 validation.success；Tools 先拒绝 null 身份；其余请求才进入 Fake 的 for 循环。",
         "把每个拒绝点安排在相应下游之前，能避免非法请求进入服务；Dispatcher 为意外 RuntimeException 提供稳定外部消息。",
         ["认为 return 只结束 if 块", "在 Validator 已失败后仍画出 Service 调用", "把成功空列表当异常", "以为 catch 会让之前失败的方法继续执行"],
         "这条链有两种顺序：方法调用的进入与返回，以及每个方法内部的语句顺序。dispatch 进入 try 后，先调用 validate；validate 返回之前，dispatch 的下一行不会执行。合法空 Map 得到成功结果，随后 !validation.success() 是 false，才有机会调用 orderTools.listMyOrders()。\n\n把 arguments 换成 {buyerId:1002L}。Validator 的 arguments.isEmpty() 为 false，所以 !arguments.isEmpty() 为 true，它立即返回 UNKNOWN_ARGUMENT。return 结束 validate；返回到 dispatch 后，!validation.success() 成立，又立即 return validation。这里 Tools、身份上下文和Service都没有被调用。拒绝结果是预期业务行为，不是Java异常。\n\n把参数保持空 Map，只把可信上下文返回值换成 null。Tools 在 currentUserId==null 分支返回 UNAUTHORIZED。这次 Validator 与 Tools确实执行过，Service还没有被调用。返回结果沿原调用者交还，不能在手算中给 orders 一个从未产生的值。\n\n若已登录而注入的服务抛 RuntimeException，赋给 orders 的语句没有正常完成，Tools的success(orders)不执行。异常传到dispatch的catch，转换成INTERNAL_ERROR和固定消息。这里只声明RuntimeException边界，并没有超时、重试或日志根因记录。\n\n正常Fake实现用for遍历两条订单，if只把符合身份的记录add到result。for完成后return result；空列表也是正常返回。请预测未知工具、null参数、伪造字段、未登录、合法用户五种停止位置，再各写哪些层没有调用。验收看真实执行顺序和停止点；本例没有测试执行证据，不能声称线上异常已修复。",
         [anchor("ToolDispatcher.java", "if (!validation.success())"), anchor("ToolArgumentValidator.java", "if (!arguments.isEmpty())"), anchor("OrderTools.java", "if (currentUserId == null)")]),
        ("interfaces-di", "为什么声明接口，却执行具体实现", "后端工程", ["java-types"],
         "接口声明可调用的方法，构造器把具体对象传给使用它的类。依赖的声明类型和运行时对象类型可以不同。",
         "教学前提是手动把服务器身份上下文与 new FakeOrderService() 传给OrderTools，再传给ToolDispatcher；orderService字段声明为OrderService，运行时执行FakeOrderService。",
         "Tools依赖业务接口，替换教学Fake和失败Stub时不必改Tools；本例手动注入即可，不需要Spring容器。",
         ["认为接口会自己生成实例", "以为有构造器注入就已启动Spring", "把Fake接口实现当真实数据库", "未检查实现绑定就猜运行时返回"],
         "读OrderService.java时只有方法声明：List<OrderSummary> listMyOrders(Long currentUserId)。它规定调用者能向服务请求什么，没有订单数据，也不会自己new实现。FakeOrderService implements OrderService 表示它承诺实现这个方法。\n\nOrderTools的构造器接收CurrentUserContext与OrderService，把参数保存到this.currentUserContext和this.orderService。这里this指正在构造的Tools对象，右边同名参数是调用者传进来的对象引用。private final表示字段赋值后不能改指向，不保证被引用对象里的数据自动线程安全。\n\n本流程的明确前提是教学调用者手动组合：Validator对象；一个返回1001L的可信身份上下文；FakeOrderService对象；接收这两个依赖的OrderTools；最后接收Validator和Tools的Dispatcher。仓库没有main、Spring配置或身份接口实现，因而这些组合是解释性前提，源码不证明框架实际替我们装配过。\n\n执行orderService.listMyOrders(currentUserId)时，字段声明类型是OrderService，实际对象类型是假设注入的FakeOrderService；Java按运行时对象执行其覆盖方法。如果换成抛RuntimeException的Stub，仍满足接口，结果则走Dispatcher异常分支。Stub场景明确是另一个依赖前提，不能假称Fake源代码必然会抛异常。\n\n请先口述“谁把什么对象传给谁”，再在自己的练习区手写一个只实现OrderService的小Stub，说明它怎样触发catch。验收要求区分接口、实例、注入与调用，并指出这里没有Spring、HTTP、数据库或真实认证；静态组合练习还未证明独立实现或真实接入。",
         [anchor("OrderService.java", "List<OrderSummary> listMyOrders"), anchor("OrderTools.java", "this.orderService = orderService;"), anchor("FakeOrderService.java", "implements OrderService")]),
        ("identity-permission", "模型选功能，服务器决定查谁的订单", "安全边界", ["control-flow", "interfaces-di"],
         "可信身份与查询对象范围是两道约束。工具身份来自CurrentUserContext，Fake以buyerId归属过滤；模型不能用参数指定别人的身份。",
         "空参数校验后读取1001L；传给Service；仅buyerId=1001L的订单1进入result。伪造buyerId字段在读取身份之前就被拒绝。",
         "让list_my_orders始终表示本人订单；上下文提供isAdmin也不会自动把本人功能变成查看全部。",
         ["把参数白名单当真实登录认证", "让模型参数覆盖currentUserId", "看到isAdmin方法就猜工具有管理员豁免", "认为没有订单表示无权限错误"],
         "用户说“查看我的订单”，模型能选择list_my_orders工具名，但不能决定“我”是谁。本例工具不接收任何参数；Validator拒绝非空Map，这使{buyerId:1002L}、{userId:1002L}与{role:ADMIN}都停在UNKNOWN_ARGUMENT。字段名不是可信身份凭据。\n\n通过参数校验以后，Tools直接调用currentUserContext.getUserId()。这是服务器上下文接口；本教学案例明确假设注入的可信对象返回1001L。仓库只定义接口，并未实现登录、JWT校验或HTTP认证，所以我们能讲身份来源边界，不能宣称真实认证已运行。身份null在Tools立即UNAUTHORIZED，下游Service不执行。\n\n服务器身份1001L通过检查以后，Tools只把它交给OrderService。Fake固定保存订单1（买家1001L）和订单2（买家1002L），逐条比较currentUserId.equals(order.buyerId())。第一条相等，加入result；第二条不相等，跳过。Service还必须执行归属筛选，参数白名单本身并不能替代对象权限。\n\n把可信身份改成1002L就只返回订单2；改成9000L就成功返回空列表。CurrentUserContext还声明isAdmin()，但这条源码从未调用它。即使教学上下文的isAdmin返回true，list_my_orders仍只按当前ID过滤；接口多一个方法不代表这个功能使用了它。\n\n请先预测1001L+{}、1001L+{buyerId:1002L}、null+{}三组结果，并说明是否读取身份、是否调用Service。再只改变身份到1002L与9000L，对比合法结果。验收需要正常成功与安全拒绝两侧，解释同一条边界；本Fake没有数据库、生产认证、真实模型选择或实际运行证据，不能据此宣布生产权限验收。",
         [anchor("OrderTools.java", "currentUserContext.getUserId()"), anchor("FakeOrderService.java", "currentUserId.equals(order.buyerId())"), anchor("ToolArgumentValidator.java", "if (!arguments.isEmpty())")]),
        ("collections-state", "列表里的订单，和返回列表的所有权", "Java 基础", ["java-types"],
         "集合保存引用。List.of建立固定订单列表，new ArrayList每次建立筛选结果；record记录的订单字段不可通过setter修改，但返回ArrayList仍可变。",
         "Fake遍历固定orders，把符合归属的OrderSummary引用add到新result；Tools把同一个结果列表放进ToolResult，没有再复制。",
         "筛选结果容器与固定源列表分开，调用者clear结果不会清空Fake源列表；可变结果列表仍需要调用者约束。",
         ["认为List接口必然不可变", "认为record会让包含它的列表也不可变", "把add(order)当深拷贝", "认为ConversationMemory的HashMap保证并发或容量"],
         "Fake的orders字段用List.of创建两条固定OrderSummary。这个列表不支持add、remove等结构修改，但每个元素是对象引用。OrderSummary是record，提供orderId()、buyerId()等访问方法，没有setter；它的Long与String字段也是不可变值，本例不包含嵌套可变字段。\n\n每次调用listMyOrders都执行new ArrayList<>()，所以result是新容器。for(OrderSummary order : orders)每轮令局部order引用一条源记录；add(order)把这个引用放入result，不会复制订单对象。1001L这次result只装订单1，下一次1002L的result是另一容器，只装订单2。\n\nreturn result把此容器交给Tools，ToolResult.success(orders)又把同一个引用存入data；这里没有List.copyOf。如果调用者clear返回的列表，这次结果会变空，但Fake的源List.of列表保持两条。不能因此说整个ToolResult都深度不可变；record限制字段重新赋值，data所引用的ArrayList仍能变。\n\n还有一个独立会话小例：ConversationMemory.append通过computeIfAbsent取得或新建列表，接着add消息；load在键存在时直接返回内部列表，所以外部可修改会话。它与订单筛选链没有调用关系，不能把会话当作订单链节点；普通HashMap也没有容量、TTL、关闭或线程安全保证。\n\n请画三个容器：Fake源orders、当次result、ConversationMemory里的消息列表。用箭头标元素是否共享，再解释修改返回列表会影响哪一个容器。验收以引用和状态变化为准，若新增嵌套可变字段要重查拷贝边界。这里只静态阅读，未证明并发、持久化、生产会话或独立复现。",
         [anchor("FakeOrderService.java", "List.of("), anchor("FakeOrderService.java", "new ArrayList<>()"), anchor("ConversationMemory.java", "return conversations.getOrDefault")]),
        ("tool-calling", "这段分发器到底接入到哪一步", "Java 与 Agent", ["identity-permission", "java-types"],
         "Dispatcher接收工具名和原始Map，先校验，再进入登记工具，并统一RuntimeException结果。这是Java分发链，不代表真实模型已选择工具。",
         "dispatch→validate；不成功提前返回；名称list_my_orders才调用Tools；Tools→上下文→Service→Fake→ToolResult→dispatch调用者。",
         "工具名和参数都在入口控制，避免模型直接绕过边界进入业务；无业务参数的本工具不需要Converter。",
         ["强行插入不存在的Converter", "方法存在就认为模型已注册Schema", "稳定INTERNAL_ERROR就认为内部故障修复了", "把内存链称完整生产端到端"],
         "先把功能说成一句人话：一个已登录用户查看自己的固定示例订单。入口不是Controller，而是ToolDispatcher.dispatch(String,Map<String,Object>)这个普通Java方法。调用者必须已经持有Dispatcher对象并显式调用；示例仓库没有HTTP入口、模型协议注册或Tool Calling循环。\n\nDispatcher先直接调用validator.validate。Validator根据工具名决定规则：list_my_orders要求Map非null且为空；其它名字返回UNKNOWN_TOOL。Dispatcher拿到ToolResult<Void>后先看success()；拒绝时原样返回，成功时才调用orderTools.listMyOrders()。Tools读可信身份、调用业务接口、把列表包装成ToolResult成功。\n\n这条链没有ToolArgumentConverter。原因是此工具没有合法业务参数需要从Object转换；currentUserId从上下文取得，并非从Map转换。以后有其他工具要取整数参数时，才应根据那条实际源码讨论校验和安全转换，不能沿旧项目印象强插节点。\n\n若业务依赖抛RuntimeException，Dispatcher返回success=false、code=INTERNAL_ERROR、message=工具执行失败、data=null。这个稳定外形能避免把异常内容直接给调用者；当前源码没有日志根因、重试、超时或观测指标，固定错误不说明根因已解决。\n\n请脱离源码口述完整进入与返回顺序，并对每个边界说清输入、输出、为何先做。然后说明“Java方法存在”“分发器可路由”“Schema暴露”“真实模型选择”“结果回传”“最终回答”有哪些在本例有证据。验收要求明确只到Java分发层；源码预测还未证明运行、真实协议、Spring、数据库或生产端到端。",
         [anchor("ToolDispatcher.java", "validator.validate(toolName, arguments)"), anchor("ToolDispatcher.java", "return orderTools.listMyOrders()"), anchor("ToolDispatcher.java", "catch (RuntimeException failure)")]),
    ]
    concepts = [{"id": cid, "title": title, "group": group, "prerequisites": prereqs,
                 "explanation": explanation, "mechanism": mechanism, "why": why, "failures": failures,
                 "lesson": lesson, "evidence": evidence, "references": []}
                for cid, title, group, prereqs, explanation, mechanism, why, failures, lesson, evidence in declarations]

    def step(sid, title, actor, action, incoming, output, why, cids, filename, needle):
        return {"id": sid, "title": title, "actor": actor, "action": action, "input": incoming,
                "output": output, "why": why, "concept_ids": cids, "evidence": [anchor(filename, needle)]}

    steps = [
        step("entry", "接到工具请求，先去问校验器", "分发器：ToolDispatcher.dispatch",
             "直接调用validator.validate(toolName, arguments)，等待依赖正常返回后才进入下一行。",
             'toolName="list_my_orders"，String；arguments={}，Map<String,Object>', '把两个原始参数传给Validator，尚未得到validation',
             "原始模型参数先过白名单与参数边界。", ["java-types", "interfaces-di", "tool-calling"], "ToolDispatcher.java", "validator.validate(toolName, arguments)"),
        step("validate", "工具登记了，参数也必须确实为空", "校验器：ToolArgumentValidator.validate",
             "按真实源码先判工具名，再判arguments==null，再判非空；合法空Map返回ToolResult.success(null)。",
             'list_my_orders 与非null空Map', '校验结果ToolResult<Void>：success=true、code=OK、message=""、data=null',
             "禁止从参数指定买家；无参数也要传一个空对象，null与空Map不同。", ["java-types", "control-flow", "identity-permission"], "ToolArgumentValidator.java", "if (!arguments.isEmpty())"),
        step("route", "校验通过，才把请求交给订单工具", "分发器：ToolDispatcher.dispatch",
             "依赖validate返回后绑定validation。先检查!validation.success()，失败立即return；通过后直接调用orderTools.listMyOrders()。",
             'validation为成功ToolResult<Void>；toolName为list_my_orders', '进入OrderTools；此工具没有业务参数，不调用Converter',
             "失败后不调用下游；按实际功能决定是否转换参数。", ["control-flow", "tool-calling"], "ToolDispatcher.java", 'if ("list_my_orders".equals(toolName))'),
        step("identity", "从服务器取得“我”的身份", "订单工具：OrderTools.listMyOrders → CurrentUserContext.getUserId",
             "直接调用身份接口；依赖返回后赋给Long currentUserId，null立即返回UNAUTHORIZED。这里的接口实现由教学前提指定，仓库未包含真实认证实现。",
             "空参数已通过校验；注入的可信上下文在本例返回1001L", "currentUserId=1001L，Long；继续，isAdmin未被调用",
             "身份来自服务器上下文；模型没有决定查谁的入口。", ["identity-permission", "interfaces-di", "control-flow"], "OrderTools.java", "currentUserContext.getUserId()"),
        step("service", "把这一个身份交给业务服务", "订单工具：OrderTools.listMyOrders → OrderService.listMyOrders",
             "直接调用接口方法；本流程明确假设注入new FakeOrderService()，因此Java运行时执行该具体实现。等待列表或异常返回。",
             "currentUserId=1001L；字段orderService声明为OrderService，运行时对象为FakeOrderService", "参数Long 1001L进入Fake，orders局部尚未绑定返回值",
             "工具负责入口与身份，服务负责组织归属筛选；接口允许换实现。", ["interfaces-di", "identity-permission"], "OrderTools.java", "orderService.listMyOrders(currentUserId)"),
        step("filter", "逐条只保留买家属于当前用户的订单", "教学服务：FakeOrderService.listMyOrders",
             "再次拒绝null；new ArrayList创建结果。for遍历固定订单，用Long.equals比较buyerId，相等才add；最后return result。",
             "currentUserId=1001L；源订单1买家1001L，订单2买家1002L", 'result为新ArrayList<OrderSummary>，只含OrderSummary(1L,1001L,"接口练习册","PAID")',
             "合法身份仍要约束对象归属；返回容器与源List.of分开，但元素引用共享。", ["java-types", "collections-state", "identity-permission", "control-flow"], "FakeOrderService.java", "currentUserId.equals(order.buyerId())"),
        step("wrap", "把订单列表放进稳定成功结果", "订单工具：OrderTools.listMyOrders → ToolResult.success",
             "Service正常返回后绑定List<OrderSummary> orders，直接调用静态工厂并返回ToolResult；没有再复制列表。",
             "orders是Fake返回的同一个结果列表", 'ToolResult<List<OrderSummary>>：success=true、code=OK、message=""、data=orders',
             "统一结果外形，空列表也属于合法业务成功。", ["java-types", "collections-state", "tool-calling"], "OrderTools.java", "return ToolResult.success(orders)"),
        step("return", "结果返回到调用者，意外异常在这里收口", "分发器：ToolDispatcher.dispatch",
             "orderTools正常返回时直接return同一结果；如果下游抛RuntimeException，catch转换成固定失败。此处是依赖返回边界。",
             "Tools返回成功或业务失败；或出现RuntimeException", "正常原样返回；异常时success=false、INTERNAL_ERROR、工具执行失败、data=null",
             "调用者看到一致外形；固定消息不等于已诊断根因，本例无日志或重试。", ["tool-calling", "control-flow"], "ToolDispatcher.java", "catch (RuntimeException failure)"),
    ]
    steps[1]["evidence"] += [anchor("ToolArgumentValidator.java", "if (arguments == null)"),
                              anchor("ToolArgumentValidator.java", 'return ToolResult.failure("UNKNOWN_TOOL"')]
    steps[2]["evidence"] += [anchor("ToolDispatcher.java", "if (!validation.success())")]
    steps[3]["evidence"] += [anchor("OrderTools.java", "if (currentUserId == null)")]
    steps[5]["evidence"] += [anchor("FakeOrderService.java", 'new OrderSummary(1L, 1001L'),
                              anchor("FakeOrderService.java", 'new OrderSummary(2L, 1002L'),
                              anchor("FakeOrderService.java", "return result;")]
    steps[7]["evidence"] += [anchor("ToolDispatcher.java", "return orderTools.listMyOrders()"),
                              anchor("ToolDispatcher.java", 'return ToolResult.failure("INTERNAL_ERROR"')]

    def frame(sid, explanation, *values):
        return {"step_id": sid, "explanation": explanation,
                "values": [{"name": n, "type": t, "value": v, "source": s} for n, t, v, s in values]}

    ok_validation = ("validation", "ToolResult<Void>", 'success=true, code="OK", message="", data=null', "validate正常返回")
    entry_frame = frame("entry", "调用者显式调用Java方法，尚未调用模型或HTTP。",
                        ("toolName", "String", '"list_my_orders"', "调用者参数"),
                        ("arguments", "Map<String, Object>", "{}", "调用者提供的非null空Map"))

    def success_case(uid, data, title, admin=False):
        value = str(uid) + "L"
        return {"id": f"user-{uid}", "title": title,
                "input": f"手动注入可信上下文：getUserId返回{value}、isAdmin返回{str(admin).lower()}；Service是Fake；dispatch(list_my_orders,空Map)。",
                "result": f"静态预测：success=true、code=OK、data={data}。isAdmin从未被调用；不是实际运行记录。", "frames": [
                    entry_frame,
                    frame("validate", "非null且为空，返回成功校验对象。", ("arguments.isEmpty()", "boolean", "true", "Map查询")),
                    frame("route", "!validation.success()为false，进入登记工具。", ok_validation),
                    frame("identity", "可信依赖返回Long，非null继续；不读取模型身份字段。", ("currentUserId", "Long", value, "CurrentUserContext.getUserId返回")),
                    frame("service", "通过接口把同一身份传给已假设注入的Fake。", ("currentUserId", "Long", value, "Tools局部参数"), ("orderService", "OrderService", "FakeOrderService实例", "教学手动注入前提")),
                    frame("filter", "依次比较订单1的1001L、订单2的1002L；仅值相等者加入新列表。", ("currentUserId", "Long", value, "方法参数"), ("result", "List<OrderSummary>（运行时ArrayList）", data, "Fake筛选返回")),
                    frame("wrap", "服务正常返回后才产生orders局部；传入成功工厂。", ("orders", "List<OrderSummary>", data, "OrderService返回，与result同一容器")),
                    frame("return", "直接交还同一个稳定结果。", ("return", "ToolResult<?>（data为List<OrderSummary>）", f'success=true, code="OK", message="", data={data}', "OrderTools返回")),
                ]}

    cases = [success_case(1001, '[OrderSummary(1L,1001L,"接口练习册","PAID")]', "用户1001仅看见自己的订单"),
             success_case(1002, '[OrderSummary(2L,1002L,"集合练习册","CREATED")]', "换可信用户1002，返回另一条"),
             success_case(9000, "[]", "管理员身份也只按本人ID过滤", admin=True)]
    cases += [
        {"id": "forged-buyer", "title": "模型伪造买家ID在参数校验处停止", "input": '可信上下文若被读取会返回1001L；dispatch("list_my_orders", Map.of("buyerId",1002L))。',
         "result": "静态预测：UNKNOWN_ARGUMENT、success=false、data=null；身份、Tools、Service均未调用。", "frames": [
             frame("entry", "保留原始模型Map，不把其中ID当服务器身份。", ("arguments", "Map<String, Object>", '{"buyerId":1002L}', "模型参数")),
             frame("validate", "非空条件为true，直接返回UNKNOWN_ARGUMENT。", ("arguments.isEmpty()", "boolean", "false", "Map查询")),
             frame("route", "!success成立，Dispatcher立即return validation。", ("validation", "ToolResult<Void>", 'success=false, code="UNKNOWN_ARGUMENT", message="此工具不接受任何参数", data=null', "Validator拒绝")),
         ]},
        {"id": "missing-map", "title": "null并不等于空参数对象", "input": 'dispatch("list_my_orders", null)，上下文不应被读取。',
         "result": "静态预测：MISSING_ARGUMENT；Tools与Service未调用。", "frames": [
             frame("entry", "仍进入校验器。", ("arguments", "Map<String, Object>", "null", "调用者参数")),
             frame("validate", "arguments==null先成立，未执行isEmpty。", ("return", "ToolResult<Void>", 'success=false, code="MISSING_ARGUMENT", message="请传入参数对象", data=null', "Validator返回")),
             frame("route", "校验失败原样返回。", ("validation.success()", "boolean", "false", "结果访问方法")),
         ]},
        {"id": "logged-out", "title": "未登录允许进入Tools，随后拒绝Service", "input": '工具名list_my_orders、空Map；注入上下文getUserId返回null；Fake不应被调用。',
         "result": "静态预测：UNAUTHORIZED；Service、筛选、成功包装均未执行。", "frames": [
             entry_frame, frame("validate", "参数合法。", ("arguments.isEmpty()", "boolean", "true", "Map查询")),
             frame("route", "参数校验通过，才调用Tools。", ok_validation),
             frame("identity", "读取null后立即返回，不产生orders。", ("currentUserId", "Long", "null", "可信上下文返回")),
             frame("return", "Dispatcher收到Tools业务失败，原样返回。", ("return", "ToolResult<?>（data=null）", 'success=false, code="UNAUTHORIZED", message="请先登录", data=null', "Tools提前返回")),
         ]},
        {"id": "dependency-fails", "title": "显式失败Stub的异常收口", "input": '合法空Map、可信ID1001L；改为注入一个listMyOrders抛RuntimeException的OrderService Stub。这是假设依赖，不是现有Fake行为。',
         "result": "静态预测：INTERNAL_ERROR、固定消息、data=null；没有Fake筛选或Tools成功包装。", "frames": [
             entry_frame, frame("validate", "合法空Map通过。", ("arguments.isEmpty()", "boolean", "true", "Map查询")),
             frame("route", "继续Tools。", ok_validation),
             frame("identity", "非null可信ID。", ("currentUserId", "Long", "1001L", "上下文返回")),
             frame("service", "此次注入的是失败Stub，调用抛异常，orders未绑定。", ("orderService", "OrderService", "抛RuntimeException的Stub实例", "本案例明确假设")),
             frame("return", "异常到Dispatcher的catch，创建固定失败。", ("failure", "RuntimeException", "Stub抛出的异常对象（内容不对外输出）", "下游抛出"), ("return", "ToolResult<?>（data=null）", 'success=false, code="INTERNAL_ERROR", message="工具执行失败", data=null', "catch返回")),
         ]},
    ]
    flows = [{"id": "my-orders", "title": "查看本人订单", "summary": "从一次功能请求进入真实Java分发链，最后回到调用者。依赖手动组合是明确教学前提，帧为静态预测；没有Converter、Spring、HTTP、数据库或真实模型调用。",
              "scenario": "已登录用户想查看自己的订单；模型或调用者只选功能，服务器上下文决定查询身份。",
              "input": "手动给Dispatcher注入Validator与OrderTools；Tools注入可信身份上下文和FakeOrderService。工具名list_my_orders、非null空Map、可信ID1001L。身份接口的真实实现与启动入口未包含在示例中。",
              "expected": "成功只返回订单1；伪造参数先UNKNOWN_ARGUMENT；未登录UNAUTHORIZED且Service不调用；意外RuntimeException转INTERNAL_ERROR。",
              "steps": steps, "branches": [
                  {"from_step_id": "validate", "condition": "arguments==null", "outcome": "MISSING_ARGUMENT，经route原样返回。", "skipped_step_ids": ["identity", "service", "filter", "wrap", "return"]},
                  {"from_step_id": "validate", "condition": "arguments含buyerId/userId/role等任意键", "outcome": "UNKNOWN_ARGUMENT，经route原样返回，身份没有读取。", "skipped_step_ids": ["identity", "service", "filter", "wrap", "return"]},
                  {"from_step_id": "validate", "condition": "toolName不是list_my_orders", "outcome": "UNKNOWN_TOOL，经route原样返回。", "skipped_step_ids": ["identity", "service", "filter", "wrap", "return"]},
                  {"from_step_id": "identity", "condition": "currentUserId==null", "outcome": "Tools提前返回UNAUTHORIZED，分发器正常交还该失败；不调用Service。", "skipped_step_ids": ["service", "filter", "wrap"]},
                  {"from_step_id": "filter", "condition": "当前身份9000L与两条buyerId均不相等", "outcome": "result=[]，仍正常走wrap与return，成功空列表。", "skipped_step_ids": []},
                  {"from_step_id": "service", "condition": "另行注入的OrderService Stub抛RuntimeException", "outcome": "转至return的catch；固定INTERNAL_ERROR，没有成功数据。", "skipped_step_ids": ["filter", "wrap"]},
              ], "trace_cases": cases, "resources": [
                  {"name": "构造器注入的三个依赖对象引用", "ownership": "教学调用者构造并交给Dispatcher/Tools；类字段private final保存引用。本例没有容器生命周期管理。", "lifecycle": "构造时赋值，方法调用期间使用；没有close或真实认证生命周期。CurrentUserContext实现与运行绑定仅为案例明确前提。", "failure": "校验失败时身份和Service均不调用；未登录时Service不调用。依赖对象未被替换或自动销毁。"},
                  {"name": "Fake固定orders与当次result列表", "ownership": "Fake拥有List.of固定源；每次调用新建ArrayList结果容器，元素是原OrderSummary引用；Tools把同一结果列表交给调用者。", "lifecycle": "源列表随Fake实例创建；result每次调用创建，循环add命中项，return后交给调用者；没有数据库或Connection。", "failure": "合法无匹配返回空列表；参数/身份拒绝不遍历源。返回列表可变，修改其结构不修改源List.of；本例没有事务、持久化或重试。"},
              ]}]

    activities = []

    def choice(aid, cid, title, prompt, options, answer, explanation, evidence):
        activities.append({"id": aid, "concept_id": cid, "kind": "predict", "title": title,
                           "prompt": prompt + " 依据当前原创源码预测，未实际运行。",
                           "options": [{"id": letter, "text": label} for letter, label in zip("abcd", options)],
                           "answer_ids": [answer], "rubric": ["结果与停止点准确", "说明输入与依赖前提"],
                           "hints": ["先沿查看本人订单链定位条件。", "先算校验，再算可信身份和归属。", "逐个写条件真假与未调用下游；不要把尚未出现的变量带入。"],
                           "explanation": explanation + " 预测答对不证明独立复现，当前未运行Java。", "evidence": evidence})

    identity_ev = concepts[3]["evidence"]
    choice("order-q01", "identity-permission", "用户1001查看本人", "可信ID1001L、工具list_my_orders、空Map、Fake服务。data是什么？", ["订单1", "订单2", "两条订单", "UNAUTHORIZED"], "a", "A：只有订单1买家等于1001L；B属于1002，C忽略过滤，D忽略非null身份。", identity_ev)
    choice("order-q02", "identity-permission", "可信身份换成1002", "仅可信ID换成1002L，其余合法条件不变。", ["订单1", "订单2", "空列表", "UNKNOWN_ARGUMENT"], "b", "B：接口传1002L，过滤得到订单2；A忽略身份变化，C漏匹配，D无非法参数。", identity_ev)
    choice("order-q03", "identity-permission", "伪造买家参数", "可信ID1001L，参数Map.of(buyerId,1002L)。", ["订单2", "订单1", "UNKNOWN_ARGUMENT，Tools未调用", "UNAUTHORIZED，Service未调用"], "c", "C：非空Map在Validator拒绝；A/B都跳过校验，D把参数拒绝混成登录拒绝。", identity_ev)
    choice("order-q04", "control-flow", "未登录在哪停", "空Map合法，可信上下文getUserId返回null。", ["成功空列表", "Tools返回UNAUTHORIZED，Service未调用", "Fake返回订单1", "Dispatcher拒绝非空Map"], "b", "B：null身份在Tools提前return；A/C虚构下游，D参数实际为空。", concepts[1]["evidence"])
    choice("order-q05", "tool-calling", "空Map与null参数", "参数为null，工具名正确。", ["MISSING_ARGUMENT", "成功查询本人", "UNKNOWN_ARGUMENT", "UNKNOWN_TOOL"], "a", "A：先判null；B跳过校验，C是非空Map规则，D工具名并未改变。", concepts[5]["evidence"])
    choice("order-q06", "tool-calling", "未知工具名", "工具名all_orders，参数为空Map。", ["管理员自动放行", "调用Fake返回全部", "UNAUTHORIZED", "UNKNOWN_TOOL且Tools未调用"], "d", "D：Validator只登记list_my_orders；A/B无注册证据，C不读取身份。", [anchor("ToolArgumentValidator.java", 'return ToolResult.failure("UNKNOWN_TOOL"')])
    choice("order-q07", "identity-permission", "管理员ID无匹配订单", "可信ID9000L、isAdmin若被调用会返回true；合法空Map、Fake。", ["全部订单", "成功空列表", "UNAUTHORIZED", "UNKNOWN_TOOL"], "b", "B：此链根本不调用isAdmin，仍按ID过滤；A虚构豁免，C身份非null，D工具合法。", identity_ev)
    choice("order-q08", "java-types", "成功校验的null数据", "Validator对合法空Map返回success(null)。说明什么？", ["校验失败", "未登录", "success=true，data=null，不携带订单", "所有订单为空"], "c", "C：Void表达无业务数据，判断结果用success字段；A/B/D误把null当业务含义。", concepts[0]["evidence"])
    choice("order-q09", "interfaces-di", "声明与运行时类型", "字段orderService声明OrderService，明确注入FakeOrderService。执行哪个方法？", ["FakeOrderService覆盖方法", "接口自己创建SQL查询", "Spring自动选择真实模型", "不能调用接口方法"], "a", "A：Java动态分派到已注入对象；B接口没有SQL，C本例无Spring或模型，D接口声明了可调用契约。", concepts[2]["evidence"])
    choice("order-q10", "collections-state", "清空当次返回的结果列表", "1001L查询成功后，调用者clear data中列表；下一次新调用同样查询。", ["永久删掉订单1", "Fake源列表变空", "record禁止clear", "下一次仍返回订单1，新结果容器"], "d", "D：每次new ArrayList，源List.of未修改；A/B把容器混同，C record不冻结data列表。", concepts[4]["evidence"])

    open_specs = [
        ("order-trace", "identity-permission", "trace", "按进入和返回顺序手算", "先1001L+空Map，再伪造buyerId，再null身份。为每次列方法、变量类型/值/来源、返回与未调用层。", ["三组结果与顺序正确", "没有虚构Converter", "orders只在服务正常返回后产生", "未登录不调用Service"]),
        ("order-debug", "java-types", "debug", "为什么Long的等号比较危险", "若把equals改为==，解释风险并设计命中引用不同而数值相同的测试草案。原示例保持只读。", ["区分引用相同与数值相同", "不依赖小值缓存", "保留合法匹配与不匹配对照", "测试草案与实测明确区分"]),
        ("order-reproduce", "tool-calling", "reproduce", "独立复现最小本人订单边界", "在自己的练习区脱离源码写校验→可信身份→按买家过滤→稳定结果。提交成功、伪造字段、未登录、依赖异常四组测试与辅助记录。", ["合法成功返回本人订单", "伪造字段拒绝且下游不调用", "未登录不调用Service", "异常固定消息", "实际命令结果与独立性记录"]),
        ("order-transfer", "identity-permission", "transfer", "48小时后设计独立管理工具", "至少48小时后用新输入设计list_all_orders管理员工具。说明可信管理员在哪验证，为什么不能放宽原本人工具参数。提交管理员成功和普通用户拒绝两侧设计与测试草案。", ["原本人边界保留", "管理员由可信上下文判断", "两侧输入与拒绝后未调用下游", "实际间隔与提示记录"]),
    ]
    for aid, cid, kind, title, prompt, rubric in open_specs:
        activity = {"id": aid, "concept_id": cid, "kind": kind, "title": title, "prompt": prompt,
                    "options": [], "answer_ids": [], "rubric": rubric,
                    "hints": ["先讲功能目标与边界。", "沿每个实际方法输入输出建立执行表。", "只给表头：入口｜类型和值｜条件｜调用或停止｜结果；实现与具体断言由你写。"],
                    "explanation": "开放回答逐项人工核对；未运行只算设计草案，测试通过也不自动说明独立复现。原项目保持只读。", "evidence": identity_ev}
        if kind == "transfer":
            activity["due_after_hours"] = 48
        activities.append(activity)

    return complete_framework({"schema_version": 2, "title": "原创Java本人订单：从功能到真实调用链",
            "project": {"name": "Java本人订单教学示例"}, "files": files,
            "concepts": concepts, "activities": activities, "flows": flows}, anchor)


def complete_framework(bundle, anchor):
    """Present the purpose, actual method boundaries, concrete returns and review questions."""
    bundle["project_summary"] = {
        "purpose": "这是一段可以嵌入应用的 Java 小内核：把“查看我的订单”请求安全地交给业务服务，并用同一种结果格式返回。它还包含一个独立的会话消息存储示例。它不是一个可直接登录、聊天或付款的网站。",
        "audience": "准备把自己做过的项目讲明白的学习者。使用订单功能的是已取得服务器身份的调用者；开发者负责在应用启动时提供身份上下文与服务实现。",
        "scenario": "调用者交入工具名 list_my_orders 和空参数对象。若可信身份为 1001L，结果只包含订单1；若伪造 buyerId，校验器先拒绝，身份与服务都不读取。另一功能将一条文本追加到指定会话，随后按同一键读取。",
        "capabilities": ["按工具名分发只读本人订单请求", "在调用业务前检查原始参数与可信身份", "按当前买家ID筛选固定示例订单", "把成功、业务拒绝和意外异常统一成 ToolResult", "按会话键追加和读取消息，供分析状态与引用边界"],
        "learning_value": "适合学会解释一条完整业务链：接口声明如何连接具体实现，校验为什么放在下游之前，身份为何不能来自模型参数，Long如何按值比较，失败怎样提前返回，集合引用怎样传递。代码很小，可以用三个具体输入手算并在练习区独立复现；无需先背完整框架。",
        "limitations": "下面的流程是依据包内源码的静态教学推演。手动装配和可信上下文返回值是案例明确前提；源码没有 main、Spring、HTTP、JWT实现、SQL、真实模型协议、超时重试或生产日志。Fake固定两条订单；会话用普通HashMap，无并发安全、容量或TTL保证。引用校验与选择题通过不证明独立掌握。",
        "evidence": [anchor("README.md", "这是为拾阶另写"), anchor("ToolDispatcher.java", "public ToolResult<?> dispatch"), anchor("ConversationMemory.java", "public void append")],
    }
    flow = bundle["flows"][0]
    by_step = {s["id"]: s for s in flow["steps"]}

    def method(sid, mid, signature, relation, incoming, output, why, file, needle):
        item = {"id": mid, "signature": signature, "relation": relation,
                "input": incoming, "output": output, "why": why, "evidence": [anchor(file, needle)]}
        by_step[sid].setdefault("methods", []).append(item)
        return item

    def event(sid, mid, explanation, *values):
        return {"step_id": sid, "method_id": mid, "explanation": explanation,
                "values": [{"name": n, "type": t, "value": v, "source": source} for n, t, v, source in values]}

    wiring = {"id": "wiring", "title": "先认识谁交给谁，随后才开始请求", "actor": "教学调用者手动装配",
              "action": "假设调用者已构造Validator、可信身份上下文与Fake服务，再构造Tools与Dispatcher。仓库没有启动入口，此步是每个案例的装配前提，不是假装发现了实际main。",
              "input": "CurrentUserContext接口的具体可信实现由案例约定；OrderService运行时是Fake或明确说明的失败Stub。",
              "output": "Tools保存两条依赖引用；Dispatcher保存Validator与Tools引用。", "why": "先确认实际实现，后面的接口调用才有确定含义。",
              "concept_ids": ["interfaces-di"], "evidence": [anchor("OrderTools.java", "this.orderService = orderService;"), anchor("ToolDispatcher.java", "this.orderTools = orderTools;")], "methods": []}
    flow["steps"].insert(0, wiring)
    by_step["wiring"] = wiring
    method("wiring", "tools-construct", "OrderTools(CurrentUserContext, OrderService)", "call", "两个已构造的依赖对象引用", "Tools对象保存引用，OrderService的实际类型可为Fake", "构造器注入不等于Spring运行。", "OrderTools.java", "public OrderTools(")
    method("wiring", "dispatcher-construct", "ToolDispatcher(ToolArgumentValidator, OrderTools)", "return", "校验器与Tools对象", "可由调用者显式调用的Dispatcher对象", "此处不猜测框架自动绑定。", "ToolDispatcher.java", "public ToolDispatcher(")
    method("entry", "dispatch-enter", "ToolDispatcher.dispatch(String, Map<String,Object>)", "call", "工具名String和原始参数Map", "等待validate结果；当前没有订单数据", "统一入口先保留未经信任的原始值。", "ToolDispatcher.java", "public ToolResult<?> dispatch")
    method("validate", "validate-enter", "ToolArgumentValidator.validate(String, Map<String,Object>)", "call", "同一工具名与Map引用", "先判断工具登记与参数边界", "所有业务请求先进入校验。", "ToolArgumentValidator.java", "public ToolResult<Void> validate")
    method("validate", "tool-name-equals", "String.equals(Object)", "return", "固定白名单名list_my_orders与传入toolName", "boolean：工具名是否匹配", "常量作接收者，传入null也可判不匹配。", "ToolArgumentValidator.java", 'if ("list_my_orders".equals(toolName))')
    method("validate", "map-empty", "Map.isEmpty()", "return", "已排除null的arguments", "boolean：是否无参数", "nullMap不能直接调用此方法。", "ToolArgumentValidator.java", "if (!arguments.isEmpty())")
    method("validate", "validation-success-factory", "ToolResult.success(T)", "call", "校验数据Void=null", "准备创建成功校验结果", "成功是否成立由success字段决定。", "ToolArgumentValidator.java", "return ToolResult.success(null);")
    method("validate", "validation-success-record", "ToolResult(boolean, String, String, T)", "return", "true、OK、空消息、null", "ToolResult<Void>：success=true，data=null", "record构造器存四个字段；没有业务订单。", "ToolResult.java", 'return new ToolResult<>(true, "OK", "", data);')
    method("validate", "validation-failure-factory", "ToolResult.failure(String, String)", "call", "稳定错误码与固定中文消息", "准备创建参数或工具拒绝结果", "业务拒绝使用值返回，不是throw。", "ToolResult.java", "public static <T> ToolResult<T> failure")
    method("validate", "validation-failure-record", "ToolResult(boolean, String, String, T)", "return", "false、拒绝码、固定消息、null", "ToolResult<Void>：success=false", "不把输入Map当可信业务数据返回。", "ToolResult.java", "return new ToolResult<>(false, code, message, null);")
    method("validate", "validate-return", "ToolArgumentValidator.validate(String, Map<String,Object>)", "return", "已经生成的ToolResult<Void>", "同一个结果交还Dispatcher并绑定validation", "被调用方法先返回，调用者才继续。", "ToolDispatcher.java", "ToolResult<Void> validation =")
    method("route", "validation-success-getter", "ToolResult.success()", "return", "validation对象", "boolean：参数校验是否成功", "record生成的访问器与有参数的静态工厂是不同方法。", "ToolDispatcher.java", "if (!validation.success())")
    method("route", "dispatch-reject", "ToolDispatcher.dispatch(String, Map<String,Object>)", "stop", "success=false的validation", "原样提前返回；Tools、身份、Service未调用", "return结束当前方法，后续行不执行。", "ToolDispatcher.java", "if (!validation.success())")
    method("route", "route-equals", "String.equals(Object)", "return", "toolName已经通过白名单", "true，准备调用登记Tools", "这是Dispatcher自己的路由判断。", "ToolDispatcher.java", 'if ("list_my_orders".equals(toolName))')
    method("identity", "tools-enter", "OrderTools.listMyOrders()", "call", "无业务参数；两个依赖引用已保存", "读取可信上下文", "无参数工具不需要虚构Converter。", "OrderTools.java", "public ToolResult<List<OrderSummary>> listMyOrders")
    method("identity", "identity-getter", "CurrentUserContext.getUserId()", "return", "服务器提供的上下文依赖；不是原始Map", "Long：1001L、1002L、9000L或null", "接口实现未包含，实际值按案例前提解释。", "OrderTools.java", "currentUserContext.getUserId()")
    method("identity", "unauthorized-factory", "ToolResult.failure(String, String)", "call", "UNAUTHORIZED、请先登录", "准备创建身份拒绝值", "null身份立即拦截下游。", "OrderTools.java", 'return ToolResult.failure("UNAUTHORIZED"')
    method("identity", "unauthorized-record", "ToolResult(boolean, String, String, T)", "return", "false、UNAUTHORIZED、请先登录、null", "身份拒绝ToolResult，随后Tools提前返回", "对象工厂没有执行查询。", "ToolResult.java", "return new ToolResult<>(false, code, message, null);")
    method("service", "service-call", "OrderService.listMyOrders(Long) → 已注入实现", "call", "可信currentUserId，不接受模型buyerId", "Fake正常返回列表；失败Stub可能抛异常", "按运行时对象动态分派，接口自身不建SQL。", "OrderTools.java", "orderService.listMyOrders(currentUserId)")
    method("filter", "fake-enter", "FakeOrderService.listMyOrders(Long)", "call", "当前案例的可信Long身份", "检查身份并新建结果列表", "业务实现也有非null身份边界。", "FakeOrderService.java", "public List<OrderSummary> listMyOrders")
    method("filter", "result-construct", "ArrayList<OrderSummary>()", "return", "每次查询初始为空", "一个新的可变result容器", "不把固定源orders直接交给调用者。", "FakeOrderService.java", "new ArrayList<>()")
    method("filter", "buyer-getter", "OrderSummary.buyerId()", "return", "循环中当前订单记录", "Long：订单1为1001L，订单2为1002L", "读取该资源的实际归属，参与对象权限。", "FakeOrderService.java", "currentUserId.equals(order.buyerId())")
    method("filter", "identity-equals", "Long.equals(Object)", "return", "currentUserId与当前buyerId", "boolean：身份值是否相同", "比较值而不是Long引用是否同一对象。", "FakeOrderService.java", "currentUserId.equals(order.buyerId())")
    method("filter", "result-add", "List.add(OrderSummary)", "return", "匹配的同一订单对象引用", "result增加该记录；未匹配记录不调用add", "筛选不等于深拷贝元素。", "FakeOrderService.java", "result.add(order)")
    method("filter", "fake-return", "FakeOrderService.listMyOrders(Long)", "return", "循环结束后的result", "List<OrderSummary>交还Tools；可为空列表", "成功空列表不等于权限异常。", "FakeOrderService.java", "return result;")
    method("wrap", "orders-success-factory", "ToolResult.success(List<OrderSummary>)", "call", "正常返回后才绑定的orders", "准备成功业务结果", "异常时不存在这次成功包装。", "OrderTools.java", "return ToolResult.success(orders)")
    method("wrap", "orders-success-record", "ToolResult(boolean, String, String, T)", "return", "true、OK、空消息、同一orders列表引用", "ToolResult<List<OrderSummary>>", "record不深度冻结可变列表。", "ToolResult.java", 'return new ToolResult<>(true, "OK", "", data);')
    method("wrap", "tools-return", "OrderTools.listMyOrders()", "return", "成功ToolResult", "同一对象返回Dispatcher", "结果沿调用栈向外返回。", "ToolDispatcher.java", "return orderTools.listMyOrders();")
    method("return", "tools-unauthorized-return", "OrderTools.listMyOrders()", "stop", "身份拒绝结果", "UNAUTHORIZED交还Dispatcher；Service不调用", "业务失败仍是一次正常方法返回。", "OrderTools.java", 'return ToolResult.failure("UNAUTHORIZED"')
    method("return", "service-exception", "OrderService.listMyOrders(Long) 的失败Stub", "stop", "案例明确另行注入的Stub", "RuntimeException沿Tools传播，orders赋值未完成", "现有Fake没有这条故障行为；不是实际故障注入记录。", "ToolDispatcher.java", "catch (RuntimeException failure)")
    method("return", "internal-failure-factory", "ToolResult.failure(String, String)", "call", "INTERNAL_ERROR、工具执行失败", "准备固定错误结果，不输出异常内容", "catch阻止内部异常细节外泄。", "ToolDispatcher.java", 'return ToolResult.failure("INTERNAL_ERROR"')
    method("return", "internal-failure-record", "ToolResult(boolean, String, String, T)", "return", "false、INTERNAL_ERROR、固定消息、null", "固定失败ToolResult", "稳定结果不等于修复根因或已经记录日志。", "ToolResult.java", "return new ToolResult<>(false, code, message, null);")
    method("return", "dispatch-return", "ToolDispatcher.dispatch(String, Map<String,Object>)", "return", "Tools正常结果或catch固定失败", "ToolResult<?>交还原调用者", "完整结束请求，不继续执行已经停止的层。", "ToolDispatcher.java", "public ToolResult<?> dispatch")

    for case in flow["trace_cases"]:
        old = {f["step_id"]: f for f in case["frames"]}
        entry_values = old["entry"]["values"]
        frames = [event("wiring", "tools-construct", "案例装配前提：Tools保存可信上下文和服务对象引用。源码没有实际main；没有运行这些构造。", ("orderService", "OrderService", "失败Stub" if case["id"] == "dependency-fails" else "FakeOrderService对象", "本案例的明确手动装配前提")),
                  event("wiring", "dispatcher-construct", "校验器和Tools保存到Dispatcher，随后由调用者发起功能。", ("dispatcher", "ToolDispatcher", "持有Validator与Tools引用的对象", "案例装配前提"))]
        frames.append({**old["entry"], "method_id": "dispatch-enter"})
        frames.append({**old["entry"], "step_id": "validate", "method_id": "validate-enter", "explanation": "dispatch同步进入validate，调用者暂时停在这一调用行。"})
        frames.append(event("validate", "tool-name-equals", "工具名匹配白名单。", ("matches", "boolean", "true", "String.equals返回")))
        if case["id"] != "missing-map":
            frames.append(event("validate", "map-empty", "null检查通过以后才调用isEmpty。", ("arguments.isEmpty()", "boolean", "false" if case["id"] == "forged-buyer" else "true", "Map查询返回")))
        rejected = case["id"] in {"forged-buyer", "missing-map"}
        if rejected:
            code = "UNKNOWN_ARGUMENT" if case["id"] == "forged-buyer" else "MISSING_ARGUMENT"
            frames.append(event("validate", "validation-failure-factory", "触发实际拒绝条件，进入失败工厂；参数对象尚未被当作身份。", ("code", "String", code, "对应拒绝分支的常量")))
            frames.append(event("validate", "validation-failure-record", "构造失败record，工厂返回该对象。", ("validation", "ToolResult<Void>", f"success=false, code={code}, data=null", "工厂构造返回")))
        else:
            frames.append(event("validate", "validation-success-factory", "合法空Map进入成功工厂，实参为null。", ("data", "Void", "null", "校验不携带业务数据")))
            frames.append(event("validate", "validation-success-record", "构造成功record，工厂返回该对象。", ("validation", "ToolResult<Void>", "success=true, code=OK, data=null", "成功工厂返回")))
        frames.append(event("validate", "validate-return", "validate返回后，Dispatcher的validation变量才完成赋值。", ("validation.success", "boolean", str(not rejected).lower(), "返回对象字段")))
        frames.append(event("route", "validation-success-getter", "调用无参数success访问器，读取是否继续。", ("validation.success()", "boolean", str(not rejected).lower(), "record访问器返回")))
        if rejected:
            frames.append({**old["route"], "method_id": "dispatch-reject", "explanation": "Dispatcher原样提前return。Tools、getUserId、Service、Fake及成功包装均未执行；原服务固定数据未改变。"})
            case["frames"] = frames
            case["skipped_step_ids"] = ["identity", "service", "filter", "wrap", "return"]
            continue
        frames.append(event("route", "route-equals", "路由名比较为true，才进入已登记Tools。", ("toolName", "String", "list_my_orders", "原始入口参数")))
        frames.append(event("identity", "tools-enter", "进入无参数Tools方法，此时尚未产生currentUserId与orders。", ("arguments", "Map<String,Object>", "{}（此方法不接收它）", "Dispatcher已经校验的入口Map")))
        frames.append({**old["identity"], "method_id": "identity-getter"})
        if case["id"] == "logged-out":
            frames.append(event("identity", "unauthorized-factory", "身份是null，在调用Service之前进入失败工厂。", ("currentUserId", "Long", "null", "可信上下文返回")))
            frames.append(event("identity", "unauthorized-record", "构造UNAUTHORIZED结果并从工厂返回。", ("return", "ToolResult<List<OrderSummary>>", "success=false, code=UNAUTHORIZED, data=null", "失败工厂构造")))
            frames.append(event("return", "tools-unauthorized-return", "Tools提前返回，Service、过滤与成功包装未调用；不是查询成功空列表。", ("orders", "尚未绑定的局部变量", "尚未产生", "Service调用行未执行")))
            frames.append({**old["return"], "method_id": "dispatch-return"})
        elif case["id"] == "dependency-fails":
            frames.append({**old["service"], "method_id": "service-call"})
            frames.append(event("return", "service-exception", "失败Stub抛出RuntimeException。Tools未正常返回；跳过Fake筛选与成功包装，沿调用栈到Dispatcher的catch。", ("orders", "尚未绑定的局部变量", "尚未产生", "服务正常返回前抛异常")))
            frames.append(event("return", "internal-failure-factory", "catch进入固定失败工厂，异常正文没有成为消息。", ("code", "String", "INTERNAL_ERROR", "catch常量")))
            frames.append(event("return", "internal-failure-record", "record构造固定错误并由工厂返回。", ("return", "ToolResult<?>（data=null）", "success=false, code=INTERNAL_ERROR, message=工具执行失败", "固定错误工厂")))
            frames.append({**old["return"], "method_id": "dispatch-return"})
        else:
            uid = int(case["id"].split("-")[1])
            frames.append({**old["service"], "method_id": "service-call"})
            frames.append(event("filter", "fake-enter", "接口字段实际绑定Fake，进入它的覆盖方法；不是接口自动生成查询。", ("currentUserId", "Long", f"{uid}L", "Tools传来的同一可信身份")))
            frames.append(event("filter", "result-construct", "每次new一个空结果容器，源orders仍有两条。", ("result", "List<OrderSummary>（ArrayList）", "[]", "本次new ArrayList")))
            for order, buyer, product, status in ((1, 1001, "接口练习册", "PAID"), (2, 1002, "集合练习册", "CREATED")):
                frames.append(event("filter", "buyer-getter", f"第{order}轮先调用记录访问器；字段值没有来自模型。", ("order.buyerId()", "Long", f"{buyer}L", f"固定订单{order}记录字段")))
                frames.append(event("filter", "identity-equals", f"再调用Long.equals，{uid}L与{buyer}L按值比较。", ("matches", "boolean", str(uid == buyer).lower(), "Long.equals返回")))
                if uid == buyer:
                    frames.append(event("filter", "result-add", "匹配后才add同一个订单对象引用。未匹配一轮不执行add。", ("result", "List<OrderSummary>", f'[OrderSummary({order}L,{buyer}L,"{product}","{status}")]', "本轮List.add")))
            frames.append({**old["filter"], "method_id": "fake-return", "explanation": "两轮结束，Fake返回result。回到Tools的赋值行，orders与result引用同一列表。"})
            frames.append({**old["wrap"], "method_id": "orders-success-factory"})
            frames.append({**old["return"], "step_id": "wrap", "method_id": "orders-success-record", "explanation": "成功工厂调用record构造器，再返回新结果对象，data仍引用同一可变列表。"})
            frames.append({**old["return"], "step_id": "wrap", "method_id": "tools-return", "explanation": "Tools正常return成功对象，调用者恢复为Dispatcher。"})
            frames.append({**old["return"], "method_id": "dispatch-return"})
        case["frames"] = frames
        if case["id"] == "logged-out":
            case["skipped_step_ids"] = ["service", "filter", "wrap"]
        elif case["id"] == "dependency-fails":
            case["skipped_step_ids"] = ["filter", "wrap"]

    add_memory_flow(bundle, anchor)
    add_review_questions(bundle, anchor)
    return bundle


def add_memory_flow(bundle, anchor):
    def ev(needle):
        return [anchor("ConversationMemory.java", needle)]

    def method(mid, sig, relation, incoming, output, why, needle):
        return {"id": mid, "signature": sig, "relation": relation, "input": incoming,
                "output": output, "why": why, "evidence": ev(needle)}

    def step(sid, title, action, incoming, output, why, needle, methods):
        return {"id": sid, "title": title, "actor": "ConversationMemory", "action": action, "input": incoming,
                "output": output, "why": why, "concept_ids": ["collections-state", "java-types"], "evidence": ev(needle), "methods": methods}

    append_methods = [
        method("append-enter", "ConversationMemory.append(String, String)", "call", "key=room-a；message=你好", "开始查找或建立该键的消息列表", "追加与读取是独立于订单的功能。", "public void append"),
        method("map-compute", "Map.computeIfAbsent(String, Function)", "call", "当前conversations没有room-a", "准备调用缺省值工厂；已有键时直接取原列表", "只在缺失时建立容器。", "conversations.computeIfAbsent"),
        method("list-create", "ArrayList<String>()（缺省值工厂）", "return", "缺省键room-a", "新空列表存入Map，同一引用返回给append", "Lambda只在缺省条件下执行，不是每次都new。", "conversations.computeIfAbsent"),
        method("message-add", "List.add(String)", "return", "刚取得的列表；message=你好", "内部列表现在是[你好]", "是在列表上追加，不是覆盖整个会话。", "conversations.computeIfAbsent"),
        method("append-return", "ConversationMemory.append(String, String)", "return", "状态已改变", "void返回调用者", "void无返回值，但对象状态发生变化。", "public void append"),
    ]
    load_methods = [
        method("load-enter", "ConversationMemory.load(String)", "call", "key=room-a或缺省room-z", "读取该键列表", "按会话键隔离状态。", "public List<String> load"),
        method("empty-list", "List.of()", "return", "无元素", "空的不可修改列表作为缺省实参", "Java先求实参再调用getOrDefault，已存在键也会求值这个表达式。", "return conversations.getOrDefault"),
        method("map-default", "Map.getOrDefault(String, List<String>)", "return", "键与已经生成的空缺省列表", "已有键返回内部列表；缺省键返回空列表，不写Map", "读取并不补建会话，已有与缺省的所有权不同。", "return conversations.getOrDefault"),
        method("load-return", "ConversationMemory.load(String)", "return", "选中的List<String>", "交还调用者；已有键仍与内部共享容器", "此处没有List.copyOf，外部可改变已有会话。", "return conversations.getOrDefault"),
    ]
    steps = [step("append", "把消息追加到本会话", "进入append→computeIfAbsent→必要时新建列表→add→void返回。", '初始{}；key="room-a"；message="你好"', '{room-a:[你好]}', "用一组完整方法边界看见状态如何产生。", "conversations.computeIfAbsent", append_methods),
             step("load", "再按同一键读取消息", "调用者另发起load；先求List.of()缺省实参，再getOrDefault，最后把选中列表返回。", 'key="room-a"，或新键"room-z"', "已有键返回[你好]；新键返回[]", "load不是append内部调用，也没有复制已有列表。", "return conversations.getOrDefault", load_methods)]

    def frame(sid, mid, text, name, type_, value, source):
        return {"step_id": sid, "method_id": mid, "explanation": text, "values": [{"name": name, "type": type_, "value": value, "source": source}]}

    shared = [frame("append", "append-enter", "调用者进入append，两个String参数原样传入。", "message", "String", "你好", "调用者参数"),
              frame("append", "map-compute", "首次缺键，Map准备执行Lambda；现有键则跳过新建列表。", "conversations", "Map<String,List<String>>", "{}", "当前实例字段"),
              frame("append", "list-create", "缺省Lambda创建空ArrayList，Map保存并返回它。", "messages", "List<String>（ArrayList）", "[]", "缺省值工厂"),
              frame("append", "message-add", "在刚返回的列表调用add(你好)，改变内部状态。", "conversations", "Map<String,List<String>>", "{room-a:[你好]}", "List.add副作用"),
              frame("append", "append-return", "append返回void，状态仍保留在这个实例内。", "return", "void", "无返回值", "方法结束")]
    cases = []
    for cid, key, title in (("append-read", "room-a", "写入后读取原会话"), ("missing-room", "room-z", "读取不存在的会话不会创建它")):
        result = "[你好]（同一个内部列表）" if key == "room-a" else "[]（缺省不可修改列表）"
        cases.append({"id": cid, "title": title, "input": f'新实例先append("room-a","你好")，随后load("{key}")。',
                      "result": f"静态预测：load返回{result}，conversations仍只有room-a；未执行Java。", "frames": [*shared,
                      frame("load", "load-enter", "append结束后由调用者发起另一方法调用。", "key", "String", key, "这一次load参数"),
                      frame("load", "empty-list", "先求缺省实参List.of()，它不会加入Map。", "defaultValue", "List<String>", "[]（不可修改）", "List.of返回"),
                      frame("load", "map-default", "Map选已存在的内部列表或缺省值，不创建新键。", "selected", "List<String>", result, "getOrDefault返回"),
                      frame("load", "load-return", "同一个选中引用交给调用者；已有会话没有读取隔离。", "return", "List<String>", result, "load返回")]})
    bundle["flows"].append({"id": "conversation-state", "title": "追加和读取会话消息", "summary": "订单分发之外的第二项独立功能。全部方法依据源码静态推演；原实现无输入校验、可信用户绑定、容量、TTL或线程安全。",
                           "scenario": "调用者为room-a保存一句话，再读取同一键或不存在的键，理解状态保留和返回引用。",
                           "input": '新建ConversationMemory；append("room-a","你好")后load("room-a")。key只是调用者字符串，不能认定为已认证会话ID。',
                           "expected": "同一键读取内部列表；缺省键得到空列表。修改已有键的返回列表会改变内部状态，此实现的边界需要学习者识别。",
                           "steps": steps, "branches": [{"from_step_id": "append", "condition": "key已有非null列表", "outcome": "computeIfAbsent直接返回已有列表；ArrayList工厂未执行，再add到原列表。", "skipped_step_ids": []},
                                                           {"from_step_id": "load", "condition": "key不存在", "outcome": "返回已经求值的List.of空列表，不向Map插入会话；并不是身份失败。", "skipped_step_ids": []}],
                           "trace_cases": cases, "resources": [{"name": "会话Map与消息列表", "ownership": "每个ConversationMemory实例拥有Map；已有键的load直接泄露内部可变列表引用。", "lifecycle": "随实例创建，append增加消息，load不释放；没有TTL、容量上限、清理或持久化。", "failure": "无输入校验和并发保护，不能承诺生产会话隔离。缺省List.of与已有ArrayList的可修改性不同。"}]})


def add_review_questions(bundle, anchor):
    def add(aid, cid, title, prompt, options, answers, explanation, file, needle):
        bundle["activities"].append({"id": aid, "concept_id": cid, "kind": "predict", "title": title, "prompt": prompt,
                                     "options": [{"id": i, "text": text} for i, text in zip("abcdefgh", options)], "answer_ids": answers,
                                     "rubric": ["依据给定前提选择完整正确集合"], "hints": ["先确认哪个方法负责当前边界。", "沿进入与返回顺序排除没有执行的节点。", "用一个合法输入与一个拒绝输入对照；选择完整集合，不按词语熟悉程度猜。"],
                                     "explanation": explanation + " 本题检测具体机制的回忆；不等于独立实现已验收。", "evidence": [anchor(file, needle)]})
    add("order-q11", "java-types", "工厂与访问器为何同名", "在当前ToolResult record里，哪些说法正确？",
        ["success(null)是带一个数据参数的静态工厂", "validation.success()是读取布尔字段的无参数实例访问器", "两个调用都查询数据库", "data为null必然说明失败"], ["a", "b"],
        "“静态工厂”创建record；“布尔访问器”读取字段，这两项正确。两者都不访问数据库；校验成功可以data=null，因此不能用它代替success判断。", "ToolResult.java", "public record ToolResult<T>")
    add("order-q12", "control-flow", "非法参数后完整停止范围", 'dispatch("list_my_orders", {buyerId:1002L})，可信上下文若读取会返回1001L。哪些方法实际未调用？',
        ["CurrentUserContext.getUserId()", "OrderTools.listMyOrders()", "OrderService.listMyOrders(Long)", "ToolArgumentValidator.validate(String,Map)"], ["a", "b", "c"],
        "身份读取、Tools入口、业务查询都在校验失败return之后，故三项未调用。校验器确实已执行并生成UNKNOWN_ARGUMENT，不能把它也说成跳过。", "ToolDispatcher.java", "if (!validation.success())")
    add("order-q13", "interfaces-di", "当前示例能证明到哪里", "只依据此示例源码，不额外假设运行环境。哪些判断有依据？",
        ["Tools声明依赖OrderService接口", "Fake提供该接口的一个具体实现", "构造器允许把实现对象传给Tools", "Dispatcher统一收口RuntimeException为固定错误", "JWT认证和生产数据库已验收"], ["a", "b", "c", "d"],
        "接口依赖、Fake实现、构造器注入、固定异常边界都能回到源码。JWT与生产数据库在本示例不存在，不能从前四项推出生产能力。", "ToolDispatcher.java", "catch (RuntimeException failure)")
    add("order-q14", "collections-state", "已存在会话的返回引用", 'append("room-a","你好")后，view=load("room-a")；调用者清空view，再load同一键。结果是什么？',
        ["空列表，因为load没有复制已有列表", "仍是你好，因为record自动深拷贝", "Map自动删除room-a键", "只有订单列表被清空"], ["a"],
        "“空列表”正确：已有键的getOrDefault返回内部列表引用，clear改变同一容器。此类不是record，未做复制；clear列表也不删除Map键，与订单无调用关系。", "ConversationMemory.java", "return conversations.getOrDefault")
    add("order-q15", "collections-state", "缺省会话读取与追加的差异", '全新实例load("room-z")，之后append("room-z","第一句")。选择正确判断。',
        ["load返回空列表且不补建Map键", "之后append可创建新列表并成功追加", "第一次load已永久缓存不可修改列表，所以append必失败", "读取缺省键就是UNAUTHORIZED"], ["a", "b"],
        "getOrDefault仅选择缺省返回值，因此“不补建键”正确；后续computeIfAbsent仍能建立新ArrayList，因此“成功追加”正确。没有永久缓存和认证判断，另两项无依据。", "ConversationMemory.java", "conversations.computeIfAbsent")
    add("order-q16", "control-flow", "失败依赖有没有订单局部值", "明确注入会抛RuntimeException的OrderService Stub。参数合法，可信身份1001L。哪些判断正确？",
        ["orderService调用已发生", "Tools的orders赋值未正常完成", "Dispatcher返回固定INTERNAL_ERROR", "Tools随后仍执行success(orders)"], ["a", "b", "c"],
        "调用发生后抛异常，正常返回缺失，所以orders赋值与成功包装未完成；异常沿调用栈进入Dispatcher catch并固定失败。“仍执行成功包装”错误。这个Stub是案例前提，不能声称Fake真的发生故障。", "OrderTools.java", "orderService.listMyOrders(currentUserId)")


if __name__ == "__main__":
    bundle = build_bundle()
    (ROOT / "learning-bundle.json").write_text(json.dumps(bundle, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"Created learning-bundle.json: {len(bundle['files'])} files, {len(bundle['concepts'])} concepts, "
          f"{len(bundle['activities'])} activities, {len(bundle['flows'])} flows.")
