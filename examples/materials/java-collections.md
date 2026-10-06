# 集合：查找速度与对象约定

这是一份为 CodeSprint Studio 编写的原创演示资料，以简单场景练习 Java 基础。可以修改它，再生成自己的题库；不是外部课程或题库原文。

## HashMap 查找

HashMap 先利用键的哈希值定位桶，再在桶内比较键。键的 equals 相等时，hashCode 必须一致；哈希相同不保证 equals 相等。碰撞可能发生，不能把哈希值当作对象的唯一身份。对象被用作键后，不宜再修改参与 equals 和 hashCode 的字段，否则查找可能进入不同的桶，找不到原有条目。

平均查找效率并不意味着每次调用都只执行一次比较。容量、负载因子、碰撞分布和扩容都会影响实际行为。HashMap 允许一个 null 键和 null 值，但这些规则不能直接套用到 ConcurrentHashMap。

## 多线程与复合操作

HashMap 不是线程安全容器。ConcurrentHashMap 提供并发访问能力，但先 get 再 put 组成的两步逻辑不会因为容器并发安全而自动成为一个原子操作。计数、按条件创建对象等场景，应考虑 merge、computeIfAbsent 等与目标语义匹配的方法，并控制回调副作用。

## 遍历与删除

遍历列表时，增强 for 并不允许随意调用原列表的 remove。需要逐项删除时，可以使用 Iterator 的 remove 或 removeIf；改变集合结构后，旧迭代器的状态可能失效。fail-fast 检测属于尽力检测错误，不应作为多线程同步方案，也不保证所有错误修改都必然抛出异常。

## 自己动手

用两个逻辑相等的键 put 两次，观察大小；再用可变键改变参与哈希的字段，观察查找。解释为什么修复对象约定比加一次重试更合理。最后比较非原子 get/put 与 merge 的计数结果。

Copyright 2026 chaosmakerw and contributors. Apache-2.0. AI-assisted original demonstration material.
