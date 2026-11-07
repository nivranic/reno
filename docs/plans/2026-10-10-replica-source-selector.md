# Replica 来源选择与路径分类实施计划

> 执行：使用 executing-plans 技能，按本批顺序实施与独立复核；沿用已授权工作区，不提交或推送。

**目标：** 让后继 replica 工厂通过明确、不可自动转换的两种来源值选择原址 raw2 包或移位 raw2 包，并在实际读取前识别固定包路径。

**架构：** 新增两个纯模块，遵循 r750/SOURCE-WIRE.md §2.1 的精确 API。投影只包含路径和来源参数，实际 owner、guard、目录身份、原字节与恢复权限继续由后继原生工厂取得。本批保持旧 raw2/Q1/R01/RP1 接口、期限与格式。

**技术：** Python 3.12.10、既有 StorageProfile、pathlib、pytest；不新增依赖。

## 1. 冻结纯接口

独审 r750 修订，先明确 S0 范围通过。原始方案、后继修订与审稿分别保留，不覆盖历史。SW01–04 和前驱扫描仍是运行设计，不能用此批纯实现填补 S1–S5。

来源模块提供关键字 constructor `ProfileRaw2Source(profile, operation_id, generation, expected_request_sha256)`、`DetachedRaw2Source(package_root, generation, expected_request_sha256)`，以及 `project_profile`、`project_selector`。generation 严格为整数 1 或 2；UUID/SHA 必须为规范文本。输出六字段 `{source_kind, package_root, generation, expected_request_sha256, profile_paths, original_operation_id}`，不含真实身份或能力字段。

路径分类模块提供 `classify_file` 与 `classify_directory`。闭集见 SOURCE-WIRE §2.1：正式控制、零字节 marker、payload、保留失败/临时文件分开；目录包括完整空目录与唯一 request-SHA anchor。未知路径拒绝，failure 永远 retained。

## 2. 实施源码

创建：

- `reno/personal_authority_replica_selector.py`
- `reno/personal_authority_replica_path_roles.py`

来源值使用 frozen/slots 和 identity equality。构造及每次投影重新验证 exact 类型、Path 原始表示/缓存一致性、StorageProfile 原类/property/字段指针与冻结路径投影。CPython Path 私有表示仅支持已审 3.12.10，未知实现或 descriptor 改变明确拒绝。路径校验只计算文本，不调用 stat、open、resolve、旧 G1 私有 helper 或旧 codec 私有分类器。

源码作者保存被后继替换的完整候选，交另一代理独立源审；作者不自审为通过。

## 3. 独立测试

创建：

- `tests/test_personal_authority_replica_selector.py`
- `tests/test_personal_authority_replica_path_roles.py`

测试作者依据规范与手写样本独立构造 oracle。验证两种来源保持独立、原址路径固定、非法 UUID/SHA/generation、混合变体、Path raw/cache 污染、零 hostile hook、property/字段 pin、路径深度/UTF-8 边界、构造后变化、返回容器分离和所有合法/非法 raw2 grammar。声明来源不证明 profile 来自真正 owner 或包已存在。

只运行两个新 selector；已有 G1、RP1/R01、H01 和正常服务不重复执行。独立源审同时审查源码、测试和本批 runner/freeze/finalizer，全部通过后再冻结输入。

## 4. 隔离执行与交付

新批次：`data/t16-h02-replica-selector-r752/`。runner 清理业务环境变量，使用新的 config/data/pytest 根，禁 conftest、插件自动加载、cache、网络和子进程。仅该批新日志与配置可写；不访问正常库或模型平台。

执行命令：

```text
.venv/Scripts/python.exe -I -B data/t16-h02-replica-selector-r752/freeze-inputs.py
.venv/Scripts/python.exe -I -B data/t16-h02-replica-selector-r752/run-isolated-suite.py run-01
```

从实际退出码、XML 和完整输入 hash 核对结果。失败保留，定位后只修受影响范围；不得扩大路径 grammar、取消 pin 或把旧绿相加掩盖新红。独立结果审查通过后，finalizer 再核实际用例与来源并产生 delivery。最终更新主方案后继章节和 AGENTS，明确 source/target/coordinator、原生复制、restore 与全 goal 仍未完成。

回滚：本批两个模块没有接入正常服务或公开 admin，可保留证据后撤销后继入口引用；不得删除已有资料、恢复旧 ACL、重置 dirty 工作区或回退别人的改动。
