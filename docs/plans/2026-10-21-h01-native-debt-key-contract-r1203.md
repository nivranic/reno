# I07：共享native debt键合同修订方案（r1203）

状态：Root设计草案，待同一只读代理事前复审；尚无本批产品候选、安装、编译或运行。用户已选A，本文仍保留原认证和内部资源生命周期要求。该修订只解决两个原producer与两个validator的键域冲突，不把完整I07或Goal标为完成。

## 问题及实际依据

operation第54/56行创建并固定五个原dict；directory第316行和source第335行均捕获其中index4。directory向其写`id(resource_key)`，source向其写`("source_unit", id(unit_key), id(holder_key))`。r1198的source修订保留该key，只改善发表成功后的提交位置等记账。

operation `_registries_original`第848–860行仅给index1/3 actor tuple域，其余含index4只接受exact-int且非负；directory retain的原第711行及r1198第718行也要求所有既有key为exact-int。因此原source retain实际put成功后，下一次执行原registry检查会先报REGISTRY_CHANGED，不能自然进入_unknown_original的UNSETTLED检查。此为源码条件推导，没有执行该路径，也没有将其归因为历史State/request失败。

已经运行中的source cleanup可有多个既存holder，不能把“tuple后新directory入口会被挡住”偷换成“不会影响后继cleanup”。具体可达顺序和前提以同一只读代理的完整盘点为准，不通过示例或诊断补证。

## 选择的最小修订

保留两个producer的原key、value、锁/原写入时点及r1198提交修订；只让operation index4和directory retain接受一致的有限联合域：

1. exact-int且>=0；
2. exact-tuple，长度3；第0项exact-str且值为`source_unit`，第1/2项各为exact-int且>=0。

其它registry域不变。bool不是exact-int，int/str/tuple子类不被接纳；未知tag、错误长度、负数、其它对象均拒绝。先exact-type再读长度/位置/数值；不对未知对象求hash、比较或遍历其内容。正常的`_unknown_original`仍因任何native debt非空报UNSETTLED，不能以key合法放行新业务。

不把source改成单id key：原namespace和unit关联保持，避免无必要改变原证据身份。不改payload或为其授信，不接受caller DTO，不增加raw/fd/owner入口。不新增跨模块helper或新的可替换callable；两个既有validator原位写相同grammar，免于扩大本次原source/ABI运输接点。grammar的两处重复是有界权衡，后继维护必须同步审查。

directory旧局部validator只检查int而不查非负；本次与operation既有非负要求统一。实际directory producer的id本就非负，不删除其原合法产物。原已存在的负key和未知结构仍视为外部不合法状态；不把新grammar称为来源证书。

## 文件责任与组合

本批若设计双审通过，建立三个完整文件的局部候选组合：

| 文件 | before | 本批改动 |
| --- | --- | --- |
| personal_authority_replica_operation.py | live 164318B，SHA256 74b55bbac5193659b3f25b72f1247cd15c23af67251887550040649654d8def5 | 仅_registries_original的index4分支 |
| personal_authority_replica_directory_native.py | r1198 candidate-02，73278B；准确hash待固定输入表记录 | 仅retain中的原debt键校验，继承r1198其余完整修订 |
| personal_authority_replica_source_native.py | r1198 candidate-02，132027B，59a8c270cc1d2012a115fb9baf059207b9360c1d27bfaa21026ceec608512f91 | 全文原字节继承，实际namespace producer不改 |

before完整保留且候选未安装。三文件只是本次共享schema的局部组合，不是完整159源码候选、不含已实现的owner资源适配，更不是可独立安装版本。未经完整H01及原准入不得覆盖live。既有约960份未提交变更保留。

## 必须审查和后继验证的行为

| 输入/路径 | 要求 |
| --- | --- |
| 原非负int key、空dict | index4校验通过；其它registry原行为保留 |
| 两种key共存 | 两个validator接纳同一有限域，原source tuple保持不变 |
| 合法source debt非空 | _registries_original不误报REGISTRY_CHANGED；_unknown_original仍拒绝UNSETTLED，不启动新业务 |
| bool、负int、未知tag、错误长度、子类key、非exact-int成员 | 拒绝，且不对未知对象执行新增hash/eq/iter |
| 原容器alias被替换 | identity和exact-dict门保持，不因新key grammar接受替换dict |
| 其它index的source tuple | 仍拒绝，不能把index4的新域扩到live/closed/actor/windows |
| 已有source tuple时本方已捕获directory cleanup发表 | 键域本身不再阻断int debt发表；其余合法state、锁、分配和first前提均仍需要 |

本批只做实际源码全文审查、固定输入身份与有限文字/字节核对；不执行候选、产品、AST/compile/import、测试、collector、native或诊断。上表是后继验证要求，不是已执行用例或通过数量。既有test_registry_alias_fault_preserves_caller_container_and_retires_original只覆盖alias替换，不冒认它已覆盖该联合域；本次未运行它。

## 仍未解决与回退

本修订不解决发表分配失败、id复用/重复证据、持续OOM、未知close/lease结果、未发表证据跨退休保全、source/direct原字段暴露或N4/SCC等问题。原永久gate、实际first和不重关/不退款/不重放保持；A也不弱化这些本方义务。

未安装所以无需执行回退。若候选复审不通过，保留红稿并修订候选；不reset/clean仓库、不写真实库、服务、dist、权限/settings或Git。原运行限额/截止及已耗r920/r964不续期，r967不执行。
