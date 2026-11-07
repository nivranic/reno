# r1155 有限后继方案：CRT替代路线否决与Win32逐出口协议

状态：经Root及同一代理有限复审，登记局部协议子方向；完整平台合同、源码和执行均未准入。父r1154封存不改，产品仍r1106 SOURCE02完整159文件／7,956,657 B未安装；B001/B002 open2，完整design/source/execution/Goal四False，Goal active。

## 1. 新证据改变的选择

调查“模块私有release CRT＋malloc/free可否消除VirtualFree失败责任”后，决定不以它替代直接OS路线。Microsoft明确说明静态链接可以隔离CRT state（包括new-mode/handler），但free文档仍允许释放错误写入errno。本机SDK 10.0.26100.0的完整free_base.cpp:98–109显示：NULL直接返回，否则HeapFree失败只将GetLastError的映射写errno。void返回不是成功见证，不能因C标准接口没有bool就从责任表删除失败分支。

同版heap_handle.cpp:13–43还显示CRT初始化用GetProcessHeap，独立CRT state不等于独立OS heap。malloc_base.cpp:23–47在HeapAlloc失败后依据_query_new_mode决定是否_callnewh并重试；malloc.cpp/free.cpp分别有debug/patchable wrapper。故/MT即使隔离本模块new-mode，仍未消除底层free失败，也没有自动闭合原入口、hooks/原型、callback与pending。此次不新增CRT依赖或更改现有build设置，SDK源码是本机安装内容，未证明它等于未来链接库或所有平台版本。

依据：[CRT global state](https://learn.microsoft.com/en-us/cpp/c-runtime-library/global-state?view=msvc-170)、[free](https://learn.microsoft.com/en-us/cpp/c-runtime-library/reference/free?view=msvc-170)、[malloc](https://learn.microsoft.com/en-us/cpp/c-runtime-library/reference/malloc?view=msvc-170)、[CRT libraries](https://learn.microsoft.com/en-us/cpp/c-runtime-library/crt-library-features?view=msvc-170)。页面观察仅短paraphrase，完整本机输入由SDK-INPUTS-01.json及gzip保存；没有下载CRT实现或执行它。

## 2. 直接Windows API的真实声明与条件分支

本机SDK的memoryapi.h:49–61为desktop/system/games声明LPVOID WINAPI VirtualAlloc(LPVOID,SIZE_T,DWORD,DWORD)；:78–96另在app/system/games分区声明BOOL WINAPI VirtualFree(LPVOID,SIZE_T,DWORD)。winapifamily.h的desktop family同时满足APP分区；不能只见第二处APP就误判desktop不可用。

同一memoryapi.h:760–821还有非desktop/system的APP条件inline：VirtualAlloc实际委派VirtualAllocFromApp。故不能仅凭名字或一段原型证明最终入口。拟Windows desktop实现使用匹配Windows头的原声明，显式核选中的API family/_WIN32_WINNT与include分支，不能手抄__cdecl替代WINAPI，也不通过Python/ctypes、当前可写alias或GetProcAddress查一个同名函数造原根。apisetcconv.h:20–26还使WINBASEAPI是否import取决于_KERNEL32_等宏，真实build/import表仍须匹配。其它Windows family、架构、Python>=3.11和非Windows支持域不因这条desktop子路线被删除，仍须各自闭合。

minwindef.h分列DWORD为unsigned long、BOOL为int、LPVOID为void*；basetsd.h把SIZE_T定义到ULONG_PTR，并按_WIN64等分支定义其底层类型。不能把BOOL当C bool、错误码当PyObject*或用8字节假设替代build核对。winnt.h实际flags为MEM_RESERVE|MEM_COMMIT和PAGE_READWRITE，释放为MEM_RELEASE。其数值仅作所读SDK事实，未来代码使用匹配头的具名常量；不申请可执行页、large pages、远程进程或文件映射。

## 3. 只有自动record拥有workspace

本批具体化为当前真实bootstrap C自动record单独拥有allocation_base，requested_bytes、win32_requested_bytes、BOOL free_result、两个DWORD error、phase与error_valid位另分列。完整声明清单及W00–W42事件表见WIN32-PROTOCOL-01.json。这是文档协议，不是已生成C候选。

拟去掉r1153尚未实施的“state非拥有workspace关联”角色：state不保存workspace指针，m_traverse也不枚举这些私有C根。原published_arena继续只指内联固定RootArena；bootstrap lease/module_guard确保既有固定state寿命，region中的PyObject由该活动C路径独立strong ref覆盖。重入只看原phase/lease/retire协议，不能读/接管私有workspace。此选择去掉一个未确定的非拥有字段，不减少任何真实owning责任；五个class snapshot及108已知固定id域沿r1154。

同一实际bootstrap原base从成功返回持至真实释放，不移动到module/public字段、最近槽或quarantine。固定root与source的可信取得、所有callback后fresh、T0原值的首次capture仍按原设计，不因私有base自证来源。

## 4. 局部事件表与原型正确调用

W00先把自动record初始化为NONE、base=NULL、error_valid=0等实际typed值。完整r1154尺寸/对齐/offset条件和非零requested_bytes先成立；size_t到SIZE_T必须已知无损，不先截断再检测。系统base对所选record的实际最大对齐仍待build/平台核，不新增GetSystemInfo探测来代替证明。

W10只发一次VirtualAlloc(NULL,actual SIZE_T bytes,MEM_RESERVE|MEM_COMMIT,PAGE_READWRITE)。若NULL，W11立即GetLastError保存DWORD并标valid；不先格式化、记录日志、抛Python错误或查询其它OS函数。若非NULL，W12把actual原base记LIVE并按真实类型初始化region；不凭OS零页代替NULL/函数指针初始化。错误码不能拿成功调用留下的thread-local旧值补造；成功分支不调用GetLastError。

W20在已经证明的无Python-callback/GIL释放包络内做第二遍原occurrence capture；完整capture后才允许原tuple等表示准备。W30按实际acquired ledger清PyObject责任并完成最后一次region读取，所有已move项归实际tuple/state；actual first独立C拥有。raw base此时仍由region外的自动record持有，标DRAINED_LIVE。

W40只对这个原base调用一次VirtualFree(base,0,MEM_RELEASE)，保actual BOOL。W41仅在actual返回非FALSE时把base=NULL、phase=RELEASED；此后不读region，也不再通过旧base取ledger、offset或大小。W42若FALSE，立即GetLastError保存release_error，保留原base作为未结清allocation责任的标识，phase=FREE_FAILED_OWNED；不据FALSE断言整个region仍可访问，失败后也不再读region。没有自动重试、额外clock/日志、FormatMessage或隐藏后台处理。

本协议只到局部region的释放后置条件，不自称整个bootstrap返回协议：即使W41成功，所有其余module/type/lease/temp责任仍须按真实顺序结清，最后联合fresh和最终ready/原actual dispatch仍依整图。已有first须按本层实际first持有并在所有cleanup后最终还原；无first入口不能伪造一个已有first。W11的OS失败尚无Python异常，后继准备错误构造及其COMPAT仍待逐site确定，不直接冒认原callee的MemoryError。

GetLastError的[官方合同](https://learn.microsoft.com/en-us/windows/win32/api/errhandlingapi/nf-errhandlingapi-getlasterror)说明需在有意义的失败返回后立即读取，且实际错误码不是可穷举的逐函数固定规范。因此不能用几个常见错误码的白名单证明不存在其它合法失败。错误数值保留不等于Python pending已经建立，也不证明失败allocation已经消失。

## 5. callback包络、原导入与失败出口的精确未闭处

拟local OS wrapper只有原ABI直接调用、C scalar赋值/分支和失败时立即GetLastError；不调Python/可替换PyMem表/可写Python函数，不做GIL释放/thread-state detach，不格式化或登记runtime journal。**这些是未来本方wrapper须满足的静态结构条件，尚非实际代码已审或底层OS绝无callback的证明。**正常线程被OS调度或系统等待不等于本方主动释放GIL，但仅凭持GIL也不能证明任意provider安全。

需要匹配的信任边界是实际编译/加载得到的原native provider及可信OS原语、同解释器有效GIL和实际头/build。原stdlib/内建T0授信不能自动代替该匹配；本稿不以DLL名、当前IAT指针或SDK文件存在自证二进制身份，也不要求自证整个OS或任意外部native内存破坏。完整import库/PE加载来源、wrapper实码及受支持build仍待，不能从本批头声明推出execution_admitted。

W42明确是尚无已批准正常/异常返回的分支：若region仍存在，必须真的释放或按原合同合法移交后才能宣布本层结账。仅记录DWORD、清C指针、返回错误、永存quarantine或无限重试均不满足。正常free可以沿合法API/可信runtime前提作有限证明，但当前没有证据把所有实际FALSE都排除在合法域外，也没有构造已观察到的合法失败反例。本批不假造完成的free失败算法，完整free仍False。

## 6. COMPAT、准入与后继

r1075原145行“不插入新的时钟、计费、I/O、资源操作或用户callback”和165行成本条款完整保留；37/39/127建立bootstrap存储的正面要求不等于平台API全面豁免。本事件表仅让新增OS准备位置可点名，尚未与每个原P0/调用边同版接线；W10/40属于新增准备资源effect及失败点，需核其相对原事件顺序、attempt/seed尚未进入、回调/退休、实际first和预算。没有“分页零I/O”或零成本的保证，没有静默局部合同修订。

后继可直接以W00–W42和SDK分支为核对项补真实原import/build及wrapper；优先解决W42的合法失败域/责任，再完成每个原P0/metadata/source/caller的完整接线。/MT不再作为本问题的解法绕回调查。完整layout/容量、binder/D1/D2、NULL/Init/free/SCC/N4、COMPAT及全部原产品工作仍待；不能从这张局部表生成完整候选或运行native/QA。

本批只读本机SDK/项目文件、只读五个新官方页面、静态hash/AST及文档派生/交付，无内存API/ctypes/native/产品/模型/QA/诊断执行。旧r920/r964耗不重放、r967不执行不重问，POSIX403终态不重取/换址，P06历史0/7不再索资料。原预算/平台/Python>=3.11域不变，正常库/服务/dist/ACLsettings/Git写入0；Task9A/9B、原111/94/r919、真实Store、P06/条件T15/生产云及完整Goal未完，非只剩验收。

## 交付与继续实施

本批只登记CRT替代否决证据与W00–W42局部协议。发布/读回/封存以本批实际记录为准，SDK源不代表已链接native。下一步按W42补合法失败域/真实结账责任与原import/build/wrapper，然后接完整原P0/source/caller及N4/COMPAT。完整产品Goal未缩小，本批静态检查不计产品测试通过。
