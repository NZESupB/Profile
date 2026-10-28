# 规则构建与单 URL 转换

当前构建输入为 `Template/Meta.yaml`，以根目录个人 `Meta.yaml` 为基准。根目录原文件不参与 CI，也不得提交其中的控制器密钥或订阅信息。`Template/metaProvider.yml` 保留为旧模板，当前构建入口以新模板为准。

## 配置与来源

新模板保留原配置的 42 条路由顺序、15 个策略组、FCM hosts、DNS 上游及 TUN 参数。国内域名仍提前走直连，OpenAI 走美国，Telegram 走 `TelegramDC5`，台湾、香港负载均衡及家庭代理组继续保留。修复了原 UDP 逻辑中 OR 子表达式的括号，并将 Geodata 条件替换为显式 provider。

公开模板的控制器监听改为 `127.0.0.1:9999`，`secret` 留空。`Kitony`、`Home_Proxy` 使用明确命名的直连占位，保证缺少订阅时可以解析；它们不提供代理连接。使用前在本机替换为真实订阅，并在区域组中选择实际节点。家庭组仅使用 `Home_Proxy`，不在真实家庭节点前插入永久直连项。

模板是构建输入，推荐使用生成包中的 `config.yaml`。模板内 SpeedTest、OpenAI 补充和第二份媒体列表指向 `rules` 分支的净化文本，首次默认分支发布前这些远程入口不可用；本地构建包已内置全部文件，不依赖这些入口。

## 本地环境与文本验证

建议使用与 CI 一致的 Python 3.11，依赖安装在虚拟环境中：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r Shell/requirements-rules.txt
.venv/bin/ruff check Shell/*.py
.venv/bin/ruff format --check Shell/*.py
.venv/bin/python Shell/rules.py audit --report dist/audit.json
.venv/bin/python Shell/rules.py build --text-only \
  --output dist/meta-text --mihomo /path/to/mihomo
.venv/bin/python Shell/rules.py check \
  --output dist/meta-text --mihomo /path/to/mihomo
```

`audit` 不调用内核。首次下载上游后，`--offline` 可使用 `.cache/rules` 中的哈希缓存与 `Rule/snapshots/` 原始快照。`build --text-only` 生成 `mode: text` 的完整文本包并启动隔离内核检查；不会编译 MRS。正式文本转 MRS 只能在 GitHub Actions 执行，本机不得设置伪造的 Actions 环境绕过该限制。

所有显式相对参数以调用目录为准，默认路径以脚本所在仓库为准。可覆盖 `--config`、`--rules-dir`、`--manifest`、`--lock`、`--cache`、`--output`；自定义规则目录须有配套清单和锁文件。

## 单 URL 导入

只提供公开 URL 即可自动识别 MRS、文本或带 `payload` 的 YAML，并推断 domain/ipcidr/classical。MRS 读取需要官方 Mihomo，操作仅为 MRS 转文本。

```bash
.venv/bin/python Shell/rules_url.py \
  'https://example.com/public-rules.mrs' \
  --config dist/meta-text/config.yaml \
  --name 自定义规则 --policy 全球互联 \
  --mihomo /path/to/mihomo --output dist/url-rule
```

常用参数：

| 参数 | 含义 |
| --- | --- |
| `--sha256 <64位哈希>` | 校验本次下载，变化时停止 |
| `--format auto\|mrs\|text\|yaml` | 自动识别有歧义时指定输入格式 |
| `--behavior auto\|domain\|ipcidr\|classical` | 指定匹配行为；MRS 只支持 domain/ipcidr |
| `--before '完整现有路由'` | 在指定路由前插入；默认在首条 MATCH 前 |
| `--no-resolve` | 将该约束保留在 Mihomo 路由与支持的客户端导出中 |

策略必须存在于输入配置。已有更早规则可能先命中，需要优先接管时使用 `--before`。输出目录必须为空；转换成功后才写入最终目录。输入配置中的 file provider 只允许引用配置所在目录内的文件，并会一并复制；缺失或越界会失败。

输出包含 `config.yaml`、原始 provider 缓存、`classical.list`、各客户端文本与 `source.lock.json`。Mihomo 不支持的 USER-AGENT 被明确报告，provider 改用本地净化文件；其他支持的客户端保留该规则。未知规则或 Geodata 条件会失败，不会被自动扩大或缩小范围。

SHA-256 校验只约束本次下载。普通 HTTP provider 后续由内核更新时不会读取 `source.lock.json`；需要固定匹配范围时应使用不可变提交 URL，或在本机把 provider 改为 file 并保留同包文件。单独一个 MRS 只能恢复该 MRS 自身的域名/IP 覆盖，不能推导上游另行维护的关键词和正则。

## Actions 构建、同步与下载

工作流为 `.github/workflows/build-rules.yml`，名称「构建并同步代理规则」。规则、脚本、新模板或工作流变动的 push/PR 触发检查，也支持手动运行。

工作流使用固定 Mihomo v1.19.33 及归档 SHA-256，依赖版本见 `requirements-rules.txt`。它审计来源、编译分片、回读 MRS 比较匹配集合、执行 `mihomo -t` 和 provider 实际加载，验证单 URL 的 domain/ipcidr 接入，再离线重建比较清单。

每次成功运行上传 `mihomo-rules-<提交SHA>` artifact。**只有默认分支的非 PR 构建成功后**，独立 publish job 才以 `contents: write` 将已验证的公开产物同步到 `rules` 分支；功能分支只验证和上传。发布保留 `rules` 分支历史，不合并业务分支，不发布 release。

```bash
gh run list --workflow build-rules.yml
gh run download <运行编号> -n mihomo-rules-<提交SHA> -D artifacts
tar -xzf artifacts/mihomo-rules.tar.gz -C artifacts
.venv/bin/python Shell/rules.py check \
  --output artifacts/mihomo --mihomo /path/to/mihomo
```

## 产物与客户端使用

| 路径 | 用途 |
| --- | --- |
| `config.yaml` | 使用同包文件的完整 Mihomo 配置 |
| `mihomo.yaml`、`config.remote.yaml` | 使用 `rules` 分支 HTTP provider 的完整配置 |
| `rules/local/`、`rules/upstream/` | 连续分片、MRS 对照文本及 classical 补充 |
| `rules/source/` | Mihomo 可加载的净化文本源，包括显式 ASN 展开 |
| `clients/<客户端>/sources/` | 按人工分组生成的客户端规则 |
| `clients/clash/rules.yaml` | Clash provider 与有序路由片段 |
| `clients/{surge,surfboard,shadowrocket}/rules.conf` | 对应客户端的有序规则片段 |
| `clients/quantumultx/rules.conf`、`routes.list` | Quantumult X 合并远程规则及 final 本地规则 |
| `clients/report.json` | 无法等价转换的规则、原因及需要建立的策略名 |
| `audit.json`、`manifest.json`、`upstreams.lock.json` | 覆盖证明、输入/产物哈希与来源锁定 |

Actions 的 `mode: mrs` 使用 MRS 与 classical 混合 provider；文本构建中的远程配置仍引用文本，不是正式 MRS 产物。两种包均不包含 `.dat`、`.mmdb`，运行配置不含 GEOSITE、GEOIP 或 IP-ASN 依赖。

各客户端输出是**规则片段**，需要在目标客户端中定义 `clients/report.json` 的 `policy_names`，保留原有订阅、DNS、证书和设备设置。策略别名由 `Rule/build.yaml` 的 `client_policies` 显式控制。

Clash、Surge、Surfboard、Shadowrocket 在原路由位置交替输出内联与规则集。Quantumult X 将可转换路由合并为一个带策略的远程文件，避免 local/remote 分段移动规则；没有已验证等价语法的 `no-resolve` 规则会列入排除报告。逻辑规则、正则、进程等能力差异也逐项报告。因此不能将这些文件描述为所有客户端的完整等价配置；语法生成与引用检查不代表付费客户端或设备上的实测。

## 覆盖与验证约束

- 共享 `Rule/*.list` 保持人工维护，不回写 Mihomo 专用上游差量。其他客户端导出使用完整分组来源，不使用 Mihomo 的路由去重结果。
- 只删除被更早、同策略、同参数上游完整覆盖的本地项；DNS 引用还须有相同条件下的覆盖证明。逻辑规则引用的本地来源不做此类删除。
- 域名/IP 使用 MRS，关键词、正则、通配与进程规则保留 classical；固定 MRS 同时锁定同版文本及完整 classical 补充，不用纯域名列表代替完整分类。
- provider 在原位置按连续分片展开；嵌套 RULE-SET 展开为 OR 并集，使外层 NOT/AND 继续作用于完整来源。`no-resolve` 保留在每个需要的分片上。
- OpenAI 补充源的 AS20473 由显式清单展开成锁定 CIDR 快照；更新时必须同步来源和哈希。它反映快照时的 ASN 范围。
- DNS 的关键词/正则 classical 分片继续参与匹配。只允许官方内核固定的「classical provider 只匹配域名」提示，其余解析警告和错误会阻止构建。
- `fakeipfillter.txt` 的 DNS 通配模式原样追加，不按普通域名处理。`Torrent.list`、`tw.list` 仍只导出分组文件，不增加当前基准未定义的路由。
- 工具在临时目录完成检查后替换输出；非空构建目录必须带 `.rules-build` 标记。下载变化、缺失依赖、重复键、未知规则及无效引用均失败。

下载包先运行 `check`，再在副本中填写真实订阅。修改配置后哈希发生变化是预期行为，不修改 manifest 来伪造校验。BettBox 的配置导入还需检查应用级 DNS/TUN 及脚本覆写；`BettboxCore` 不等同于官方 MRS 编译 CLI。隔离内核加载不证明真实节点连通，也不代表已经导入或切换 BettBox。
