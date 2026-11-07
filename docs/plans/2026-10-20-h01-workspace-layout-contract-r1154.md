# r1154 有限后继方案：工作区字段类型、尺寸递推与原合同范围

状态：经Root及同一代理有限复审，登记字段与算法子方案；完整layout、平台合同、源码与执行均未准入。父r1153封存不改，产品仍r1106 SOURCE02完整159文件／7,956,657 B，未安装；B001/B002 open2，完整design/source/execution/Goal四False，Goal active。

## 1. 本批解决什么

r1153已列工作记录，但“必要原call结构scalar”和“按真实sizeof/align”仍不足以直接实施。本稿为每个已知字段指定C类型，并给出不依赖指针位宽、无隐藏变长数组的checked尺寸递推。JSON只是文档schema，不生成C源码、不编译、不调用平台或Python内存API；本批没有数值sizeof或ABI实测。

平台free另按原文区分：必须结清本方责任，不等于必须先证明任意OS故障后仍零物理泄漏；缺少合法失败反例也不等于free必成功。正常free可采用合法API域／可信原语／匹配build的显式前提，但这些前提尚未在本方平台入口闭合。失败返回不能仅凭记录或报告就当作allocation责任已结清。

## 2. 字段类型及完整记录集合

完整逐字段清单在WORKSPACE-SCHEMA-01.json。owning字段一律为独立`PyObject *`槽；same pointer的两次取得仍两笔。每个记录先声明表中的owning字段（按JSON顺序），再按JSON fields顺序声明scalar/nonowning字段；未来C实现不得偷偷改变或增加字段后沿用本尺寸式。

| 记录 | owning字段数 | 其余字段类型及数量 |
| --- | ---: | --- |
| WorkspaceHeader | 0 | 19个size_t；3个unsigned char |
| DispatchCapture | 10 | 6个size_t；5个unsigned char；1个vectorcallfunc |
| KwCapture | 3 | 2个size_t；1个unsigned char |
| CellCapture | 2 | 2个size_t；2个unsigned char |
| ClassEntryCapture | 3 | 4个size_t；1个unsigned char |
| BaseCapture | 3 | 6个size_t |
| ClassCapture | 3 | 4个size_t；2个unsigned char |
| LedgerEntry | 0 | 1个非拥有PyObject ** |

所有counts、indices、ranges、byte offsets和capacity使用size_t；boolean/role/phase用unsigned char及本方固定小常量，不能以C enum的实现相关大小替代后仍沿用原sizeof。function_domain区分named六行与dispatch；property三accessor、SM、CM、plain分支role分列。role不作来源证明，所有index使用前按其实际domain/range核界。

函数指针用匹配头声明的`vectorcallfunc`，绝不塞进void*、uintptr_t、size_t或PyObject*。继承的v3.12.10 funcobject.h显示optional字段的NULL以及独立vectorcall字段；该头不是其它版本/build的ABI证明。原call结构不再使用未定的“必要scalar”占位：本工作区只新增original_vectorcall；函数行已持actual code/defaults/kwdict/closure/G/B。参数结构仍沿原actual code根及原不可变链使用，由版本化binder/D1/D2合同核，不另缓存一份未知CodeShape，不新增name/qualname/annotations/func_version拒绝谓词，也不声称binder全域已闭。若后继确需新增副本字段，必须先修订schema、尺寸与COMPAT。

非拥有LedgerEntry.slot_address只指向本region内上述六类记录的owning字段，不指向固定arena或任意外部对象。此C槽地址不新增PyObject引用；region在ledger最后一次读取之后才能free。NamedRanges[6]是固定自动记录（4个size_t＋1个unsigned char），ClassRootMap[5]是固定自动记录（2个非拥有PyObject **＋1个unsigned char）；它们不在region中，不冒称零栈/峰值成本。不另建D/B/E等增长的index数组。

## 3. 尺寸与对齐：完整符号算法

记s(T)=未来匹配C build的sizeof(T)，a(T)=其真实alignof(T)。不使用pack、位域、灵活数组成员、手写32/64位偏移或“指针都是8字节”。所有s/a为正size_t值；平台原始base须满足这些T的最大对齐，尚待实际build/平台核对。平台页对齐不能未经核对就替代该条件。

计数维度沿r1153：C=5、F=checked_add(6,D)、0≤P≤Z；D/K/Z/E/B保完整occurrence，不能去重或设任意固定cap。R按checked乘加得到10D+3K+2Z+3E+3B+3C。R只计region声明owning槽，不计固定arena、独立C local、隐含type根或frame内存。

每个操作先验后算：

1. checked_add(x,y)：仅在y≤SIZE_MAX−x时计算x+y。
2. checked_mul(n,s)：s>0，先核n≤SIZE_MAX/s再计算n*s。
3. align_up(x,a)：a>0，r=x%a；r=0则padding=0，否则padding=a−r；用checked_add(x,padding)。不假定a为2的幂，不先溢出再掩码。
4. 初始cursor=s(WorkspaceHeader)。按Dispatch、Kw、Cell、Entry、Base、Class、Ledger顺序，对每类T/n执行：offset=align_up(cursor,a(T))；bytes=checked_mul(n,s(T))；cursor=checked_add(offset,bytes)。七个offset写回header，requested_bytes=最后cursor。

零count仍经过相同align步骤，offset可以与相邻空区域重合，但不得解引用。allocation取得requested_bytes；只有成功原base、实际长度和全部offset范围成立后，才在各T的对齐位置初始化。owning槽用实际C NULL赋值，scalar逐字段初始化，不把OS零页或全字节memset自动当作跨ABI NULL/函数指针零值证明。original_vectorcall在捕获前由实际匹配类型的NULL函数指针初始化，成功后写真实原指针；未捕获值不消费。

size_t聚合计数与Python tuple的Py_ssize_t参数分开。原dict/MRO/closure实际长度先验证非负再安全转size_t。每个将被构造的tuple长度单独验证可表示于Py_ssize_t：某行kw_count/cell_count、某base entry_count、某class base_count/dispatch_count及固定2/3/4/9等。不得因总K或总Z超过PY_SSIZE_T_MAX而直接裁掉各输出仍合法的原域；总量受checked bytes与实际分配约束。raw字典扫描游标是独立Py_ssize_t自动变量，不塞进unsigned持久字段、不把它当条目ordinal。

## 4. 容量、索引与取得顺序

count→allocate→capture仍以同一明确无Python-callback/GIL释放段为条件；本稿不通过“多核一次数量”补此条件。每个range满足start≤total且count≤total−start，索引满足index<count；不用start+count的先溢出表达式。D按class/base/entry/accessor原次序排列，K/Z按实际named/dispatch行各自顺序排列，禁止按identity合并。

R个ledger位置在分配时已备足。每笔NewRef前先核目的槽NULL、ledger_count<R、地址在正确记录的owning字段集合内；随后同一raw段取得actual引用、写槽、登记槽地址、加count。NULL absence不登记；每槽最多取得一次，move后槽置NULL，不重新填充。由此ledger_count≤R不依赖失败后再分配登记空间；不以瞬时refcount或指针重复抵消ownership。后继回调只在可再入准备/清理段，保持module_guard及bootstrap lease，回到raw认可点重核实际source/phase，绝不从callback后的current补T0 expected。

五个class_snapshot新增目的槽须同时把已知固定ledger的id域和容量103扩到108，并列五个新id；不是仅把“五根”写在说明里而继续用103容量。这个108只计既有103＋五个快照，不包含尚未展开的其它source/type字段。kw/cell/pair/class等子tuple全部完成后才进入外层，原borrowed cell另NewRef才move；内部NULL缺席转Python row9时另取得实际owned None，沿r1153分账不改。

自动责任还包括：creation_module_guard、PathFinder原MRO guard、bootstrap lease、allocation原base/长度/释放状态、actual first、当前构造返回temp、raw读取借用局部、字典游标和固定六/五映射。它们各有独立退出责任；本表只闭region字段集合和尺寸递推，未给完整C栈布局、原103全字段的native大小或整个heap上界。

## 5. 正常free、失败及原合同范围

free前先结束所有PyObject责任的真实release或合法move，最后一次读ledger后将原base/length运输到独立C scalar。正常平台free只以实际原allocation和API合法域调用一次；成功后不再读region。不能把CPython默认free忽略返回或“持GIL”当成本方完整证明，也不以缺合同制造一个已确认的合法失败。

有实际first的路径依r1135：本层实际first独立C拥有，所有实际cleanup在最终restore之前；无first的成功/准备失败路径另列，不能伪造first。平台API返回的status/OS错误数值不天然成为Python pending错误；本方wrapper若构造错误，必须另列实际effect及first/NULL后置条件。free失败后allocation若仍存在，责任没有因槽清空或报告消失，不能移入永久quarantine、无限重试或后台清理。本批没有选定能在该失败分支真正结清责任的算法，因此完整free合同与源码准入仍False。

r1075 §七145行确有“不插入新的时钟、计费、I/O、资源操作或用户callback”，不能只引165行“无新clock/I/O/计费”就说原文不存在资源限制。145行位于前callee正常返回／直接抛错与下一边认证的上下文；23/37/39/127行又明确要求bootstrap owner与完整原行存储。两者合看不支持“禁止一切内部准备内存”，也没有给新增平台工作区全面豁免。P0与初始化仍在原合同内，具体COMPAT需沿实际事件表证明：新增准备发生于何处、是否改变业务clock/I/O/计费/资源callee/次数顺序、实际分配失败时原attempt是否仍0、回调与退休如何处理。不把分页“零I/O”或OS行为无影响当已证，不静默修改原合同。

独立合同初报遗漏145行已由Root发现并要求更正，原初报与纠正分别保留在本批证据；不得将初报当最终原文结论。当前无平台实际调用、无新增诊断、无新下载；POSIX两页既有403终态不重取/换址，支持平台/Python>=3.11范围不收窄。

## 6. 后继明确动作与边界

本批若经两审，可登记的有限选择为：本schema字段类型、七数组尺寸递推、逐输出Py_ssize_t域、ledger先验与108已知id域；实际types的build映射仍待。平台入口/无callback/ABI与free失败责任尚需具体证据和算法，不因本批符号式而批准完整容量。其后还要接完整source/state、五快照实际producer/consumer、真实caller/restoration、P0/binder/D1/D2、一般NULL/Init/free、SCC/N4、全预算与COMPAT。

原17lease/1449slot/406尾、case30/parent360/matrix1680/cleanup10/control1MiB/concurrency1/max2pytest children/native20–25–10等限额保持；不运行旧绿检查。r920/r964已耗不重放；P06历史0/7不再索材料。Task9A/9B、原111/94/r919、真实Store、P06/条件T15/生产云和完整Goal仍有工程，非只剩验收。本批静态schema校核不计产品测试通过。

## 交付与继续实施

本批Root与同一代理仅批准记录字段类型、符号尺寸及ledger子方案。完整平台合同与实现未准入，先补实际原平台入口/无callback/ABI及free失败剩余责任，再接完整state/source/caller与五快照消费。初报遗漏已保留并纠正，不改原合同。Task9A/9B及完整产品Goal仍有工程；静态文档校核不计产品测试通过。
