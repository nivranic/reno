# 配置入口的显式 Profile 构造来源

2026-10-17，r1021 DESIGN01。落实profile-route-custody方案Task 3，依赖已交付r1020 SOURCE02。此段准备未安装组合，真实三个owned入口及其scope/root消费者仍按Task 4–5继续，不能把本段叫作路由接通。

## 当前事实

31份指定完整来源1286409bytes已核同，清单在data/t16-h01-configured-profile-origin-r1021/INVESTIGATION-01.json。正式与c973的storage_admin.py逐字节相同，均SHA256 5f25dce97c3a1cfd7adecf5335bfb9c493a9745ddb7f18b2da201bf9a84607c5。父十一候选及其48项结果已封存，本段不重跑旧批次。

指定四文件的直接StorageProfile构造共七处：storage_admin四处（公开profile一次、from_connection三次）、CLI一次、storage_members一次、storage_backups一次。指定十文件另有25个storage_admin.profile句法调用点；没有排除动态/别名调用，也不把句法次数当实际执行次数。API/线程池/worker/周期持有点在本段只列接线责任，不修改其业务入口。

既有候选lifecycle已导出实际create_owner/construct/owner_facts；construct在class type-call前认领owner，保存原四参数、实际profile/partial self/Cdict/四Path和48调用、setter return/effect、Context注册与reset事实。owner_facts明确helper_outer_return_observed=False；本段不能把新wrapper返回推作原producer已有的外层返回事实，更不授path/physical/marker/metering/publication权限。

## 实施选择

增加私有显式入口，公开profile与_configured_paths及现有from_connection/setup/connection全部AST与行为保持。自动让公开profile返回封套会破坏所有普通调用和四字符串投影；新建全局profile到owner强表会重新保活构造图；这两种不采用。

候选storage_admin.py在原profile定义之后增加无参数私有factory `_make_profile_capture`。factory在模块正常import lifecycle返回之后，一次捕获原lifecycle._profile_origin_create_owner_original、原lifecycle._profile_origin_construct_original、原_configured_paths、Path和StorageLifecycleError；它只保存静态callee，不捕获profile/owner/异常或操作上下文。原lifecycle顶层无storage_admin导入，db中相关引用是函数内lazy import；仍需复审整个实际新初始化顺序，避免重现r1020 partial-module eager helper缺陷。

factory返回 `_new_profile_owner` 与 `_profile_with_origin` 两个私有函数。前者只调用捕获的原owner factory并返回实际owner。后者必须显式接owner位置参数，其余四个kwonly参数与公开profile同名且默认None：

```python
owner = storage_admin._new_profile_owner()
profile = storage_admin._profile_with_origin(owner, database=..., media=..., reports=..., checkpoints=...)
```

调用者在进入captured helper前已经持有同一原owner，成功、预处理失败、constructor未返回或cleanup失败时都由该调用者继续持有owner及原异常；helper不内部创建owner、不在失败时重新构造同值对象、不包装或替换异常，也不自动重试。

`_profile_with_origin`逐句保留公开profile原输入检查：四字段全None才读原_configured_paths；部分None仍原STORAGE_PROFILE_REQUIRED/400；四值再按原顺序分别做Path(value).absolute()，将这四个实际返回值原样传给捕获的construct(owner, ...)；返回construct的实际结果。不能从已归一字段重造一份参数作为历史。getter/Path.absolute/config/env更早来源仍unknown；预处理失败尚未调用construct时，owner的construct_claimed/constructor_attempted仍False，caller持原异常，本段不伪填构造first或成功回执。显式None/坏owner最终按原construct拒绝，不降级到公开profile；若参数求值更早失败，保Python原求值顺序与该原错误。

helper使用factory闭包原callee，模块导出替换不应使已创建helper改调其他constructor。该静态捕获不等于认证所有可写函数代码/globals，后续Task 4–5必须把实际传来的owner/profile与原producer函数/记录及原构造结果核同；当前不提供来源查找或权限授予API。

预计完整组合十二文件：父十一逐字节继承，storage_admin以已核同的正式/c973原文件为新增parent pair并仅追加上述factory/两导出，不改其他实现。提供十二份parent/candidate/inverse及原bytes精确diff，ASTcompile十二份；正式树与c973均不安装。保护目标由旧22项增storage_admin正式/c973两项，预计12同12缺，实际核验后记录。

## 重建入口与后续交接

| 入口 | 本段保留的来源含义 | 后续工作 |
| --- | --- | --- |
| storage_admin.profile/from_connection | 普通构造/比较对象，未自动取得构造owner | 严格调用者显式create owner并进入私有capture；from_connection不能凭DB同identity补来源 |
| CLI _membership_input | 四字符串文档重建新对象，文档不带owner | 严格命令逐个实际capture并持有，各普通命令和四字段JSON保持 |
| storage_members._profile/_profile_json | 现有对象原样返回，映射重建来源未知，投影仍四字符串 | 需严格来源时在真实重建点新capture；不复用旧同值owner |
| storage_backups._profile_value | manifest声明校验对象 | 声明值不能冒充活源producer/commit owner；实际源capture另接 |
| API/线程池/worker/Cycle/StateStore | 当前普通profile实际持有链 | Task 6显式配对传同owner，constructor线程是历史，不代替后继操作owner；frozen DTO不临时挂属性 |

## 验证与边界

两设计审之后生成完整候选，两源码审及最终脚本/实际准备双审之后，才以新claim执行。新普通模型实际执行完整storage_admin模块，对config/db依赖显式合成，db.connect和正常服务/库入口一律不调用；实际StorageProfile、owner producer、Path/type-call/ContextVar从当前父候选选入，不用同名替身代替constructor主体。

具名覆盖：四显式路径与默认配置、部分参数None拒绝；实际constructor四输入/Cdict/profile/四Path/48调用和setter事实；外部caller先持owner的正常和constructor失败路径；PathLike预处理失败保持原异常且construct未claim；owner缺失/None/坏身份/重复消费；公开profile和copy/pickle同identity普通语义保持、无owner回填；不同调用独立owner；构造后跨线程只读facts；放弃owner/profile/外部异常后weak GC；不放弃原异常或owner时不提前GC。实际loader按全新package选入完整admin，既有scope-control两方向加载及其他受影响路径按风险选择，不机械重跑旧48/69/111/94/r919。

严格区分construct内部观察到的class返回、此新helper的实际返回和将来真正route caller接收；只按实际执行断言，不升级owner_facts中的helper_outer_return_observed或其他认证旗标。PathLike失败是普通预处理语义模型，不做profiling/frames/locals/停点/定点计时诊断。保持case30/parent360/matrix1680/native20–25–10/cleanup10/control1MiB/最多2pytest child，本批max1；监督用原Popen/finally/kill-wait与raw-first证据保全，工具摘要允许同一session原序分片，唯一末次退出另核。旧E001/源码/checker红保，不重放旧claim。

该阶段不能称Task 4–8、真实route/三个owned body、scope构造owner消费、pending容量/恢复/最终释放、原111/94/r919、Store/source_closed/live witness/写权、H02–H06/永久分享或完整Goal完成。正常库、服务/dist、既有ACL/settings、真实平台和Git保持；r920/r964耗不重复，r967待原问不执行不重问，P06历史0/7不再问。canonical/c973本段apply0/历史正式累计11、产品pass0，Goal保持active。
