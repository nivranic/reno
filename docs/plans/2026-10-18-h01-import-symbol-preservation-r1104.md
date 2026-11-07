# 保留原模块导入符号，恢复原属性调用发射

## 已确认输入与目标

r1102 SOURCE01 159份完整候选只改三模块；四个尾context读取的P0活动帧来源修订已静态接受，完整认证原红1仍保。本批父仍是这份确切组合，不安装、不导入或执行候选。r1103明确新增6次静态供给，精确复现旧metadata；原423 code中46个raw hash变化已实测为122处导入基对象调用的244条LOAD_GLOBAL/LOAD_ATTR低位flag变化。完整names、typed constants和异常表相同，但严格指令对比仍不同。

官方CPython3.12.10的import基对象识别使用module st_top的DEF_IMPORT；将原imports搬进constructor且声明global，并不把DEF_IMPORT同步回module。原先import基对象的普通属性调用因此改用method优化。当前只确认静态发射及其编译条件，不声称运行等价或发现实际运行故障。

## 方案比较与选择

采用module级literal `if False` 中的原import声明，保原compiler symbol事实；真实imports继续留在原constructor body位置，真实导入次数/顺序不变。这不需要新helper、runtime compile/exec/CodeType.replace、新的公开alias、权限或原生调用。

直接接受不同method lowering需另证完整动态属性访问与调用协议，且不能称原bytecode保持；本批不选。撤回constructor会重新失去已经修订的四尾原context来源，不选。将真实imports提前到module则改变循环初始化时序，也不选。

## 确切源码变换

对control/scope/registration：取r1100原模块的所有module词法域Import/ImportFrom，在原context赋值之后的语句中查找，包括module if/try/loop内的import，排除nested function/class/lambda域。按源位置排序，以原Import AST（module/level/name/asname逐项相同）置入下列固定region。prefix既有future/import保持，不复制future、不含star。

```python
# BEGIN ORIGINAL-MODULE-IMPORT-SYMBOLS
if False:
    # 每条为原module域Import/ImportFrom的相同AST；不调用或执行
    from ... import ...
# END ORIGINAL-MODULE-IMPORT-SYMBOLS

def _AUTH_BOOTSTRAP_CONSTRUCT(...):
    ...  # r1102完整原文，不改
```

region仅插在原constructor def之前，原prefix原bytes、原constructor body/tail/唯一调用全部原bytes保持。未加新Name key STORE，literal False不是动态变量，guard没有else、walrus、函数或调用。语法上保留额外import声明是有意的编译输入；不能把这组AST imports算成实际import次数。

静态将region一个If节点移除后，完整Module AST及全文bytes必须恢复r1102父；region内import AST与r1100原module列表全同。完整159源码三改156逐byte继承、每份parent/candidate/inverse pin、完整diff/ndiff。原17lease/1449slot/406尾局部、四held读取、五S002同锁/锁外及所有原Call由精确bytes逆像保持。

## 供给与验证

设计及完整builder经Root/同一独立代理静态复核后才供给。新增Root候选三次syntax compile，每个改动文件一次；本批父新增compile0，r1103原始完整JSON作为只读父比较事实。所有复审新增compile0。保存module+全部code原始marshal及JSON，code只检查不执行；先保存完整产物后断言，失败不重编译。

映射r1103的423个原code，对候选剔除原有constructor后逐项核name/args/varnames/freevars/cellvars，named qualnames、原8匿名qualname变化及68 CO_NESTED增量维持r1102协议，first_line/positions按新region行数另列。所有423个原co_code raw bytes必须与r1100原供给相同；names与完整typed constants（code-ref按预序映射）及原异常表逐项同，不将仅“长度相同”当恢复证明。

新module/constructor不作旧module bytecode相同声明；额外literal guard可能影响NOP、module常量/名字表和源位置，原始数据全保。对module代码实际IMPORT_NAME/IMPORT_FROM操作列表须与r1102 module相同，证明新增region没有发射运行import。对新constructor/已有所有子code的实际import指令位置和计数应与r1102相同（物理行号变化另列），不只凭if False文字断言。actual globals key/cardinality、三循环导入、GC、运行兼容及性能仍待以后完整包QA。

保护56同18缺、正常库/service-dist/ACLsettings/平台/Git及原控制限额保持。本批无候选import/执行/QA/诊断/native/SQL/Store，无新异常链观察、profiling、frames/locals。r920/r964耗不重放、r967待原问题不执行重问，P06历史0/7。source_approved/execution_admitted=False、产品pass0，完整factory/caller/audit来源阻塞仍须后续具体修订，Goal active未完。
