# 私有个人设施状态接口

`GET /api/personal/authority/status` 读取服务器明确选择的既有文件设施。它复用 `X-Reno-Token` 或既有 `token` 参数认证；响应统一 `Cache-Control: private, no-store`。客户端不能传入路径、节点、绑定摘要或报告，也不能发送请求体。

## 服务器观察目标

通过服务器进程环境变量 `RENO_PERSONAL_AUTHORITY_READ_TARGET` 指定闭合 JSON：

```json
{
  "format_version": 1,
  "facility_root": "<平台规范绝对路径，database.parent 的 .reno-personal-authority* 直接子目录>",
  "profile_paths": ["<database>", "<media>", "<reports>", "<checkpoints>"],
  "expected_node_id": "<既有 node-identity.json 中的 canonical 非零 instance_id>",
  "expected_declared_binding_sha256": "<原 declared-binding.json 字节的小写 SHA256>"
}
```

这是字段说明，尖括号占位符不能作为有效配置。四路径必须逐项等于当前 `storage_admin.profile().identity`。设施路径必须使用 `normcase(abspath(path))` 的平台规范拼写；Windows 使用规范反斜线和小写，不接受重复、尾分隔或点路径。校验不 resolve，也不会扫描和自动选择设施。JSON 上限 32768 UTF-8 字节，重复键和额外字段拒绝。

设施的声明 `profile_sha256` 必须是上述四路径**数组** canonical JSON（UTF-8、ensure_ascii=False、sort_keys=True、separators=(',', ':')、allow_nan=False）的 SHA256。字典形式的存储摘要不能代替该数组摘要。

服务器没有设置目标时返回 `503 / TARGET_NOT_CONFIGURED`。读取要求当前生命周期设施和原节点文件已经存在；端点不创建、登记、修复或授权任何设施。修改服务器进程环境需要重启该进程，本实现不提供配置写接口。

## 响应和错误

成功 DTO 的 `reported_filesystem_state` 为 `absent`、`retained_unproven`、`reserved`、`unavailable` 或 `ready`；ready 的 `reported_head_state` 为 `missing`、`matches` 或 `stale`。`unavailable` 通过 `observation_error_code` 保留有限文件错误码，其余为 null。

`effective_authority_state` 恒为 `unknown`，`write_authority` 和 `handoff_authority` 恒为 `not_established`。这些状态描述选中目录的文件观察结果，不能用于决定写入、切主或接管。reserved 缺少 binding 时仍为 reserved，不表示节点或路径绑定已经完整建立。

选择不匹配、节点不匹配、存储忙、节点变化和其他来源失败分别返回有限错误码，不回传路径、UUID、SHA 或原始材料。请求断开或取消时，实际观察 worker 完成退出后才能交付响应；正常验证或关闭失败不返回 ready。

## 验证范围

本接口依赖真实 lifecycle SH、同 FD 节点读取和文件状态观察器。受控替身/ASGI 测试验证接口和故障处理；它们不代替真实 Windows 文件、锁和正常关闭的运行证明。当前批次的实际证据见 `data/t16-h06-read-runtime-r814`，完成状态以其中交付记录为准。
