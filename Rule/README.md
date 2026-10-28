# 个人规则与自动构建

个人规则按用途维护，发布后的 Mihomo 配置统一使用 MRS 域名/IP 分片与 classical 补充。

| 主文件 | 用途 | classical 配套文件 |
| --- | --- | --- |
| `Daily.list` | 低流量日常网站 | `classical/Daily.list` |
| `Direct.list` | 国内漏网直连与误杀恢复 | 无 |
| `Download.list` | 大带宽下载 | `classical/Download.list` |
| `GameDirect.list` | 游戏直连和国内 CDN | 无 |
| `Games.list` | 游戏代理，节点需支持 UDP | `classical/Games.list` |
| `Reject.list` | 自定义拦截 | 无 |
| `Speedtest.list` | 测速服务 | `classical/Speedtest.list` |
| `Torrent.list` | 下载进程与域名 | `classical/Torrent.list` |
| `Video.list` | Emby 等影视站 | 无 |
| `jp.list`、`us.list`、`tw.list` | 区域限制服务 | 无 |

主文件只保留 DOMAIN、DOMAIN-SUFFIX、IP-CIDR、IP-CIDR6，Actions 将所有域名/IP 连续段打包为 MRS。关键词、正则、进程等不支持 MRS 的条件独立维护在 `classical/`，保持 classical/text 格式。

例如主文件中：

```text
DOMAIN,example.net
# @classical 001
DOMAIN-SUFFIX,example.org
```

其声明的配套文件中：

```text
# @part 001
PROCESS-NAME,example.exe
```

构建器在两条域名规则之间恢复该进程规则。段号可复用在不同来源中，同一来源内必须唯一且全部引用。主文件与配套文件分别校验哈希，审计报告指向真实文件和行号。新增配套文件需同步 `build.yaml` 的 `local.<主文件>.classical`。

**拆分后的主 `.list` 不再是完整的独立订阅。** `rules` 分支发布的 `rules/source/<组>.list` 会按原顺序恢复完整个人条件、规范化地址且不做上游减法；其中 USER-AGENT 等仍需按客户端能力处理。客户端转换版本位于 Actions 构建包的 `clients/<客户端>/sources/`，不发布到 rules 分支。个人运行分片在 `rules/local/`，域名/IP 为 MRS，其余及对照文本为 `.list`。

- `build.yaml`：来源映射、classical 路径、明确排除项、逻辑路由/DNS、发布基址和客户端策略名。
- `upstreams.lock.json`：不可变上游地址、快照和 SHA-256；现成 MRS 同时锁定同版文本与完整 classical 补充。
- `snapshots/`：Sukka 原始发布快照，保留来源和许可证头，供可重复构建使用。
- `fakeipfillter.txt`：原样接入 DNS 的通配模式，不编译为分流 MRS。
- `Torrent.list`、`tw.list`：生成域名/IP MRS 和 classical 产物，但不增加原配置未定义的路由。
- `classical/Speedtest.list`：USER-AGENT 供支持的客户端导出，Mihomo 按清单明确排除。

`Template/Meta.yaml` 是唯一的完整 MRS 部署模板。修改规则后运行 `rules.py build --text-only --write-template`，生成通过隔离内核检查的文本包并同步模板引用；正式 MRS 编译和回读验证只在 Actions 执行。工作流使用 `--check-template` 防止分片过期。

只删除由更早、同策略、同参数上游完整覆盖且 DNS 兼容的本地项，差量仅用于 Mihomo 运行包。其他客户端使用未做上游减法的完整有序来源。关键词、正则和 no-resolve 不得因 MRS 拆分而丢失；不支持的条件写入 `clients/report.json`。

默认分支构建成功或手动设置 `publish_rules=true` 后，仅将个人来源生成的 `.mrs`/`.list` 发布到独立 `rules` 单提交快照分支；功能分支普通构建只验证和上传 artifact。第三方现成 MRS 和未经转换的文本直引上游，转换补充内嵌模板，不在 rules 分支镜像。配置、报告和客户端目录仅随完整 artifact 提供。GitHub 公开文件 URL 继续使用 ghproxy 前缀。

构建命令、单 URL 导入、产物路径及使用限制见 [Shell/README.md](../Shell/README.md) 和 [MRS 方案](../Shell/MIHOMO-MRS.md)。
