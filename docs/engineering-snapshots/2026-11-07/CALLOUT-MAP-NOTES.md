# 交接补记：code 读取与条件调用边

此文整理同一独立只读代理已完成的窄源码 map，属于静态调查，未产生新 V3 决策或源码。`R/` 为本快照 r1528 `working-01/runtime/`；`I/` 为本机既有 r1108 CPython 索引，该索引未完整随包交付。

| 站点 | 已读源码事实 |
| --- | --- |
| vectorcall | `R/Objects/call.c.txt:914` 读 function flags；普通入口 1097–1098 经 `_PyEval_Vector:2478` 获取引用。已读普通分支没有显式分配／evaluator 调用。 |
| frame 选择 | `R/Python/ceval.c.txt:1870` 再读 `func_code`，1918 使用 `co_framesize`。PushFrame 容量不足经 pystate 进入 `_PyObject_VirtualAlloc`；最终 allocator 实现未读，不能称全段无 callback。 |
| frame／binder | ceval 1897 使用先前 code 初始化 frame，1902 独立晚读 `func->func_code` 给 binder。code guard 只保证生命周期，不保证 code 不变。 |
| binder | positional 搬移和关键字指针比较是 raw 段；dict/tuple 分配、慢 richcompare、hash、kwdefaults lookup、格式化、错误 helper 和实际 LAST DECREF 是需要分别核对的条件调用边。defaults 字段在部分调用边之后读取。 |
| evaluator | `_PyEval_EvalFrame` 在 `eval_frame != NULL` 时调用实际 hook。没有证明 hook 实际安装或触发；默认入口直接 DISPATCH，没有统一入口 pending poll，目标 opcode 顺序未取证。 |
| MakeCoro | `R/Objects/genobject.c.txt:896` 读取当时 flags；900/904/915 各在实际 caller 点再读当前 `func_code`。873 算 slots、874 分配，883/885 在分配后取名称。 |
| coroutine origin | genobject 929→1309，1319 分配 tuple，1325 取各 frame 的 code；GetLine／BuildValue 最终实现本轮未展开。 |
| RETURN_GENERATOR | 既有 `I/generated_cases.c.h:4552–4559` 是 MakeCoro 后更新 stack pointer 与 Frame_Copy。**已读 handler 没有 pending poll**；Copy 仍只有既有服务表示证据，没有新增原始 C／构建证明。 |

GC 分配链达到条件时，`_PyObject_GC_Link → _Py_ScheduleGC` 只设置调度位和 eval_breaker。只有之后实际到达 poll，才可能经 `_Py_HandlePending → _Py_RunGC → gc_collect_with_callback → invoke_gc_callback` 调用已注册 callback。不能把 `PyObject_GC_NewVar` 或 binder 分配直接写成同步执行 GC callback；也不能据此泛称全部 allocator 无 callback。是否实际轮询、收集或注册 callback 均没有运行证据。

dict 事件调用站点和 `_PyDict_SendEvent` watcher 调用可见；inline 通知分派未读，未证明新建 kwdict 已被 watch。DECREF 仅在实际达到 LAST 时才形成 `_Py_Dealloc → tp_dealloc` 边，未取得运行 LAST 见证。

本轮也读到 CPython v3.12.10 官方 `Doc/c-api/function.rst` 的既有本机副本（7,006 B；SHA-256 `1e70381d3f09e27217544d21e08095008681bf593b61cc844ce7514cef5ec53f`）：watcher 可 inspect，但不得修改本次 func；须保留进入时 pending exception。SetClosure 的 None/cell tuple 文字不证明任意长度形状合法。原文未随本次快照复制。

窄 map 审读回执：`686a4e`、`74e222`、`d496f9`、`73b287`、`3afd3f`、`30a0c7`、`f36e85`、`0563d0`、`c9bb5b`、`020964`、`780af5`，均 exit0。function 文本主读 `649f69`、来源清单 `b01205`。这些是历史回执引用，不是随包提供的运行证明。

不得新增普通动态域 code-equality 拒绝，或以 generic callable 旁路替代完整 owned 路线。这些调用边仍没有完成合法 code 关联、V3、COMPAT、物理预算或完整产品交付证明。
