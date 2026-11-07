# StorageProfile 路径构造原始记录实施方案

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 在四字段实际规范化调用发生时保存输入、普通返回、Path 原始状态与 setter 效果，为后继 root 来源接线提供真实前件。

**Architecture:** 以 r1006 的九文件完整候选为 parent，新增不导入产品资源模块的 `storage_profile_origin.py`。记录由 profile 自己持有，模块仅保弱索引；保原四字段字典和原调用顺序。此段只产生观察证据，不激活 strict 准入，不将 factory 返回观察等同于完整 Path 出生认证。

**Tech Stack:** Python 3.12.10、现有 frozen dataclass、Path 原 descriptor、weakref、GC；仅源码候选和 source-selected 词法模型。

---

## 1. 范围与事实

`StorageProfile.__post_init__` 先于原 origin begin 发生。现有 scope 在 select_paths 时只保存四个已经存在的 Path 身份；r1006 的 selected_input_observation 明确 birth_proven=False。不能事后用同值、当前 image 或原类型补签历史。

本轮先完成 profile 四字段 producer；`database.parent`、profile.roots 的 last-wins 与外层 `_roots` first-wins/两级排序、后续 identity 与 roots 比较及其合法 cache 更新列为下一段。它们未接齐之前，全部 Path/physical/marker/计费/发布旗标保持 False，不把该组合安装到 canonical/c973。

不使用诊断、profiling、frames/locals、额外计时或新权限。r920/r964已耗；r967仍待原问题，不执行、不重问。普通开发及同一只读复审已获授权，继续复用该代理；不新增代理、Git 操作或 worktree。

## 2. 选型与兼容

采用 profile 内两个私有 slot 持有同一 journal，保留 `__dict__` 和 `__weakref__`。四个 dataclass 字段、生成 init/equality/hash/repr 与 `vars(profile)` 四项不变。模块 registry 仅 `id -> (weak profile, weak journal)`；callback 只捕获 id/registry，不闭包持有 profile/journal。原输入或异常 traceback 即使反向引用 profile，也只是可由 GC 回收的实例内环，不形成全局强根。

不采用全局强 raw map：PathLike 输入/异常 traceback 可能间接保住 profile，weak key 本身不足以解决。也不采用进入 origin 后补快照，因为那不能提供构造时实际返回。

slot 是持有锚，journal 内独立 raw 闭包是内容来源。弱索引与一个 surviving slot 能恢复另一个 slot 的损坏；无共识时报告 unknown/corrupt，不创建历史，不无条件修复 slot。两锚同时损坏/一致伪造不作恢复承诺。

copy/deepcopy/pickle 从原 `object.__getstate__` 取得动态字典及子类 slotstate，只排除新增两私有 journal slot；无其余 slots 时返回原字典形式，有其余 slots 时保原二元组形式。恢复保原 dict-update/对子类 slot 的动态 setattr 语义，不携带原 journal、不重新运行规范化。新对象历史为 unknown；不能把副本认作原 producer。显式 `__getstate__/__setstate__` 保持原状态恢复语义，测试 frozen/dataclass/含自有slots子类行为与原版对照。`dataclasses.replace` 调用原构造链，应有自己的新记录。未知或 legacy PathLike 仍按原调用执行，不因记录层缩小合法输入范围。

## 3. 实际调用与失败

`_path(value)` 原无记录入口保持原实现；新增仅供 post_init 的固定私有接线。每个调用在执行前登记，普通返回先存，再记录 raw image；不能通过传入 callee/expected/callback 选择任意执行。

每字段记录：原 getattr；第一次 Path(value)；is_absolute；按原短路顺序的一次/两次 parts getter及 membership/len；原 os.fspath；原 os.path.abspath；第二次 Path(actual_abspath)；`_path` 返回；原 object.__setattr__ 的普通返回和通过原 C dict 观察到的字段效果。两个 Path factory 返回先强持有再读取九 slots；读 image 不调用 str/parts/hash/fspath/eq，不预热。

保留原 ValueError/TypeError/OSError -> STORAGE_INVALID_PROFILE/400 映射。非这三类的 BaseException 原对象传播；失败 cell 与普通返回后观察失败分开。setter 已生效而后验失败时不能补普通返回。记录同时保原首错和最终映射错误，不能以记录后验错覆盖既有业务错误。

factory 内输入转换、ntpath 的 C abspath fallback、类/globals/stdlib 完整认证仍 unknown。本段保存实际原调用的 callee 身份和返回，并不宣称冻结完整动态依赖。所有 image 只称 return-point observation，birth_proven=False；任意 PathLike 在工厂内部的副作用不倒推成已认证。

## 4. 文件级任务和验证

### Task 1：冻结有限来源、设计双审

Create `data/t16-h01-profile-normalization-origin-r1008/`，保此设计、r1006 manifest、九 parent、原 lifecycle、r1007交接、实际 pathlib 与 ntpath 全文/SHA。Root 与同一代理核原操作先后、short circuit、异常映射、copy/pickle/GC边界；有缺陷保旧稿另出修订。

### Task 2：完整候选实现

Modify 候选 `storage_lifecycle.py` 的 `_path` 与 `StorageProfile.__post_init__`，新增状态序列化方法；Create 候选 `storage_profile_origin.py`。其余八 parent 全文继承。保完整 parent/inverse/diff、新增文件缺失基线与十文件 ASTcompile；正式/c973新文件缺失状态及18旧保护目标核同。

### Task 3：有界 source-selected 检查

使用原 lifecycle 的 `_path`、StorageProfile、_key/_roots 及新模块完整源码，明确外部文件 I/O/SQL/资源HANDLE或EX native 均不执行。原 Windows ntpath.abspath 的 C `_getfullpathname` 词法返回可以实际发生；若用 _key 对照，normcase 的 C `_LCMapStringEx` 亦可发生。两者与资源获取 native 分列，不能宣称没有任何 C 代码运行或已认证其完整来源。新固定用例覆盖普通 str/Path/PathLike、四字段顺序、parts短路、非法输入/异常映射、返回/image身份、原始列表副本不漂移、setter效果、frozen/dataclass、copy/deepcopy/pickle/replace、单锚与索引损坏、self-referencing PathLike/traceback 的 GC、真实线程隔离。与原版比较值和实际外部 PathLike 回调次数；对照不得替换目标实现获得绿。

脚本和实际准备两份复审后，以新claim唯一执行；旧91/13结果不重跑，不称新组合完整回归。保持 case30/parent360/matrix1680/最多2pytest child/control1MiB，native/cleanup 原限额不变。原111/94/r919另待完整组合准入。

命令为 `.venv/Scripts/python.exe -I -B -X utf8 data/t16-h01-profile-normalization-origin-r1008/<已封存脚本>.py`；实际文件/固定ID/hash在执行前确定，当前模板不是准入。

### Task 4：记录结果与后继

保原输出、具名结果、失败和耗尽claim，Root及同一代理 actual复核；主方案只追加新事实，AGENTS只更新项目事实。回滚为不安装，不还原 dirty 工作树。

后继把该真实 journal 交给原 select_paths，再补原 parent/roots/key/identity/比较的逐调用与合法cache接线；copy/pickle/旧对象没有记录则明确unknown，不新建历史。完整 stdlib/global/C/ctypes/lock、三marker同预算与future N/容量、最终Context区间、新五件图/final/父退出/四恢复、Store/source_closed/live witness/写权、H02–H06/永久分享及完整Goal均未完成。

## 修订02

01 独立复审 D001 指出只保存字典会丢失合法子类自有 slots。02 保原 object 默认 state 的非journal slots 与动态恢复语义，新增含slots子类的 copy/deepcopy/pickle 对照。01 完整源稿保于 SOURCES-01；原红不删。其余范围不扩展。
