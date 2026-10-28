# 规则构建与单 URL 转换

`Template/Meta.yaml` 是唯一的完整 Mihomo MRS 部署模板，基于根目录个人配置脱敏生成；不再区分文本模板和 MRS 示例。全局配置、订阅占位、策略组、DNS 和 TUN 保留在模板中，来源映射与拆分前的逻辑路由由 `Rule/build.yaml` 维护。根目录私人原文件不参与 CI，`Template/metaProvider.yml` 保留为旧模板。

规则拆分、完整工作流及 URL/缓存路径说明见 [Mihomo MRS 方案](MIHOMO-MRS.md)。

## 配置与来源

构建清单保留原配置的 42 条逻辑路由顺序，模板自动展开为 MRS/classical 引用，同时保留 15 个策略组、FCM hosts、DNS 上游及 TUN 参数。国内域名仍提前走直连，OpenAI 走美国，Telegram 走 `TelegramDC5`，台湾、香港负载均衡及家庭代理组继续保留。修复了原 UDP 逻辑中 OR 子表达式的括号，并将 Geodata 条件替换为显式 provider。

公开模板的控制器监听改为 `127.0.0.1:9999`，`secret` 留空。`Kitony`、`Home_Proxy` 使用明确命名的直连占位，保证缺少订阅时可以解析；它们不提供代理连接。使用前在本机替换为真实订阅，并在区域组中选择实际节点。家庭组仅使用 `Home_Proxy`，不在真实家庭节点前插入永久直连项。

个人域名/IP 规则生成 MRS，个人 classical 分片使用 `.list`，仅这些个人规则引用 `rules` 分支。现成第三方 MRS 直接引用锁定的上游 URL；未经转换的文本直接引用上游文本；净化、ASN 展开和 classical 差量使用模板 `type: inline` provider。完整配置和第三方副本不发布到 `rules` 分支，配置入口仍是 `Template/Meta.yaml` 或 Actions 运行包中的 `mihomo.yaml`。

`Rule/build.yaml` 的 `github_proxy: https://gh-proxy.com/` 控制公开 GitHub 文件的 URL 反代前缀；模板、图标、生成配置和客户端引用均使用该前缀。Actions 内核下载、锁定规则源及单 URL 导入也由公共下载函数添加前缀。`proxy: DIRECT` 表示连接 ghproxy 站点，不改变业务策略或路由顺序。锁文件保留原始 URL 与 SHA-256；修改前缀时同步模板图标、面板等静态 URL，再更新模板引用。认证 Git remote 和发布推送保持原生机制。

## AIsouler 来源融合

`Rule/aisouler.lock.json` 锁定 AIsouler 配置与其实际引用来源。构建器按 `build.yaml.reference` 映射全部 46 个 provider、58 条参考路由及 7 项 DNS 引用：当前 10 个来源以相同二进制/classical 哈希证明复用，36 个独立接入；新增 51 条路由，6 条复用原规则，MATCH 复用原最终策略。原 42 条逻辑路由的相对顺序不变，参考规则插入个人规则之后、综合媒体和全局规则之前。因此沿用个人优先策略，并非替换成参考配置的所有优先级。

AI 映射到美国，Steam 到游戏娱乐，Apple/Microsoft 到测速下载，Telegram 到 TelegramDC5，其他海外服务到全球互联。参考中的国内/私有域名 DNS 接入现有国内解析器，抖音保留专用解析器；fake-IP 引用全部接入。`fakeip_filter` 含 DNS 特殊模式，保留原始 MRS/text、禁止用于路由、不导出为其他客户端的分流规则，并写入能力差异报告。

14 条参考内联规则逐条保存来源序号和文本快照，并在 `build.yaml.inline` 显式声明，全部保留为模板 inline provider：6 条 domain、8 条 classical；在原插入位置使用 RULE-SET，保留策略及 no-resolve。锁定文本必须与固定配置去掉策略后的规则完全一致，不能跨位置合并。原个人逻辑路由中的家庭网段和个人域名内联条件保持原样。

构建输出 `reference.json` 逐项记录来源、内联规则、路由与 DNS 去向，漏 provider、漏策略映射、内联派生哈希不符、失效插入位置或 DNS 源误接路由均失败。来源升级使用 `Shell/update_aisouler.py`，显式提供配置、bett-rules、Emby、Emos 的完整提交 SHA、缓存路径和 Mihomo 回读路径；该工具仅下载/回读，不编译。可变的 `cn_additional` 保存已下载 MRS 与同版回读文本快照，Emos YAML 配套文本转换也记录原始哈希。升级后核对 `build.yaml.inline` 声明，再执行文本验证及模板更新。

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

修改来源或 `Rule/build.yaml` 后，使用以下命令更新唯一部署模板；`--write-template` 只在文本包通过隔离加载后写回 MRS 地址，不生成本地 MRS。`--check-template` 用于检查现有模板是否与当前规则一致，Actions 正式构建也执行该检查。

```bash
.venv/bin/python Shell/rules.py build --text-only --write-template \
  --output dist/meta-text --mihomo /path/to/mihomo
.venv/bin/python Shell/rules.py build --text-only --check-template \
  --output dist/meta-text --mihomo /path/to/mihomo
```

个人主列表 `Rule/*.list` 只维护 DOMAIN、DOMAIN-SUFFIX、IP-CIDR、IP-CIDR6。其他类型位于 `Rule/classical/{Daily,Download,Games,Speedtest,Torrent}.list`；主文件的 `# @classical 001` 对应配套文件的 `# @part 001`，构建器在原位置插入。新段须有唯一编号，且在 `build.yaml` 的 `local` 项中声明 `classical` 路径。缺失、重复、未引用的段、放错类型及越界路径都会失败。主文件与配套文件分别计入输入哈希，报告保留真实文件与行号。

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

工作流使用固定 Mihomo v1.19.33 及归档 SHA-256，依赖版本见 `requirements-rules.txt`。它审计来源、只编译个人 MRS、回读个人和现成上游 MRS、加载全部 provider，并核验模板中的个人 URL、上游 URL 和内嵌 payload。上游直引文件另行实际下载校验锁定哈希；随后验证单 URL 接入和离线重复构建。当前模板含 60 个个人分片、68 个上游直引、64 个内嵌 provider，共 192 个。

每次成功运行上传完整 `mihomo-rules-<提交SHA>` artifact，内含离线验证来源、配置和报告。默认分支非 PR 构建成功后自动发布；功能分支普通 push/PR 只验证和上传，手动设置 `publish_rules=true` 才发布。publish job 根据 `manifest.publish_files` 提取个人规则，在全新 worktree 中重建 `rules` 单提交快照，并用 force-with-lease 防止覆盖并发更新；不合并 main。发布分支只有个人 `.mrs` 和 `.list`，不含 `mihomo.yaml`、第三方镜像、`clients/` 或报告。发布后逐个下载白名单文件核对哈希，失败则报告错误。CI 可达不等于中国大陆可达。

```bash
gh workflow run build-rules.yml --ref <来源分支> -f publish_rules=true
```

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
| `mihomo.yaml`、`config.remote.yaml` | 个人规则引用 rules 分支，第三方直引上游或内嵌补充；配置本身仅在构建包和源码模板中提供 |
| `rules/local/` | 个人连续分片；域名/IP 为 MRS，配套文本和 classical 为 `.list`，允许发布 |
| `rules/upstream/` | 仅供离线验证的第三方副本和内嵌补充对照，不发布 |
| `rules/source/<个人组>.list` | 恢复顺序、规范化且不做上游减法的完整个人列表，允许发布；包含其他客户端支持的 USER-AGENT |
| `rules/source/upstream-*.list` | 第三方净化来源，只在验证包保留 |
| `clients/<客户端>/sources/` | 按人工分组生成的客户端规则 |
| `clients/clash/rules.yaml` | Clash provider 与有序路由片段 |
| `clients/{surge,surfboard,shadowrocket}/rules.conf` | 对应客户端的有序规则片段 |
| `clients/quantumultx/rules.conf`、`routes.list` | Quantumult X 本地规则段及有序规则列表 |
| `clients/report.json` | 无法等价转换的规则、原因及需要建立的策略名 |
| `audit.json`、`manifest.json`、`upstreams.lock.json` | 覆盖证明、输入/产物哈希与来源锁定 |
| `reference.json` | AIsouler 全部来源、路由、DNS 引用及策略映射的逐项结果 |

Actions 的 `mode: mrs` 包含个人 MRS 及用于回读的上游 MRS 副本；文本构建的本地 `config.yaml` 使用文本，但远程配置仍可直引已存在的上游 MRS，不表示本机编译过 MRS。两种包均不包含 `.dat`、`.mmdb`，运行配置不含 GEOSITE、GEOIP 或 IP-ASN 依赖。第三方运行时更新不会读取本仓库锁文件；固定提交 URL 保持版本，可变上游（例如 cn_additional）未来变化需重新锁定验证。

各客户端输出是**规则片段**，需要在目标客户端中定义 `clients/report.json` 的 `policy_names`，保留原有订阅、DNS、证书和设备设置。策略别名由 `Rule/build.yaml` 的 `client_policies` 显式控制。

客户端文件仅随完整构建包提供，不再发布到 `rules` 分支。Clash 使用同包 `sources/` 文件，需保留相对目录；Surge、Surfboard、Shadowrocket 按原位置展开可转换规则；Quantumult X 导入 `[filter_local]` 保留顺序。没有已验证等价语法的规则列入排除报告；这些片段不保证所有客户端完整等价，语法生成和引用检查不代表付费客户端或设备实测。

## 覆盖与验证约束

- 人工源由 `Rule/*.list` 与声明的 `Rule/classical/*.list` 合并恢复。单独订阅拆分后的主列表会遗漏 classical 条件；完整个人来源使用发布的 `rules/source/<组>.list`，客户端转换版本使用构建包内 `clients/<客户端>/sources/`。二者均不使用 Mihomo 的上游差量。
- 只删除被更早、同策略、同参数上游完整覆盖的本地项；DNS 引用还须有相同条件下的覆盖证明。逻辑规则引用的本地来源不做此类删除。
- 个人域名/IP 使用 MRS，其他条件保留 classical/list。第三方优先直引现成 MRS 或未经修改的文本；差量和转换补充使用 inline。固定 MRS 同时锁定同版文本及完整 classical 补充，不用纯域名列表代替完整分类。
- provider 在原位置按连续分片展开；嵌套 RULE-SET 展开为 OR 并集，使外层 NOT/AND 继续作用于完整来源。`no-resolve` 保留在每个需要的分片上。
- OpenAI 补充源的 AS20473 由显式清单展开成锁定 CIDR 快照；更新时必须同步来源和哈希。它反映快照时的 ASN 范围。
- DNS 的关键词/正则 classical 分片继续参与匹配。只允许官方内核固定的「classical provider 只匹配域名」提示，其余解析警告和错误会阻止构建。
- `fakeipfillter.txt` 的 DNS 通配模式原样追加，不按普通域名处理。`Torrent.list`、`tw.list` 仍只导出分组文件，不增加当前基准未定义的路由。
- 工具在临时目录完成检查后替换输出；非空构建目录必须带 `.rules-build` 标记。下载变化、缺失依赖、重复键、未知规则及无效引用均失败。

下载包先运行 `check`，再在副本中填写真实订阅。修改配置后哈希发生变化是预期行为，不修改 manifest 来伪造校验。BettBox 的配置导入还需检查应用级 DNS/TUN 及脚本覆写；`BettboxCore` 不等同于官方 MRS 编译 CLI。隔离内核加载不证明真实节点连通，也不代表已经导入或切换 BettBox。
