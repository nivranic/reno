# 现场核对单本人摘要预览

本项落实主方案 §17.116 明列的“核对单摘要”未完范围。用户打开一份清单后，可以预览当前结果和复看提示；读取现有本地或已安装的云镜像数据，不依赖 H01 新写权。当前阶段不生成分享链接或访客访问凭据，不能据此宣称可撤销分享已完成。

## 请求与版本核对

接口为 `POST /api/projects/{project_id}/checklists/share-preview`，严格接收一份清单：

```json
{
  "selection": [{
    "instance_id": "11111111-1111-4111-8111-111111111111",
    "revision": 1,
    "items": [{
      "item_id": "22222222-2222-4222-8222-222222222222",
      "revision": 1,
      "last_commit_seq": 3
    }]
  }]
}
```

这些 UUID 仅为文档样例。`items` 必须包含原详情页的全部条目并保持原顺序；`last_commit_seq` 是该条目全部检查、整改和作废记录的最大序号，无记录为 0。追加检查并不会增加清单或条目版本，不能只提交 `revision` 或只看当前生效检查。

后端在同一原只读事务内核对项目归属、清单版本、条目全集及顺序、各条目版本和最大记录序号。任一选择变化返回 `409 SHARE_SELECTION_CHANGED`。随后沿 `checklists.detail` 原业务逻辑重新计算 `current_result`、`needs_recheck`、`progress` 和清单状态。依据或附件后续变化不会增加选集序号，因此不使用客户端旧结果替代本次真实读取。

## 摘要边界

响应采用与现有决定摘要一致的封闭声明：

```text
snapshot:
  format_version: 1
  kind: personal_checklist_share_snapshot
  checklists:
    title, state, lifecycle_state, revision, updated_at
    progress: total, complete
    items: title, revision, current_result, needs_recheck, basis_kind
snapshot_sha256: canonical snapshot bytes 的 SHA-256
personal_read_version: null 或原镜像读取版本
```

选集中的内部 ID 和记录序号不进入摘要。空间、工序、项目资料、判据正文、依据详情、观察值、记录说明、附件内容和历史记录不进入响应。`checklists.detail` 在内部仍读取原判断所需数据，本项没有删除原业务判断。纯声明投影只复制白名单字段。

`updated_at` 保留清单修订时间，不把新增检查时间解释成清单修订时间。哈希仅证明当前摘要声明的字节一致，不证明访客权限、发布成功、H01 写入回执或工程验收。

## 权限与退出顺序

- 只有精确的 API 名称、路径和 POST 方法可以使用现有云只读通道；其他写接口继续使用原写门。
- 个人读取和现场读取开关在请求体解析前拒绝。正式读取仍通过 `read_result(field=True)` 的原字段准入和镜像版本检查。
- 原事务、来源校验和读取工厂正常退出后才冻结摘要字节并产生 HTTP 响应。退出失败时不返回先前读取到的摘要。
- HTTP 响应继续使用 `private, no-store` 和原读取版本；不写数据库，不创建分享令牌或操作回执。

## 页面行为

入口放在现场清单详情页，读写页和只读镜像页均可用。只有用户点击才发送读取请求，加载时不使用缓存详情拼出摘要。正文由 React 作为普通文本渲染。

关闭、离开清单、读取范围/版本/权限变化、清单刷新或读取失败均撤去摘要，并取消未完成请求；迟到的旧响应不能重新显示。权限恢复或刷新完成后需要用户重新点击。清单归档操作尚未确认时也不保留原预览。

409 不提供重复提交旧选集的重试按钮；用户点击“重新读取清单”，关闭预览后读取详情，再自行重新打开。普通暂时读取错误允许明确点击重试。

页面明确“尚未生成分享链接”和“结果仅表示所记录的本项核对情况，不等于工程验收合格”。个人要求的 pass 显示“符合个人本项要求”；依据需复看的状态保持可见。用户填写的标题仍需本人核对是否含私人信息。

## 实现与验证分工

后端新增 `reno/personal_checklist_share_contract.py`、`reno/personal_checklist_share_preview.py`，在 `app/checklist_api.py` 接线，并对 `app/personal_api.py` 原路由门做精确扩展。前端在 `frontend/src/lib/personal-api.ts` 增加声明与请求函数，新增 `features/checks/checklist-share-preview.tsx`，在 `check-detail-page.tsx` 接入。

后端验证使用新临时目录内的合成家庭库及真实安装的合成云镜像，检查只读/无写、版本与记录序号冲突、跨项目拒绝、依据变化重算、字段门和工厂退出失败。前端验证点击请求、响应白名单、文本渲染、取消/迟到响应、409、权限与详情刷新清理以及真实页面接线；执行类型检查和输出到新临时目录的生产构建。

最终实际结果与日志位置写入主方案 §17.177。本文件描述实现合同，不把准备工作或尚未完成的测试当作绿色证据。真实私人库、正常服务和生产 `frontend/dist` 不在本项隔离验证的修改范围内。

## 本次实际交付结果

已完成源码与独立审查，未决代码发现为 0。后端新增选集37项通过，增强历史记录序号断言后的目标及既有路由回归4项通过。前端新预览文件31项通过，最后合成事件ID修正的单例通过；早期两处新测试查询定位错误及全部旧日志保留。类型检查通过，生产构建输出到新TEMP，当前生产dist保持。

源码和隔离测试结果见主方案 §17.177；上述是该阶段的历史时点。后继 §17.178 已完成两套真实本地API浏览器功能验证及独立稳定视图补证，并交付到正常本机服务。正常HTTP逐bytes核同49份新资产，63次请求检查通过；空选集POST返回400/private,no-store。真实库0决定卡和0清单，没有为了验证创建真实记录。正向摘要使用真实合成家庭库，权限撤回/刷新仍是标记的浏览器模拟。启动后两次完整保全和新worker自然续租通过，旧50份dist完整保留，schema/config/身份/附件/原知识保持。正常交付结果位于 `data/checklist-summary-normal-20261009/terminal-reviewed.json`；稳定截图位于 `data/checklist-summary-browser-20261009-views/`。真实分享发布和永久撤销仍未完成，完整goal保持active。
