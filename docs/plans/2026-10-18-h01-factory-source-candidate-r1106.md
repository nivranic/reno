# r1106 私有factory首次来源运输完整候选

r1106已生成私有factory首次来源运输的159完整候选，三改156继承；24个私有协议变化与其余399个原code保留均已核对。源码双审本次变换0、完整认证仍各1红，候选未安装，Goal active未完。

## 实际完成

源码父为r1104 SOURCE01，设计为r1105 DESIGN02。24个原私有factory（control10/scope8/registration6）原def前移至原module首次源码段，唯一新增显式context参数，首capture改读实际参数；原factory body及公开函数/class仍在原调用时刻创建。首次原function与code/globals/builtins/closure五项事实由原direct call参数强持进入constructor，entry先删constructor、再逆序删本模块早期factory aliases，原位置从held原对象发布。

24个实际caller均保一次原alias lookup，以held原function身份门及code审计后的readonly字段、完整exactstr key扫描核对，再从实际fastlocal显式传原context调用。实际return先入新fastlocal，再作原result STORE。helper保原global中间return、context后读、fact[4]、锁内_make_core STORE及effect/success原序；完整原9句及锁finally包含在新caller cleanup之内。新caller19槽预绑None、每轮key finally及逆序清理；最外try覆盖entry删除和全部原body/tail，原锁finally先退、caller临时根再清、held facts再清，原context参数随后随frame退出。

builder01的生成说明把caller与原锁finally顺序写反，独审1红；实际源码嵌套正确。Root初0漏审以ERRATA保留。builder02仅三处确切替换（顺序文案、读取02合同、读取两份02审查），全文可逆回01，双审0后单次供给成功。21个构建输入34321722B核同；01从未执行，02未重复供给。

源码复审另发现CODE-CHECKS01四个positions_equal错误：JSON列表与内存元组直接比较，误把四个实际未变项标False。SOURCE01及两边红审/Root漏审原保；仅由已保存父/候选raw JSON重算429项，修四个False到True，生成CODE-CHECKS02和SOURCE02表征，逆改四项可逐字节恢复01。159实际源码、raw及编译输出完全不变，新增compile0；本批425实际位置变化统计保持。

## 静态验证与编译边界

159组三方477pin、158份Python AST、三份完整ndiff双向全文恢复及完整Module AST逆像核同；三改156继承。仅三候选syntax compile、父0，三角色raw marshal/完整JSON先保存后作metadata比较，warnings0。复审新增compile0，不load marshal，不导入或执行候选、不跑局部bootstrap/control模型、QA、旧绿/F001或诊断。

429全部code包含423原code与六个module/constructor scaffold。24私有factory由0入参变1入参、加原context fastlocal并去掉各自CO_NESTED；其余399个原code（55/290/54，匿名13/156/18）ABI/qualname/flags/rawcode/names/完整typed constants/异常表逐对象核同。保留同name/qualname/原首行的有序多重occurrence，不以去重字典或全目录flat zip代替映射；24factory父显式重挂module，后代parent与原const ordinal保持。429项IMPORT顺序与数量核同，不声称offsets相同。

相对原r1100基线，匿名qualname差仍8处（3/3/2），CO_NESTED差实际44处（12/22/10），原68已减少24。相对r1104，位置表变化65/299/61共425个code（包含module/constructor），原raw JSON保存全部位置项；不把字节码核同冒认为位置表或运行等价。原17lease/1449slot/406尾、四held读取及五S002通过Module逆像保持。

## 未闭合的工程工作

首次P0阶段新增24个code getter均有audit；实际caller正常全路径再24个。P0未受GC/audit/其他回调污染仍是明确且未作运行证明的前提，五项事实tuple自洽不证明真实原source。初始化余体、origin/failure/audited-getter及完整EDGE/PHASE认证仍未闭，源码双审保完整阻塞1；本次变换0不折算完整认证0，不得import/QA/安装。新tuple/局部根/字典扫描成本已列，未证明原control1MiB容量、实际内存或时限改善。

保护56同18lstat缺席，本批canonical apply0/历史11、产品pass0。正常库/service-dist/ACLsettings/真实平台/Git保持。r920/r964单次耗不重放，r967待原问题不执行重问，P06历史0/7不再索材料。原case30/parent360/matrix1680/cleanup10/control1MiB/并发1/最多2pytest children/native20–25–10保持；禁profiling/frames-locals/停点/定点计时/新异常链诊断/runtime journal。Task9A9B、原111/94/r919、真实Store、P06/条件T15/生产云及完整Goal均未完，非只剩验收。

## 24项实际候选坐标

| 模块 | 原私有factory | 首次def行 | 实际call行 | 原result STORE行 | result目标 |
| --- | --- | ---: | ---: | ---: | --- |
| control | `_AUTH_CATALOG_DEFINE_FAILURE_CLASS` | 58 | 13339 | 13340 | `GuardControlError` |
| control | `_AUTH_CATALOG_DEFINE_fail` | 417 | 13436 | 13437 | `_fail` |
| control | `_AUTH_CATALOG_DEFINE_function_pin` | 653 | 13533 | 13534 | `_function_pin` |
| control | `_AUTH_CATALOG_DEFINE_check_function` | 1249 | 13630 | 13631 | `_check_function` |
| control | `_AUTH_CATALOG_DEFINE_make_class_authentication` | 2462 | 13825 | 13826 | `_make_class_authentication` |
| control | `_AUTH_CATALOG_DEFINE_bind_budget_class` | 4732 | 14028 | 14029 | `_bind_budget_class` |
| control | `_AUTH_CATALOG_DEFINE_authenticate_class` | 5538 | 15129 | 15130 | `_authenticate_class` |
| control | `_AUTH_CATALOG_DEFINE_dictionary` | 6846 | 15226 | 15227 | `_dictionary` |
| control | `_AUTH_HELPER_DECLARE_CORE` | 8511 | 15368 | 15369 | `_hc_actual_factory_return` |
| control | `_AUTH_CATALOG_DEFINE_bootstrap_scope_owned` | 9613 | 15696 | 15697 | `_bootstrap_scope_owned` |
| scope | `_AUTH_CATALOG_DEFINE_fail` | 44 | 11065 | 11066 | `_fail` |
| scope | `_AUTH_CATALOG_DEFINE_make_origin_profile_paths` | 179 | 11163 | 11164 | `_make_origin_profile_paths` |
| scope | `_AUTH_CATALOG_DEFINE_origin_profile_functions` | 407 | 11264 | 11265 | `_origin_profile_functions` |
| scope | `_AUTH_CATALOG_DEFINE_authenticate` | 553 | 11796 | 11797 | `_authenticate` |
| scope | `_AUTH_CATALOG_DEFINE_origin_authenticate` | 1250 | 12902 | 12903 | `_origin_authenticate` |
| scope | `_AUTH_CATALOG_DEFINE_make_origin_core` | 1690 | 12999 | 13000 | `_make_origin_core` |
| scope | `_AUTH_CATALOG_DEFINE_initialize_origin_owned` | 4528 | 13105 | 13106 | `_initialize_origin_owned` |
| scope | `_AUTH_CATALOG_DEFINE_initialize_owned` | 5670 | 13203 | 13204 | `_initialize_owned` |
| registration | `_AUTH_CATALOG_DEFINE_fail` | 35 | 5102 | 5103 | `_fail` |
| registration | `_AUTH_CATALOG_DEFINE_marker_bootstrap_facts` | 170 | 5225 | 5226 | `_marker_bootstrap_facts` |
| registration | `_AUTH_CATALOG_DEFINE_stack_class_pin` | 967 | 5322 | 5323 | `_stack_class_pin` |
| registration | `_AUTH_CATALOG_DEFINE_check_stack_class` | 1484 | 5419 | 5420 | `_check_stack_class` |
| registration | `_AUTH_CATALOG_DEFINE_authenticate` | 1989 | 5516 | 5517 | `_authenticate` |
| registration | `_AUTH_CATALOG_DEFINE_initialize_owned` | 2805 | 6095 | 6096 | `_initialize_owned` |

# 后续工程入口

源码父为本批SOURCE02表征的完整159，文档父为本批DELIVERY。两份SOURCE审的本次变换0与完整认证1分列；source/execution均False，不能以交付材料审0替代源码准入。三候选compile已消耗，不重供给；旧r1102六+r1103六+r1104三另列，不重复。

1. 先读本批两份源码审与ROOT-SOURCE-MATRICES01，围绕仍红的initializer/origin/failure/audited-getter和完整EDGE/PHASE建立明确剩余边清单。使用实际159源码及已有目录，记录原producer/事实/actual alias/call/return/STORE/释放和阶段；不把已完成24factory运输重新列成待实施，不用tuple自洽认领原身份。
2. P0真实首次source无污染仍是条件前提，不能延伸到后续body/callback；原24code audit与caller另24code audit必须分开。针对剩余实际可变读取给出具体source位置及反例或明确边界，先修实质源码问题并复审，不用历史静态通过或位置差异作完整认证结论。
3. 仅在完整认证源码获双审准入之后，才准备完整包QA脚本及实际输入的双审和新claim；当前不得跑局部control/bootstrap模型、旧绿/F001或任何候选导入。保原导入顺序、skip/False/deferred/repeated分支以及17lease/1449slot/五S002原边界。

不操作正常库/service-dist/ACLsettings/平台/Git，不重放r920/r964；r967不执行重问，P06用户无历史材料，不再索取。原case30/parent360/matrix1680/cleanup10/control1MiB/并发1/最多2pytest children/native20–25–10不变；禁profiling/frames-locals/停点/定点计时/新异常链诊断/runtime journal。Task9A9B、原111/94/r919、真实Store、P06/条件T15/生产云及完整Goal active未完。

## 已定位的后续静态入口

下列五项来自独审01的实际候选坐标，SOURCE02只修材料，源码行不变。已确认的是源码顺序；仍待证明的是来源/审计后freshness和完整EDGE/PHASE覆盖，不冒称已证可利用缺陷。两项inner local factory已有词法来源，先核清其已有保证，不重复24项已完成运输。

| 模块 | 原定义 | 候选code index | 候选行 | 调查重点 |
| --- | --- | ---: | --- | --- |
| control | `_check_function` | 12 | 1256、1351/1368/1385/1402、1445–1448 | 实际pin receiver；逐code/defaults/kwdefaults getter audit后owner/record/slot/current是否仍新鲜，再到原continuation |
| control | `_AUTH_CATALOG_DEFINE_class_pin` | 17 | def2722、capture2723、return3560、call3561 | 已有局部词法来源、callback、真实return/publication，不预判local call不安全 |
| control | `_AUTH_CATALOG_DEFINE_check_class` | 20 | def3564、capture3565、return4602、call4603 | 同上；独立核原class元数据/实际调用来源 |
| scope | `_authenticate` | 19 | 619、`_PINS[2]`626、getters629–632、helper651/886 | 实际条目及连续audit之后的freshness，再到原helper消费者 |
| registration | `_check_stack_class` | 10 | 1569/1571、rows1574、functions1578、getters1809–1812 | 实际class/mro/rows/functions的接收、fact生命周期及helper return/effect |

三源路径均为本目录`source-01/candidate-full/reno/personal_authority_guard_{control,scope,registration}.py`，准确源码pin见SOURCE02。五入口并非完整认证剩余边的穷尽证明；下一批先以这些具名原边作有限静态核对，发现具体缺陷才设计改动。
