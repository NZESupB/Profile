# 统一 Mihomo MRS 配置与构建

`Template/Meta.yaml` 是唯一的完整部署模板。个人域名/IP provider 使用 MRS，其余个人条件使用 classical/list。第三方现成 MRS 和未经修改的文本直接引用上游 URL，需要转换的补充使用 inline provider；本仓库不再镜像第三方供模板调用。AIsouler 来源、路由和 DNS 引用仍通过锁文件验证，保留个人规则的优先顺序。

## 文件职责

| 文件 | 职责 |
| --- | --- |
| `../Template/Meta.yaml` | 完整 MRS 部署配置，含 15 个策略组、DNS、TUN、hosts 和展开后的路由 |
| `../Rule/build.yaml` | 人工来源映射、classical 配套路径、42 条逻辑路由及 DNS 引用、发布基址和客户端策略别名 |
| `../Rule/*.list` | 个人 DOMAIN、DOMAIN-SUFFIX、IP-CIDR、IP-CIDR6 主列表与顺序标记 |
| `../Rule/classical/*.list` | Daily、Download、Games、Speedtest、Torrent 的非 MRS 条件 |
| `../Rule/upstreams.lock.json` | 固定上游提交、SHA-256、同版文本及 classical 补充 |
| `rules.py`、`rule_sources.py`、`rule_templates.py` | 顺序恢复、审计、分片、模板生成与验证 |
| `../.github/workflows/build-rules.yml` | 正式编译、回读、加载、打包和独立分支发布 |

`Rule/aisouler.lock.json` 锁定参考配置及 46 个 provider 的实际来源；10 个复用同版来源、36 个独立来源均直接引用上游 MRS。58 条参考路由中 51 条插入原个人规则之后、综合兜底之前，6 条复用原路由，MATCH 复用原最终策略。14 条参考内联条件按原序号转为模板 inline provider（6 domain、8 classical），不发布对应文件。7 项 DNS 引用全部纳入；逐项结果见构建包 `reference.json`。

模板保留全球互联、游戏娱乐、测速下载、漏网之鱼、TelegramDC5、直连域名、地区选择、香港负载均衡、家庭回退及广告拦截。国内域名提前直连，OpenAI 使用美国，Telegram 使用 TelegramDC5。`Kitony`、`Home_Proxy` 是明确命名的直连占位，需在本机替换为真实订阅。

## 规则格式与顺序

| 个人原始条件 | 正式产物 |
| --- | --- |
| DOMAIN、DOMAIN-SUFFIX | `behavior: domain`、`format: mrs` |
| IPv4/IPv6 CIDR | `behavior: ipcidr`、`format: mrs` |
| 关键词、正则、进程等 | `behavior: classical`、`format: text` |
| USER-AGENT | Mihomo 明确排除并报告；支持的其他客户端仍导出 |

上游 MRS 直引原文件；未经转换的上游文本保持原 behavior/format。差量、净化、ASN 展开及参考内联规则使用 `type: inline`，不进行第三方文本转 MRS。离线构建包仍保存验证副本和同版文本，用于回读、覆盖证明与客户端转换。

个人主文件中的 `# @classical 001` 对应配套文件中的 `# @part 001`。构建器把配套段放回原位置，再按连续规则类型生成 MRS/classical 分片。Games 的交错进程、关键词条件也按原顺序保留。缺失、重复、未引用、空段、越界路径或放错类型会阻止构建。

主文件和配套文件分别计入输入哈希，审计保留实际文件与行号。单独订阅拆分后的主 `.list` 会遗漏 classical 条件；跨客户端应使用生成的 `clients/<客户端>/sources/`，这些文件从完整有序来源生成，不使用 Mihomo 的上游差量。

MRS 仅支持 domain/ipcidr，不能通过更改后缀转换 classical。DOMAIN 在编译前写为 `example.net`，DOMAIN-SUFFIX 写为 `+.example.org`，IP 分片只含 CIDR；`no-resolve` 保留在对应 RULE-SET 引用中。嵌套 RULE-SET 拆分为 OR 并集，外层 AND/NOT 不变，DNS 仍引用含关键词/正则的 classical 补充。

固定 MetaCubeX MRS 同时校验同版文本及完整 classical 分类；不能以纯域名 MRS 代替完整分类。OpenAI 补充中的 AS20473 由锁定快照展开为 CIDR，运行包不含 Geodata 依赖。

## 更新唯一模板

全局设置、订阅、策略组和 DNS/TUN 普通参数在 `Template/Meta.yaml` 修改；规则来源和逻辑路由/DNS 的 rule-set 引用在 `Rule/build.yaml` 修改。

```bash
.venv/bin/python Shell/rules.py audit
.venv/bin/python Shell/rules.py build --text-only --write-template \
  --output dist/meta-text --mihomo /path/to/mihomo
.venv/bin/python Shell/rules.py build --text-only --check-template \
  --output dist/meta-text --mihomo /path/to/mihomo
```

`--write-template` 在文本包和 inline provider 隔离加载通过后更新模板，不在本机编译 MRS。文本包标记 `mode: text`，本地配置使用文本，远程配置中的第三方 MRS 仍直引现成上游文件。`--check-template` 只核对、不写回。个人 MRS 的二进制编译使用固定官方 Mihomo `v1.19.33`，只允许在 Actions 执行。

以下是 **Actions 内部**的编译方式：

```bash
mihomo convert-ruleset domain text input.domain.txt output.domain.mrs
mihomo convert-ruleset ipcidr text input.ipcidr.txt output.ipcidr.mrs
```

## 发布 URL 与缓存路径

```yaml
publish_base: https://raw.githubusercontent.com/NZESupB/Profile/rules
github_proxy: https://gh-proxy.com/
```

基址最后的 `rules` 是分支名，发布产物也有 `rules/` 目录，因此 URL 中的 `rules/rules/` 是正常路径。该基址仅用于个人规则；第三方 URL 从锁文件读取。例如个人分片：

```yaml
rule-providers:
  其他直连__001:
    type: http
    behavior: domain
    format: mrs
    interval: 86400
    proxy: DIRECT
    url: https://gh-proxy.com/https://raw.githubusercontent.com/NZESupB/Profile/rules/rules/local/Direct/001.domain.mrs
    path: ./rules/local/Direct/001.domain.mrs
rules:
  - RULE-SET,国内域名,直连域名
  - RULE-SET,其他直连__001,直连域名
  - MATCH,漏网之鱼
```

该片段只说明路径，完整 provider 和策略组见统一模板。Direct 去重分片依赖更早的同策略国内域名覆盖，不能把它当成完整独立来源。`url` 用于下载，`path` 是 Mihomo 工作目录内唯一的缓存路径；分片编号变化后由生成器同步引用。

GitHub 公开文件统一使用 `ghproxy/原始URL`，包括规则、图标、面板文件、客户端引用及 Actions 内核下载。锁文件保留原始来源 URL 和 SHA-256；下载时添加前缀，已有前缀不会重复套用，失败不自动回退直连。`proxy: DIRECT` 表示连接反代站点，不改变业务策略或路由顺序。认证 Git remote、发布推送和 Actions 平台操作保持原生机制。

Fork 后修改 `publish_base`、静态图标/面板等 GitHub URL 前缀，再运行模板更新命令。上游固定提交不会随 `interval` 自动升级；升级需同步提交、哈希及配套来源。

## Actions 与使用方式

工作流检查 Python、下载并校验固定内核，随后审计来源、正式编译 MRS、回读匹配集合、验证配置和全部 provider 加载。`--check-template` 核对完整模板，独立步骤逐项检查其 provider 名称、URL、格式、路径及 ghproxy/rules 分支引用，再验证单 URL 导入与离线重复构建。

成功运行上传完整 `mihomo-rules-<提交SHA>` artifact，保留离线文件、配置和验证报告。默认分支非 PR 构建自动发布；功能分支普通 push/PR 只验证，手动设置 `publish_rules=true` 才发布。publish job 从 `manifest.publish_files` 提取个人文件，并在全新 worktree 中创建单提交快照。发布后仅检查这些个人文件 URL，直引上游的 URL 在构建阶段另行下载并校验锁定哈希。

`rules` 分支只发布 `rules/local/` 下的个人 `.mrs`/`.list` 及 `rules/source/<个人组>.list`。完整个人列表按原顺序恢复并规范化，不做上游差量，也包含其他客户端支持的 USER-AGENT；Mihomo 使用分片及能力排除结果。配置、第三方副本、客户端目录、manifest 与报告均不进入发布分支。首次个人规则发布前，可使用 artifact 的本地文件配置：

```bash
gh run download <运行编号> -n mihomo-rules-<提交SHA> -D artifacts
tar -xzf artifacts/mihomo-rules.tar.gz -C artifacts
.venv/bin/python Shell/rules.py check \
  --output artifacts/mihomo --mihomo /path/to/mihomo
```

`config.yaml` 使用同包文件；`mihomo.yaml`、`config.remote.yaml` 使用个人发布 URL、上游 URL 及内嵌补充。完整配置从源码 `Template/Meta.yaml` 或 artifact 获取，`rules/mihomo.yaml` 不再提供。客户端文件也仅随包提供；Clash 规则片段引用同包 sources，其余客户端规则按顺序展开供本地导入。

验证后在副本填写本机订阅，不将个人密钥和节点上传。其他客户端输出是规则片段，需要对接 `clients/report.json` 中的策略名和能力差异。隔离内核加载不证明真实节点连通，也不代表已在 BettBox 或付费客户端导入测试。其余命令及限制见 [README.md](README.md)。
