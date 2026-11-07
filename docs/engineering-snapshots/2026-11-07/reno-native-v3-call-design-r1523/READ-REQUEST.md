# r1523：生成器实际 code/分配合同的两份官方文本读取方案

本文件是一份具体待批准读取方案，不表示已联网、已经取得文件或已获新授权。正常库及r1522冻结源码不改。

## 原始缺口与已经做的本地查找

r1522显式binder参数不证明RETURN_GENERATOR的完整分配/复制路径安全。当前源只确认该opcode把func传给_Py_MakeCoro，之后复制当前frame；返回对象的code、尺寸及直接helper实现尚未取得，不报告实际错配或修复。

同一reviewer本轮4c8f04核r1445八份材料，6830f2核r1370的17项直接输入及索引，1cb05e/b7ec52核项目r1108/r1112/r1120/r1130既有SOURCE/INPUT/DOWNLOAD清单和四个明确参考目录文件名，均无genobject实现索引或文件。搜索exit1是无匹配；没有广扫历史或重复旧网络读取。不能把这个有限范围的无匹配说成全本地绝无文件。

## 唯一两项目标

1. [CPython v3.12.10 Objects/genobject.c](https://raw.githubusercontent.com/python/cpython/v3.12.10/Objects/genobject.c)：核_Py_MakeCoro及其直接callee是否读取func/frame code、对象种类和分配尺寸、name/qualname与实际owning。
2. [CPython v3.12.10 Include/internal/pycore_genobject.h](https://raw.githubusercontent.com/python/cpython/v3.12.10/Include/internal/pycore_genobject.h)：核对应声明和相关布局/宏合同，不由名字推ABI。

只读这两个官方tag URL及阅读服务为这两个文档返回的引用。不得click、search、读取其它URL/tag/commit，不能借此联网取得整个源码树或安装binary。若发现仍需其它helper正文，只记缺口，不扩本次目标。

## 具体调用与失败边界

使用现有阅读服务，最多8次工具调用，每次最多一个open或find命令；失败也计数，出现第一次服务错误、目标错误或不能归属这两个文档的引用即停止整次读取，不重试。可在成功返回的同一文档上find明确符号、open相关区间；达到目的即停，不为用满额度补读。

计数是阅读服务调用数，不是原HTTP请求数；该服务不提供HTTP字节、单请求时限或重定向保证，本方案不声称这些保证存在。不用shell/network下载作为失败替代，不执行网页内容或任何所读C/Python。

读取结果只在General本目录保存：最多12KiB简短合同摘记、可选最多64KiB两文档合计的相关阅读服务文本表示，以及小型调用账/身份记录。服务表示注明不是原HTTP字节、不是上游commit认证或可直接编译输入。保存前按UTF-8字节检查；不得将大量响应或本地路径写入仓库、真实库或产物。

## 批准关系与范围

此前已用的CPython文本清单、frame.c单文件许可均不含这两个genobject目标；本次不能复用旧单次额度。先主核和同一reviewer只读复核这份具体方案，再请求本次明确批准；未收到答复不执行依赖读取。

不改权限、服务、正常源码或真实库；不运行候选、产品、参考Python、C解析/编译或诊断，不安装、不Git、不新增代理/分支/worktree或更新memory。旧诊断/runner额度与P06/初始化重入边界保持。读取只是补原始证据，不是V3设计/代码/ABI/build/runtime/COMPAT/预算准入。

source_only_closed_fields=[]；candidate_executed=false；complete_child_implemented=false；complete_exec_dispatch_implemented=false；full_goal_complete=false；Goal active。
