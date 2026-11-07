# Task9A 原锁标记计费与scope存在性修订03 Implementation Plan

> For Codex: 继续 executing-plans 和同一只读复审代理。01、02及其红审不覆盖；本03仅优先修订owner缺席判定，三处begin/read/原异常包装等02设计继续适用。

**Goal:** 即使原owners locator与ambient同时丢失，仍阻止已存在strict操作降级为legacy，同时避免访问或借用foreign owner。

**Architecture:** 在原scope工厂内增加独立弱presence否决记录，记录原claim尝试所属执行ownerkey与原custody的弱身份。它只能证明“有原尝试，必须拒绝无来源入口”，不能认证scope/成功/结束或作为新授权。positive准入仍走原Call/owners partition/claims/budget/group/current链。

**Tech Stack:** 原scope工厂闭包、原custody owner弱引用、ReferenceType、原pending RLock、固定标量metadata与独立原presence行；不新增ContextVar、持久库或资源操作。

## D003与原红

02及OWNER说明01的no-preferred分支不充分：已注册scope仍由原partition/claims/budget/group/raw保留，仅丢owners.index[currentkey]与ambient时，对未知lock的direct调用看不到locator，不能因此判legacy。_table_data只核容器身份，不证明key完整性。02独审D003=1，保01两红和Root漏审。

本修订撤销说明01中“ambient与locator都空即可进入最后legacy确认”的充分性。此后还必须核独立原presence的完整性与原不存在事实；不能只因新presence表的快速索引没有key宣称没有strict。

## 1. 原claim之前建立负向存在记录

在scope工厂保存私有presence原行集合及按ownerkey的索引，两者核同；原行仅含固定标量ownerkey、原custody弱引用和本次唯一nonce，不含scope/group/budget/task/异常等强引用。

原claim完成原exact budget/group与重复budget/group初筛后、分配第一个Scope或写任一scope索引之前：

1. 从已持有的原Call取得原custody，按已pin原owner getter观察一次实际执行ownerkey。该观察独立记录，不替代原claim稍后真实owner getter或其returned事实；稍后两个实际key必须核同。
2. 先建立本次负向原行及弱custody身份，再关联其ownerkey索引。两次登记任一步失败即停止原claim，不分配Scope、不继续新业务操作，也不删掉已确认的部分负向行来重试。
3. 冲突不得覆盖既往原行。相同execution ownerkey可能保留失败尝试；原scope.claim的既有OWNER_BUSY语义仍在原位置执行，新presence不能替它授予成功。相同原custody的已保留presence可作为同一个否决事实复用，不创建新的root或独立budget。
4. presence登记完成后，原scope claim的原顺序、部分根记录、原索引/slots、authenticate/control claim和set/reset逻辑保持；最后检查原scope实际ownerkey与本次presence原观察同。

独立原行和快速索引要认证类型、nonce/weakref原身份及对应关系。单独删除/替换一个索引、等值替换原行、未知部分行、弱ref指向不同custody或任何矛盾均拒绝；不能把损坏项忽略成不存在。若登记在原key确认前失败且留下未知key的原partial行，后续不能证明其归属，安全拒绝相关新marker入口，不扫foreign scope或补填key。

## 2. 弱回收不能等同业务退休

presence只弱持原custody，不新增永久scope/owner/task强根。已存在Scope的原custody_lease、pending/caller/载体的原持有关系会维持该custody；只要任何此类原强链存在，弱presence不能自行失效。

弱回调只携原弱引用、标量key与nonce，在原RLock内先核当前原行和索引仍指向同一条目，再删除该条目的metadata；不得移走后继条目或闭合业务债务。存在部分/损坏状态时保留否决或unknown，不能凭弱回调修复成legacy。实际custody已回收意味着不存在仍强持其原lease的有效Scope；这只允许清弱metadata，不颁terminal/root-release/费用完成证明。

presence自身不保历史身份墓碑：明确原custody回收且元数据核同后移除其对应原行与索引；不以id复用认新scope。原pending/Scope图的寿命和清理合同不改。

## 3. 查询与明确legacy

查询仍使用02和说明01的原Call.preferred/RLock前置读方案，不直接get未知locator、不values扫描、不keep foreign：

- 已有preferred：先核该Call原state、presence原行/索引与同一原custody、自己的原owners partition和key locator。全部吻合后才允许原Call.get读取已存在的该key；缺locator但原partition有key直接拒绝。原by_scope/raw/owner/current继续认证，Token不旋转。
- 无preferred：先仅核本current execution ownerkey的独立presence及其原行/索引完整性。若存在原活跃或partial尝试，直接拒绝；不解引用scope、不keep其custody。即使ambient与owners locator均缺，也不能进入legacy。
- 只有presence原行与索引经完整性核对均不存在本key或unknown partial、ambient原无、原ownerkey locator原无，且resources独立确认本lock没有strict原来源，才明确legacy。presence缺失单独不构成无strict证据。
- 任一metadata无法确认、presence指向foreign、原行与索引矛盾或bootstrap就绪状态矛盾均硬拒绝。真实bootstrap尚无claim、明确legacy的路径必须保留；不能简单“无preferred一律拒绝”破坏旧合同。

不扫描foreign scope、原scope字段或foreign owner state。必要的presence原行完整性遍历仅是固定标量key/nonce/弱引用metadata，不调用foreign scope getter、observe/keep或loan；具名负例核foreign原state和loans保持。原行集合是固定claim producer写入的原事实，不能接受caller提供“无scope”票据；本段仍不宣称防御任意同时销毁全部独立原数据的Python反射。

## 4. 候选和必要验证

修改文件仍为未安装scope/resources/lifecycle三份完整源码，其余34份继承r1055；全部来源/逆向/diff/静态compile和源码双审齐全后才执行新case。03、说明01、原02红报告和新两设计审全部精确pin；旧保全pairs不覆盖。

在02全部矩阵上增加：

- 已有strict scope，owners locator丢失+ambient丢失+unknown direct lock+无parent：begin拒绝、legacy/read/getter0，无foreign keep。
- presence快速索引单删、原行单损坏/等值替换/指向foreign、索引有行无/行有索引无、未知partial、登记第1/2步失败：全部保原否决或unknown，不继续claim/read。
- 原合法claim后presence原key与原scope实际ownerkey一致；不替换原owner getter返回事实，不重复发claim。
- 活Scope/原lease/未决pending存在时执行GC，弱custody不得消失；仅该原custody强链解除并实际回收时，正确弱条目移除；旧回调不删新条目。GC检查不冒认业务退休。
- 无scope真实bootstrap与三处明确legacy保原lookup次数和异常；不同线程/task的legacy不因无关scope被全局误拒绝；原foreign loans/state保持。

02的D001最早dynamic前begin、D002原filesystem映射后caller first、唯一32请求与实际len结算、部分失败/旧根/原清理/未安装及全部限额均保持。Task9A不认证Task9B动态全目录/物理或完整费用，也不开放publication。canonical/c973本段apply0、历史11、产品pass0；完整Goal active未完成。
